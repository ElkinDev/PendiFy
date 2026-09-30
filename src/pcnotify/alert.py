"""An alert: the beep of S:161-169 and, with a stored link id, the ping of its kind off the watcher's thread.

Every alert beeps and names a kind (S:336-355). The ping's result goes to the pairing state through record_ping,
as lane lnk5a's ping command answers it, so three refusals in a row offer the relink, and its name and time stay
for the page as the last ping. With no link id nothing is sent and nothing is queued. The console gets fixed
lines only.
"""
import threading
import time

from . import worker

try:
    import winsound
except ImportError:  # not Windows: the beep is silent, as in the script
    winsound = None

BEEP_NOTES = (988, 1319)  # S:167
BEEP_ROUNDS = 3  # S:166
BEEP_MILLISECONDS = 160  # S:168
PING_LINES = {"sent": "alert: sent to the phone", "refused": "alert: refused, link this PC again from the page",
              "not_delivered": "alert: not delivered", "failed": "alert: failed"}
CONFIG_UNREADABLE_LINE = "alert: the config file could not be read, nothing was sent"
BEEP_FAILED_LINE = "alert: the sound device refused the beep"


def _print(line):
    print(line, flush=True)


def _start_daemon(target):
    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    return thread


def beep(sound=winsound, start=_start_daemon):
    """The script's siren, three rounds of two notes, on a thread of its own; None without the module."""
    if sound is None:
        return None

    def play():
        try:
            for _ in range(BEEP_ROUNDS):
                for frequency in BEEP_NOTES:
                    sound.Beep(frequency, BEEP_MILLISECONDS)
        except RuntimeError:  # winsound's answer when the system cannot play it
            _print(BEEP_FAILED_LINE)

    return start(play)


def _result_name(result):
    if isinstance(result, worker.Sent):
        return "sent"
    if isinstance(result, worker.Refused):
        return "refused"
    if isinstance(result, worker.NotDelivered):
        return "not_delivered"
    return "failed"


class Alerter:
    def __init__(self, store, state, ping, *, beep=beep, start=None, log=None, wall=time.time):
        self._store, self._state, self._ping, self._beep = store, state, ping, beep
        self._start = start or _start_daemon
        self._log = log or _print
        self._wall = wall
        self._lock = threading.Lock()
        self._pending = []
        self._last_ping = (None, None)

    def __call__(self, kind):
        """Beeps; with a stored link id, sends the ping of `kind` on a thread of its own."""
        self._beep()
        try:
            pair = self._store.read()  # read at each alert: a link made on the page counts at once
        except OSError:
            self._log(CONFIG_UNREADABLE_LINE)
            return
        if pair is None or pair.link_id is None:
            return
        done = threading.Event()
        with self._lock:
            self._pending = [event for event in self._pending if not event.is_set()] + [done]
        self._start(lambda: self._send(pair, kind, done))

    def _send(self, pair, kind, done):
        try:
            try:
                result = self._ping(pair.link_id, pair.secret, kind)
            except Exception as failure:  # a refused input raises a fixed sentence; it still ends as failed
                result = worker.Failed(type(failure).__name__)
            self._state.record_ping(result)
            name = _result_name(result)
            with self._lock:
                self._last_ping = (name, self._wall())
            self._log(PING_LINES[name])
        finally:
            done.set()

    def last_ping(self):
        """The last ping's result name (sent, refused, not_delivered or failed) and its wall time; (None, None)
        before any ping was made."""
        with self._lock:
            return self._last_ping

    def flush(self, timeout):
        """Waits up to `timeout` seconds for the pings in flight; True when none is left."""
        deadline = time.monotonic() + timeout
        with self._lock:
            pending = list(self._pending)
        return all(event.wait(max(0.0, deadline - time.monotonic())) for event in pending)
