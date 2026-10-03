"""The game client's side: where its credentials are (S:693-716) and its loopback calls (S:718-786).

The client writes its port and token to a lockfile while it runs. When no lockfile answers, one read of
the client process's command line through PowerShell takes the place of the script's psutil fallback, at
most once every 10 s. Every failure is no client, never an exception. Nothing here prints, logs or raises
the client's token, and Credentials hides it from repr. The client's phase says the loading screen opened;
the game's own loopback port is asked one number, its clock, to tell when the match itself starts. Nothing
else of the live data is read, kept, printed or sent.
"""
import base64
import calendar
import http.client
import json
import os
import re
import ssl
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

# S:139-140: the two Windows places of the client's lockfile, the one list of them.
LOCKFILE_PATHS = (r"C:\Riot Games\League of Legends\lockfile", r"D:\Riot Games\League of Legends\lockfile")
# S:705: the process whose command line carries the port and the token, matched as a part of its name.
PROCESS_NAME = "LeagueClient"
# S:707-708, the script's two expressions.
PORT_PATTERN = re.compile(r"--app-port=(\d+)")
TOKEN_PATTERN = re.compile(r"--remoting-auth-token=([\w-]+)")
# Written by [Console]::Out: the host's own output wraps a long line at the console width, which would
# cut the token in two.
PROCESS_QUERY = ("powershell", "-NoProfile", "-Command",
                 f"Get-CimInstance -ClassName Win32_Process -Filter 'Name LIKE ''%{PROCESS_NAME}%''' "
                 "| ForEach-Object { [Console]::Out.WriteLine($_.CommandLine) }")
PROCESS_READ_INTERVAL = 10.0
PROCESS_READ_TIMEOUT = 5


@dataclass(frozen=True)
class Credentials:
    """The client's port and its token; the token is never shown by repr."""

    port: int
    token: str = field(repr=False)


def _credentials(port, token):
    """Credentials when the port is a TCP port and the token is not empty, else None."""
    if not port.isdigit() or not 0 < int(port) < 65536 or not token:
        return None
    return Credentials(int(port), token)


def read_lockfile(path):
    """The third and fourth fields of the lockfile (S:697-700); a missing, short or unreadable file is None."""
    try:
        with open(path, "rb") as handle:
            data = handle.read().decode("utf-8").split(":")
    except (OSError, UnicodeDecodeError):
        return None
    if len(data) < 4:
        return None
    return _credentials(data[2].strip(), data[3].strip())


def parse_command_lines(text):
    """The first command line that carries both the port and the token (S:704-709), or None."""
    for line in text.splitlines():
        port, token = PORT_PATTERN.search(line), TOKEN_PATTERN.search(line)
        if port and token:
            return _credentials(port.group(1), token.group(1))
    return None


