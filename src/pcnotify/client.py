"""The game client's side: where its credentials are (S:693-716).

The client writes its port and token to a lockfile while it runs. When no lockfile answers, one read of
the client process's command line through PowerShell takes the place of the script's psutil fallback, at
most once every 10 s. Every failure is no client, never an exception. Nothing here prints, logs or raises
the client's token, and Credentials hides it from repr.
"""
import re
import subprocess
import time
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
