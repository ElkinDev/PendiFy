"""The standalone Teamfight Tactics app on macOS: the game's own log read as the client's phase.

The app serves no loopback API, so nothing can accept a match for its player: the watcher alerts the queue pop and
the player accepts it in the game. Its log, ~/Library/Logs/TFT/TFT.log, says each step in a line of its own:

    Entering LobbyState[ETFTLobbyState::Matchmaking]      in the queue                Matchmaking
    Match found, showing AFK Check Modal                  the queue popped            ReadyCheck
    Accepted match                                        accepted, waiting           Matchmaking
    LogNet: Welcomed by server                            the match's loading screen  InProgress
    Entering LobbyState[ETFTLobbyState::InGame]           in the match                InProgress
    Entering LobbyState[ETFTLobbyState::EndOfGame...]     the match ended             EndOfGame
    Entering LobbyState[ETFTLobbyState::<other>]          the app's other screens     Lobby

The phases are the League client's own names, so the watcher treats both the same. The log is read on from where
the last read stopped, in binary, and only the last of those lines counts; nothing of it is kept, logged or sent. A
ReadyCheck is over READY_CHECK_SECONDS after its stamp, as the game's own check is, so an old pop never alerts. The
app's process is looked for at most once every PROCESS_CHECK_SECONDS; with the app closed the session is lost.
"""
import os
import re
import subprocess
import time

from . import client

PROCESS_PATTERN = "Teamfight Tactics.app/Contents/MacOS/TFTClient"
PROCESS_CHECK_SECONDS = 3.0
READY_CHECK_SECONDS = 15.0
CHUNK = 1024 * 1024
MAX_BYTES_PER_READ = 32 * 1024 * 1024

LOBBY_MARK = b"Entering LobbyState[ETFTLobbyState::"
POP_MARK = b"Match found, showing AFK Check Modal"
MARKS = ((LOBBY_MARK, None),  # the phase is the state's name
         (POP_MARK, "ReadyCheck"),
         (b"Accepted match", "Matchmaking"),
         (b"Matchmaking Update[MATCHMAKING]", "Matchmaking"),
         (b"LogNet: Welcomed by server", "InProgress"))
_STATE_NAME = re.compile(rb"\w+")


def lobby_phase(name):
    """The client's phase for one of the app's lobby states."""
    if name == "Matchmaking":
        return "Matchmaking"
    if name == "InGame":
        return "InProgress"
    if name.startswith("EndOfGame"):
        return "EndOfGame"
    return "Lobby"


def last_mark(lines):
    """(phase, stamp) of the last marked line in `lines`, whole lines; None when no line is marked. The stamp is
    the line's epoch, None when it does not parse."""
    best = None
    for mark, phase in MARKS:
        at = lines.rfind(mark)
        if at >= 0 and (best is None or at > best[0]):
            if phase is None:
                name = _STATE_NAME.match(lines, at + len(mark))
                phase = lobby_phase(name.group().decode("ascii") if name else "")
            best = (at, phase)
    if best is None:
        return None
    at, phase = best
    start = lines.rfind(b"\n", 0, at) + 1
    end = lines.find(b"\n", at)
    return phase, client._log_stamp(lines[start:end if end >= 0 else len(lines)])


def last_pop(lines):
    """The stamp of the last queue pop line in `lines`, None with none or one whose stamp does not parse."""
    at = lines.rfind(POP_MARK)
    if at < 0:
        return None
    start = lines.rfind(b"\n", 0, at) + 1
    end = lines.find(b"\n", at)
    return client._log_stamp(lines[start:end if end >= 0 else len(lines)])


def app_running(run=subprocess.run):
    """True while the app's process runs."""
    try:
        done = run(["/usr/bin/pgrep", "-f", PROCESS_PATTERN], stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL, timeout=client.PROCESS_READ_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0


class AppSession:
    """The app as the watcher's client: phase() reads it, and it cannot accept (`accepts` False). `port` and `token`
    are there for the watcher's address, which nothing calls."""

    accepts = False
    port = 0
    token = None

    def __init__(self, path=client.real_game_log, running=app_running, clock=time.monotonic, wall=time.time,
                 opener=open):
        self._path, self._running, self._clock, self._wall, self._opener = path, running, clock, wall, opener
        self._offset, self._file_id = 0, None
        self._phase, self._stamp = None, None
        self._pop = None  # the stamp of a queue pop read and not said yet: said once even when accepted since
        self._checked_at, self._alive = None, False

    def running(self):
        """The app's process, looked for at most once every PROCESS_CHECK_SECONDS."""
        now = self._clock()
        if self._checked_at is None or now - self._checked_at >= PROCESS_CHECK_SECONDS:
            self._checked_at, self._alive = now, self._running()
        return self._alive

    def phase(self):
        """The app's phase now, Lobby when its log has said none yet; ClientUnreachable once the app is closed."""
        if not self.running():
            self._checked_at = None  # looked for again on the next read
            raise client.ClientUnreachable("closed")
        self._read_on()
        pop, self._pop = self._pop, None
        if pop is not None and self._wall() - pop <= READY_CHECK_SECONDS:
            return "ReadyCheck"  # a pop accepted between two reads is still a pop
        phase = self._phase or "Lobby"
        if phase == "ReadyCheck" and (self._stamp is None or self._wall() - self._stamp > READY_CHECK_SECONDS):
            return "Matchmaking"
        return phase

    def _read_on(self):
        path = self._path()
        if path is None:
            return
        try:
            with self._opener(path, "rb") as handle:
                status = os.fstat(handle.fileno())
                file_id = (status.st_dev, status.st_ino)
                if file_id != self._file_id or status.st_size < self._offset:  # a new log: the app started again
                    self._file_id, self._offset, self._phase, self._stamp, self._pop = file_id, 0, None, None, None
                handle.seek(self._offset)
                budget, partial = MAX_BYTES_PER_READ, b""
                while budget > 0:
                    chunk = handle.read(min(CHUNK, budget))
                    if not chunk:
                        break
                    budget -= len(chunk)
                    partial += chunk
                    end = partial.rfind(b"\n") + 1
                    if end:
                        lines = partial[:end]
                        found = last_mark(lines)
                        if found is not None:
                            self._phase, self._stamp = found
                        pop = last_pop(lines)
                        if pop is not None:
                            self._pop = pop
                        self._offset += end
                        partial = partial[end:]
        except OSError:  # no log yet, or one that cannot be read: the phase stays as it was
            return


class EitherClient:
    """The League client first, as ever; with none, the standalone TFT app while it runs."""

    def __init__(self, league, app):
        self._league, self._app = league, app

    def read(self):
        found = self._league.read()
        if found is not None:
            return found
        return self._app if self._app.running() else None
