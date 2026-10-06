"""The update (OR-103): one GET to PyPI a minute after the start and every six hours after, and the newer version
installed with pip.

`auto` checks and installs in the background, and the page then offers «Reiniciar ahora»; `notify` checks only, and
the page offers «Actualizar», which installs and then restarts; `off` never checks. The check carries nothing of the
pair: one GET to pypi.org with the program's User-Agent. pip runs with python.exe beside this interpreter, since the
output of pythonw goes nowhere, with the arguments of the paste (install.ps1:184) and the version pinned, and never
with a console over a game; its output goes to update-pip.log in the config folder, never to pipes of this copy, and
main's stop waits for it a minute before the run file goes. The running copy is never touched: every module was
imported at its start, so the new
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
# The one early round after auto mode's first failed install of a version, for an index that lags PyPI's JSON.
RETRY_SECONDS = 10 * 60
INSTALL_SECONDS = 10 * 60
# How long main's stop waits for an install under way before the run file goes.
INSTALL_JOIN_SECONDS = 60
# pip's output, in the config folder: a file, never a pipe of this copy, which a quit or a logoff would close under a
# pip that is swapping the files, and pip's next line would then raise before its rollback.
PIP_LOG_NAME = "update-pip.log"
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


def install(version, log_path, run=subprocess.run, installed=installed_version, executable=None, in_venv=None):
    """`version` onto the disk: (True, None) when the disk already holds it, which skips pip, or when pip ends 0;
    (False, line) otherwise, the line pip's last one, read from `log_path` once pip has ended, or the name of what
    kept pip from running. pip's stdout and stderr go to `log_path`, truncated at each install, never to pipes of
    this process: a copy that stops while pip swaps the files leaves pip writing to a file."""
    if installed() == version:
        return True, None
    executable = sys.executable if executable is None else executable
    in_venv = sys.prefix != sys.base_prefix if in_venv is None else in_venv
    # pip's own version notice would be its last line; the variable keeps it out, the arguments stay the paste's.
    # Unbuffered, pip's stdout and stderr reach the one file in the order pip wrote them, so its last line is last.
    environment = {**os.environ, "PIP_DISABLE_PIP_VERSION_CHECK": "1", "PYTHONUNBUFFERED": "1"}
    try:
        with open(log_path, "wb") as output:
            done = run(pip_command(version, executable, in_venv), stdin=subprocess.DEVNULL, stdout=output,
                       stderr=output, timeout=INSTALL_SECONDS, creationflags=CREATE_NO_WINDOW | DETACHED_PROCESS,
                       env=environment)
        if done.returncode == 0:
            return True, None
        written = Path(log_path).read_bytes().decode("utf-8", "replace")
    except (OSError, subprocess.SubprocessError) as failure:
        return False, type(failure).__name__
    lines = [line.strip() for line in written.splitlines() if line.strip()]
    return False, lines[-1] if lines else f"pip ended {done.returncode}"


def _log(line):
    print(f" [update] {line}", file=sys.stderr, flush=True)


class Updater:
    """The rounds on their own daemon thread, never the watcher's nor the tick loop's, and the state the page reads.

    `check` answers PyPI's latest version, `install(version)` answers (done, line), pip itself with its output in
    update-pip.log of `folder` (the config folder) when None, `restart` stops this copy once the restart flag is set
    (main's quit), `clock(seconds)` waits before a round and answers True once the updater is closed (the close's
    own wait when None), and `log` takes a failure's one line (stderr when None)."""

    def __init__(self, current_version, mode, check=check, install=None, restart=None, clock=None, log=None,
                 folder=None):
        if mode not in MODES:
            raise ValueError("the update mode is not auto, notify or off")
        self.pip_log = None if folder is None else Path(folder) / PIP_LOG_NAME
        if install is None and self.pip_log is None:
            raise ValueError("pip's output needs the config folder: no update without it")
        self.current_version, self.mode = current_version, mode
        self._check, self._install, self._restart = check, self._pip if install is None else install, restart
        self._closed = threading.Event()
        self._wait = self._closed.wait if clock is None else clock
        self._log = _log if log is None else log
        self._lock = threading.Lock()
        self._state = (NONE, None, None)
        # The versions whose install failed once in a round of auto mode: each had its one early round.
        self._retried = set()
        # Set while no install runs: what wait() reads; cleared under the lock wherever the state turns installing.
        self._idle = threading.Event()
        self._idle.set()
        # The flag main reads after the run file's release: set, it starts a new copy.
        self.restart_requested = threading.Event()
        # Whether that copy opens its page, so it starts without --quiet: every restart asked today does.
        self.restart_opens_page = True
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
        """Ends the wait between rounds; an install under way runs on in its own process, and wait() is how main's
        stop waits for it."""
        self._closed.set()

    def wait(self, timeout=INSTALL_JOIN_SECONDS):
        """Main's stop, before the run file's release: an install under way waited for, up to `timeout` seconds, so
        a quit does not end this copy while pip swaps the files. True once none runs; False, said in one log line,
        when pip still runs at the bound and goes on alone, its output in its file."""
        if self._idle.wait(timeout):
            return True
        with self._lock:
            version = self._state[1]
        self._log(f"the install of {version} did not end in {timeout:g} seconds; pip goes on alone")
        return False

    def run(self):
        """One round a minute after the start, then one every six hours, until the close; after a round in which
        auto mode's install of a version failed for the first time, the next one comes ten minutes after, once for
        that version."""
        delay = FIRST_ROUND_SECONDS
        while not self._wait(delay):
            delay = RETRY_SECONDS if self.round() else ROUND_SECONDS

    def round(self):
        """One check; a newer version reads available, and auto mode installs it at once. A failed install is open
        too: when the check still names a newer version, auto mode installs it again and notify mode reads it
        available, so the page offers «Actualizar» again. A failure is one log line, and the next round runs; a check
        that fails or names nothing newer leaves the state as it is. Nothing is checked again once an install has
        started or ended ready. True when auto mode's install of that version failed here for the first time, the
        round run() brings ten minutes early; False otherwise."""
        if self.mode == OFF or not self._open():
            return False
        try:
            latest = self._check()
        except Exception as failure:  # the network, PyPI or its answer: silent on the page until the next round
            self._log(f"the check failed: {type(failure).__name__}")
            return False
        if not newer(latest, self.current_version):
            return False
        with self._lock:
            if self._state[0] not in (NONE, AVAILABLE, FAILED):
                return False
            self._state = (AVAILABLE, latest, None)
        if self.mode != AUTO or self._apply(latest):
            return False
        with self._lock:
            first = latest not in self._retried
            self._retried.add(latest)
        return first

    def request_install(self):
        """The page's «Actualizar» in notify mode, and its «Reintentar» after a failed install in either mode: the
        version installed on its own thread, then, in notify mode, the restart; in auto mode it reads ready and waits
        for «Reiniciar ahora». False, and nothing done, in any other mode or state."""
        with self._lock:
            state, version, _ = self._state
            if state != FAILED and (self.mode != NOTIFY or state != AVAILABLE):
                return False
            self._state = (INSTALLING, version, None)
            self._idle.clear()
        threading.Thread(target=self._install_then_restart if self.mode == NOTIFY else self._apply, args=(version,),
                         name="update-install", daemon=True).start()
        return True

    def restart(self, opens_page=True):
        """A request only: the flag main reads after the run file's release, then the stop of this copy. With
        `opens_page`, true for every restart asked today (the page's «Reiniciar ahora», the restart after
        «Actualizar», the icon's item), the new copy starts without --quiet, so its page opens; a restart that does not
        ask for it keeps the run's own --quiet."""
        self.restart_opens_page = opens_page
        self.restart_requested.set()
        if self._restart is not None:
            self._restart()

    def _open(self):
        with self._lock:
            return self._state[0] in (NONE, AVAILABLE, FAILED)

    def _pip(self, version):
        """pip itself, its output in update-pip.log of the config folder."""
        return install(version, self.pip_log)

    def _install_then_restart(self, version):
        if self._apply(version) and not self._closed.is_set():
            self.restart()

    def _apply(self, version):
        with self._lock:
            self._state = (INSTALLING, version, None)
            self._idle.clear()
        try:
            done, line = self._install(version)
        except Exception as failure:  # never a dead thread with the page reading installing for ever
            done, line = False, type(failure).__name__
        with self._lock:
            self._state = (READY, version, None) if done else (FAILED, version, line)
            self._idle.set()
        if not done:
            self._log(f"the install of {version} failed")
        return done
