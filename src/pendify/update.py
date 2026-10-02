"""The update (OR-103): one GET to PyPI a minute after the start and every six hours after, and the newer version
installed with pip.

`auto` checks and installs in the background, and the page then offers «Reiniciar ahora»; `notify` checks only, and
the page offers «Actualizar», which installs and then restarts; `off` never checks. The check carries nothing of the
pair: one GET to pypi.org with the program's User-Agent. pip runs with python.exe beside this interpreter, since the
output of pythonw goes nowhere, with the arguments of the paste (install.ps1:184) and the version pinned, and never
with a console over a game. The running copy is never touched: every module was imported at its start, so the new
files take effect at the next start, the one «Reiniciar ahora» asks for.
"""
import importlib.metadata
import json
import os
import re
import subprocess
import sys
import threading
import urllib.request
from pathlib import Path

from . import config, worker

DISTRIBUTION = "pendify"
PYPI_URL = f"https://pypi.org/pypi/{DISTRIBUTION}/json"
MODES = config.UPDATE_MODES
AUTO, NOTIFY, OFF = MODES
FIRST_ROUND_SECONDS = 60
ROUND_SECONDS = 6 * 60 * 60
INSTALL_SECONDS = 10 * 60
# What the page reads: the state, with the version it is about.
NONE, AVAILABLE, INSTALLING, READY, FAILED = "none", "available", "installing", "ready", "failed"
# No console over a game: the flags of the process pip runs in.
CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008
_MAX_ANSWER_BYTES = 4 * 1024 * 1024
_VERSION = re.compile(r"([0-9]+)\.([0-9]+)\.([0-9]+)")


def _parts(version):
    """X.Y.Z as a tuple of ints; None for anything else."""
    match = _VERSION.fullmatch(version) if isinstance(version, str) else None
    return tuple(int(part) for part in match.groups()) if match else None


def newer(remote, local):
    """True only when both are X.Y.Z and `remote` is the higher; anything else, on either side, is not newer."""
    remote_parts, local_parts = _parts(remote), _parts(local)
    return remote_parts is not None and local_parts is not None and remote_parts > local_parts


def installed_version():
    """The version the disk holds now, read fresh from the package's metadata; None when it cannot be read."""
    try:
        version = importlib.metadata.version(DISTRIBUTION)
    except Exception:  # not installed (a checkout) or a half-written dist-info: no version
        return None
    return version.strip() if isinstance(version, str) and version.strip() else None


# The version this copy runs: read once at import, at the start, before any install can change the disk.
RUNNING_VERSION = installed_version()


def check(urlopen=urllib.request.urlopen):
    """PyPI's latest version of the package, as its JSON names it in info.version; raises on any failure."""
    request = urllib.request.Request(PYPI_URL, headers={"User-Agent": worker.USER_AGENT})
    with urlopen(request, timeout=worker.TIMEOUT_SECONDS) as answer:
        data = json.loads(answer.read(_MAX_ANSWER_BYTES).decode("utf-8"))
    return data["info"]["version"]


def pip_command(version, executable, in_venv):
    """The paste's pip arguments (install.ps1:184) with the version pinned, run by python.exe beside `executable`;
    without --user in a venv, where pip refuses it."""
    user = [] if in_venv else ["--user"]
    return [str(Path(executable).with_name("python.exe")), "-m", "pip", "install", *user, "--upgrade",
            "--force-reinstall", "--no-deps", "--no-warn-script-location", f"{DISTRIBUTION}=={version}"]


def install(version, run=subprocess.run, installed=installed_version, executable=None, in_venv=None):
    """`version` onto the disk: (True, None) when the disk already holds it, which skips pip, or when pip ends 0;
    (False, line) otherwise, the line pip's last one on stderr, or the name of what kept pip from running."""
    if installed() == version:
        return True, None
    executable = sys.executable if executable is None else executable
    in_venv = sys.prefix != sys.base_prefix if in_venv is None else in_venv
    # pip's own version notice would be its last stderr line; the variable keeps it out, the arguments stay the paste's.
    environment = {**os.environ, "PIP_DISABLE_PIP_VERSION_CHECK": "1"}
    try:
        done = run(pip_command(version, executable, in_venv), stdin=subprocess.DEVNULL, capture_output=True,
                   encoding="utf-8", errors="replace", timeout=INSTALL_SECONDS,
                   creationflags=CREATE_NO_WINDOW | DETACHED_PROCESS, env=environment)
    except (OSError, subprocess.SubprocessError) as failure:
        return False, type(failure).__name__
    if done.returncode == 0:
        return True, None
    lines = [line.strip() for line in (done.stderr or "").splitlines() if line.strip()]
    return False, lines[-1] if lines else f"pip ended {done.returncode}"