def query_process(run):
    """The client process's command lines, or None on a timeout, a nonzero exit or a missing shell."""
    try:
        done = run(list(PROCESS_QUERY), capture_output=True, text=True, errors="replace",
                   timeout=PROCESS_READ_TIMEOUT, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 and isinstance(done.stdout, str) else None


class ClientCredentials:
    """The lockfiles first; the process read only when none answers and at most once every 10 s.
    `run` None turns the process read off (the test override reads its one lockfile only)."""

    def __init__(self, paths=LOCKFILE_PATHS, run=subprocess.run, clock=time.monotonic):
        self._paths, self._run, self._clock = tuple(paths), run, clock
        self._last_query = None

    def read(self):
        for path in self._paths:
            found = read_lockfile(path)
            if found is not None:
                return found
        if self._run is None:
            return None
        now = self._clock()
        if self._last_query is not None and now - self._last_query < PROCESS_READ_INTERVAL:
            return None
        self._last_query = now
        output = query_process(self._run)
        return None if output is None else parse_command_lines(output)



# S:742: the client's user name beside its token, in basic authentication.
USER_NAME = "riot"
CLIENT_HOST = "127.0.0.1"
PHASE_PATH = "/lol-gameflow/v1/gameflow-phase"  # S:745
ACCEPT_PATH = "/lol-matchmaking/v1/ready-check/accept"  # S:784
CLIENT_TIMEOUT = 2.0  # S:746, S:785
_MAX_ANSWER_BYTES = 64 * 1024
# The game's own loopback port while a match runs, and the one route of it that is read: its clock.
LIVE_PORT = 2999
LIVE_CLOCK_PATH = "/liveclientdata/gamestats"
LIVE_TIMEOUT = 1.0


class ClientUnreachable(Exception):
    """The client gave no answer. The message names the failure's type only."""


def real_addresses(port):
    """The client's base address in a real run (S:743)."""
    return f"https://{CLIENT_HOST}:{port}"


def loopback_addresses(port):
    """The test override's base address: a fake on 127.0.0.1, over plain http."""
    return f"http://{CLIENT_HOST}:{port}"


def real_live_address():
    """The game's own loopback port in a real run."""
    return f"https://{CLIENT_HOST}:{LIVE_PORT}"


def loopback_tls_context(host):
    """The one TLS context in the package that does not verify, for the client's self-signed certificate.
    Only the literal 127.0.0.1 is served; any other host, a name that resolves there included, is refused."""
    if host != CLIENT_HOST:
        raise ValueError("an unverified TLS context is only for 127.0.0.1")
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect answer is returned as it is, never followed off the loopback address."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _call(url, token, timeout, data=None):
    """(status, body) of one call on 127.0.0.1; ClientUnreachable when no answer came."""
    parts = urllib.parse.urlsplit(url)
    if parts.hostname != CLIENT_HOST or parts.scheme not in ("http", "https"):
        raise ValueError("the client is only called on 127.0.0.1")
    handlers = [urllib.request.ProxyHandler({}), _NoRedirect()]
    if parts.scheme == "https":
        handlers.append(urllib.request.HTTPSHandler(context=loopback_tls_context(parts.hostname)))
    headers = {"Accept": "application/json"}
    if token is not None:
        try:
            pair = f"{USER_NAME}:{token}".encode("latin-1")  # as requests encodes basic authentication
        except UnicodeEncodeError:
            raise ClientUnreachable("UnicodeEncodeError") from None
        headers["Authorization"] = "Basic " + base64.b64encode(pair).decode("ascii")
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method="GET" if data is None else "POST")
    try:
        try:
            answer = urllib.request.build_opener(*handlers).open(request, timeout=timeout)
        except urllib.error.HTTPError as refused:  # an answer all the same
            answer = refused
        with answer:
            return answer.getcode(), answer.read(_MAX_ANSWER_BYTES)
    except urllib.error.URLError as failure:
        reason = failure.reason
        name = type(reason).__name__ if isinstance(reason, BaseException) else type(failure).__name__
        raise ClientUnreachable(name) from None
    except (OSError, http.client.HTTPException, ValueError) as failure:  # a reset, a timeout, a broken answer
        raise ClientUnreachable(type(failure).__name__) from None


def get(url, token, timeout):
    """GET on the client: (status, body), or ClientUnreachable. `token` None sends no authentication."""
    return _call(url, token, timeout)


def post(url, token, timeout):
    """POST of an empty JSON object on the client, as S:784-786: the status, or ClientUnreachable."""
    return _call(url, token, timeout, data=b"{}")[0]


def game_clock(base, get=get):
    """The game's clock in seconds, from one GET of its clock route with no authentication; None when the
    port gave no answer, the status is not 200, the body is not a JSON object or its gameTime is not a
    number (a bool is not one). Never raises; no other key is read and nothing is kept."""
    try:
        status, raw = get(base + LIVE_CLOCK_PATH, None, LIVE_TIMEOUT)
    except ClientUnreachable:
        return None
    if status != 200:
        return None
    try:
        answer = json.loads(raw.decode("utf-8"))
    except (ValueError, RecursionError):  # not JSON, not UTF-8, or nested past the parser's depth
        return None
    if not isinstance(answer, dict):
        return None
    seconds = answer.get("gameTime")
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
        return None
    try:
        return float(seconds)
    except OverflowError:  # an int no float holds
        return None


