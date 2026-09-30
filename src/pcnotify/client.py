"""The game client's side: where its credentials are (S:693-716) and its loopback calls (S:718-786).

The client writes its port and token to a lockfile while it runs. When no lockfile answers, one read of
the client process's command line through PowerShell takes the place of the script's psutil fallback, at
most once every 10 s. Every failure is no client, never an exception. Nothing here prints, logs or raises
the client's token, and Credentials hides it from repr.
"""
import base64
import http.client
import json
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
LIVE_CLOCK_PATH = "/liveclientdata/gamestats"
LIVE_CLOCK_URL = f"https://{CLIENT_HOST}:2999{LIVE_CLOCK_PATH}"  # S:112
CLOCK_TIMEOUT = 1.5  # S:721
GAME_TIME_THRESHOLD = 2.0  # S:113
_MAX_ANSWER_BYTES = 64 * 1024


class ClientUnreachable(Exception):
    """The client gave no answer. The message names the failure's type only."""


def real_addresses(port):
    """The client's base address and the live clock's address in a real run (S:743, S:112)."""
    return f"https://{CLIENT_HOST}:{port}", LIVE_CLOCK_URL


def loopback_addresses(port):
    """The test override's addresses: one fake on 127.0.0.1 answers both, over plain http."""
    return f"http://{CLIENT_HOST}:{port}", f"http://{CLIENT_HOST}:{port}{LIVE_CLOCK_PATH}"


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


def game_really_started(get_call, url):
    """True once the game clock runs past the threshold (S:718-726). The clock only answers while a game
    runs, so no answer, or an answer that is not a clock, is False and never an error."""
    try:
        _, raw = get_call(url, None, CLOCK_TIMEOUT)
        return float(json.loads(raw.decode("utf-8")).get("gameTime", 0)) > GAME_TIME_THRESHOLD
    except (ClientUnreachable, ValueError, TypeError, AttributeError):
        return False