def _log(line):
    print(f" [update] {line}", file=sys.stderr, flush=True)


class Updater:
    """The rounds on their own daemon thread, never the watcher's nor the tick loop's, and the state the page reads.

    `check` answers PyPI's latest version, `install(version)` answers (done, line), `restart` stops this copy once
    the restart flag is set (main's quit), `clock(seconds)` waits before a round and answers True once the updater
    is closed (the close's own wait when None), and `log` takes a failure's one line (stderr when None)."""

    def __init__(self, current_version, mode, check=check, install=install, restart=None, clock=None, log=None):
        if mode not in MODES:
            raise ValueError("the update mode is not auto, notify or off")
        self.current_version, self.mode = current_version, mode
        self._check, self._install, self._restart = check, install, restart
        self._closed = threading.Event()
        self._wait = self._closed.wait if clock is None else clock
        self._log = _log if log is None else log
        self._lock = threading.Lock()
        self._state = (NONE, None, None)
        # The flag main reads after the run file's release: set, it starts a new copy.
        self.restart_requested = threading.Event()
        self.thread = None

    def snapshot(self):
        """What the page reads: the state, the version it is about and, for a failed install, pip's last line."""
        with self._lock:
            state, version, error = self._state
        return {"state": state, "version": version, "error": error}

    def start(self):
        """The rounds on their own daemon thread; none in off mode."""
        if self.mode == OFF or self.thread is not None:
            return
        self.thread = threading.Thread(target=self.run, name="update", daemon=True)
        self.thread.start()

    def close(self):
        """Ends the wait between rounds; an install under way runs to its end in its own process."""
        self._closed.set()

    def run(self):
        """One round a minute after the start, then one every six hours, until the close."""
        delay = FIRST_ROUND_SECONDS
        while not self._wait(delay):
            self.round()
            delay = ROUND_SECONDS

    def round(self):
        """One check; a newer version reads available, and auto mode installs it at once. A failure is one log
        line, and the next round runs. Nothing is checked again once an install has started, ended ready or failed:
        a failed version is tried again at the next start only."""
        if self.mode == OFF or not self._open():
            return
        try:
            latest = self._check()
        except Exception as failure:  # the network, PyPI or its answer: silent on the page until the next round
            self._log(f"the check failed: {type(failure).__name__}")
            return
        if not newer(latest, self.current_version):
            return
        with self._lock:
            if self._state[0] not in (NONE, AVAILABLE):
                return
            self._state = (AVAILABLE, latest, None)
        if self.mode == AUTO:
            self._apply(latest)

    def request_install(self):
        """The page's «Actualizar» in notify mode: the version seen installed on its own thread, then the restart.
        False, and nothing done, in any other mode or state."""
        with self._lock:
            state, version, _ = self._state
            if self.mode != NOTIFY or state != AVAILABLE:
                return False
            self._state = (INSTALLING, version, None)
        threading.Thread(target=self._install_then_restart, args=(version,), name="update-install",
                         daemon=True).start()
        return True

    def restart(self):
        """A request only: the flag main reads after the run file's release, then the stop of this copy."""
        self.restart_requested.set()
        if self._restart is not None:
            self._restart()

    def _open(self):
        with self._lock:
            return self._state[0] in (NONE, AVAILABLE)

    def _install_then_restart(self, version):
        if self._apply(version):
            self.restart()

    def _apply(self, version):
        with self._lock:
            self._state = (INSTALLING, version, None)
        try:
            done, line = self._install(version)
        except Exception as failure:  # never a dead thread with the page reading installing for ever
            done, line = False, type(failure).__name__
        with self._lock:
            self._state = (READY, version, None) if done else (FAILED, version, line)
        if not done:
            self._log(f"the install of {version} failed")
        return done