# The game's own log, for a game that serves no clock: where it is under LOCALAPPDATA, the mark of the line it
# writes when it joins the match, the most one call reads and the size of each read.
GAME_LOG_PARTS = ("TFT", "Saved", "Logs", "TFT.log")
GAME_LOG_MARK = b"LogNet: Welcomed by server"
GAME_LOG_MAX_BYTES = 16 * 1024 * 1024
GAME_LOG_CHUNK = 256 * 1024
_GAME_LOG_STAMP = "%Y.%m.%d-%H.%M.%S"  # then :mmm, in UTC


def real_game_log(environ=os.environ):
    """The game's own log in a real run, <LOCALAPPDATA>/TFT/Saved/Logs/TFT.log; None when LOCALAPPDATA is unset or
    blank."""
    base = environ.get("LOCALAPPDATA")
    if base is None or not base.strip():
        return None
    return os.path.join(base, *GAME_LOG_PARTS)


def _log_stamp(line):
    """The epoch of the stamp between a log line's first [ and the ] after it, YYYY.MM.DD-HH.MM.SS:mmm in UTC,
    with its milliseconds; None when it does not parse."""
    start = line.find(b"[")
    end = line.find(b"]", start + 1) if start >= 0 else -1
    if end < 0:
        return None
    try:
        moment, millis = line[start + 1:end].decode("ascii").split(":")
        if len(millis) != 3 or not millis.isdigit():
            return None
        return calendar.timegm(time.strptime(moment, _GAME_LOG_STAMP)) + int(millis) / 1000
    except ValueError:  # not ASCII, not one colon, or not the stamp's shape; UnicodeDecodeError is a ValueError
        return None


def _holds_welcome(lines, since):
    """True when one of `lines`, whole lines each ending in a line feed, is a welcome stamped at or after `since`.
    A welcome older than `since`, or whose stamp does not parse, is skipped and the search goes on."""
    at = lines.find(GAME_LOG_MARK)
    while at >= 0:
        start = lines.rfind(b"\n", 0, at) + 1
        stamp = _log_stamp(lines[start:lines.find(b"\n", at)])
        if stamp is not None and stamp >= since:
            return True
        at = lines.find(GAME_LOG_MARK, at + len(GAME_LOG_MARK))
    return False


class GameLogStart:
    """Whether the game's own log shows it joined the match: a welcome line stamped at or after a time.

    The log is read in binary and on from where the last call stopped: the first call from byte 0, each later one
    from the end of the last whole line read, at most GAME_LOG_MAX_BYTES a call in GAME_LOG_CHUNK reads; a partial
    last line is read again next time, and a file shorter than the offset, a new one, is read from its start. Only
    the stamp of a welcome line is parsed. No line or part of one is kept, returned, logged or printed: between
    calls the object keeps the offset and nothing of the file. Every failure answers False, never an exception.
    `path` answers the log's path or None; `opener` opens it as open() does."""

    def __init__(self, path=real_game_log, opener=open):
        self._path, self._opener = path, opener
        self._offset = 0

    def reset(self):
        """Forget the offset: the next call reads the file from its start."""
        self._offset = 0

    def __call__(self, since):
        """True when the log holds a welcome stamped at or after `since`, in epoch seconds; else False."""
        path = self._path()
        if path is None:
            return False
        try:
            with self._opener(path, "rb") as handle:
                if handle.seek(0, os.SEEK_END) < self._offset:
                    self._offset = 0
                handle.seek(self._offset)
                return self._read_on(handle, since)
        except OSError:  # no file, a folder, a refused open or a read that broke
            return False

    def _read_on(self, handle, since):
        budget, partial = GAME_LOG_MAX_BYTES, b""
        while budget > 0:
            chunk = handle.read(min(GAME_LOG_CHUNK, budget))
            if not chunk:
                return False
            budget -= len(chunk)
            partial += chunk
            end = partial.rfind(b"\n") + 1  # the whole lines read so far end there
            if end:
                lines, partial = partial[:end], partial[end:]
                self._offset += end
                if _holds_welcome(lines, since):
                    return True
        return False
