"""The pairing state of design P5, a plain object with an injected clock and an injected check.

Nothing here sleeps: a driver calls tick() now and then, the page calls page_seen() on every fetch and
ask() on its button. While there is no link id and the page is open, a check is made every 30 s, at most
20 in a series, then the series pauses until the button asks again. Every check, automatic or asked,
shares one window of two a minute, so three PCs behind one address fit the Worker's six (P5).
A Throttled answer is a wait and a Refused answer is not terminal: the polling goes on after both.
"""
import threading
import time
from collections import deque

from . import worker

CHECK_INTERVAL = 30.0
CHECK_CAP = 20
RATE_LIMIT = 2
RATE_WINDOW = 60.0
# The page script fetches the state every few seconds; a background tab may be throttled to once a minute.
PAGE_OPEN_WINDOW = 70.0
REFUSED_PINGS_FOR_RELINK = 3

WAITING, WAIT, OFFLINE, PAUSED, LINKED, REFUSED = "waiting", "wait", "offline", "paused", "linked", "refused"


class PairingState:
    def __init__(self, store, check, clock=time.monotonic):
        self._store, self._check, self._clock = store, check, clock
        self._lock = threading.Lock()
        pair = store.load()
        self._secret, self._linked = pair.secret, pair.link_id is not None
        self._generation = 0
        self._series = 0
        self._last_check = None
        self._recent = deque()
        self._last_answer = WAITING
        self._page_seen = None
        self._button_refused = False
        self._typed_refused = False
        self._refused_pings = 0
        self._relink_offered = False
        self._config_failed = False
        self._theme = store.read_theme()

    def page_seen(self):
        with self._lock:
            self._page_seen = self._clock()

    def config_failed(self):
        """A POST met a config file that could not be read or replaced; the page says so until a write succeeds."""
        with self._lock:
            self._config_failed = True

    def tick(self):
        """One automatic check when one is due; the check's result, or None when none was made."""
        with self._lock:
            now = self._clock()
            open_page = self._page_seen is not None and now - self._page_seen <= PAGE_OPEN_WINDOW
            due = self._last_check is None or now - self._last_check >= CHECK_INTERVAL
            if self._linked or self._series >= CHECK_CAP or not open_page or not due or not self._window_open(now):
                return None
            ticket = self._reserve(now)
        return self._run(ticket)

    def ask(self):
        """The page's button: a check at once, which starts a new series, unless the window is full."""
        with self._lock:
            now = self._clock()
            self._page_seen = now
            if self._linked:
                return False
            if not self._window_open(now):
                self._button_refused = True
                return False
            self._series = 0
            ticket = self._reserve(now)
        self._run(ticket)
        return True

    def record_ping(self, result):
        """Three pings refused in a row offer a relink; one sent resets the count."""
        with self._lock:
            if isinstance(result, worker.Refused):
                self._refused_pings += 1
                self._relink_offered = self._relink_offered or self._refused_pings >= REFUSED_PINGS_FOR_RELINK
            elif isinstance(result, worker.Sent):
                self._refused_pings, self._relink_offered = 0, False

    def accept_relink(self):
        """«Volver a enlazar», only while offered: the link id cleared, the SAME secret shown again."""
        with self._lock:
            if not self._relink_offered:
                return False
            self._reset(self._store.clear_link_id())
            return True

    def forget(self):
        """«Olvidar este PC»: a new secret, no link id."""
        with self._lock:
            self._reset(self._store.forget())

    def typed(self, link_id, secret):
        """The typed road (P6); a malformed pair is refused and changes nothing."""
        with self._lock:
            try:
                pair = self._store.set_typed(link_id, secret)
            except ValueError:
                self._typed_refused = True
                return False
            self._reset(pair)
            return True

    def theme(self):
        """The page's kept theme choice, light or dark, or None for the system's."""
        with self._lock:
            return self._theme

    def set_theme(self, choice):
        """The theme button's choice (light, dark or system) kept in the config file; anything else raises
        ValueError and changes nothing."""
        with self._lock:
            self._theme = self._store.set_theme(choice)
            self._config_failed = False

    def secret_for_display(self):
        """The secret while the QR is to be shown (no link id), else None."""
        with self._lock:
            return None if self._linked else self._secret

    def snapshot(self):
        """The state in plain values, never the secret or the link id."""
        with self._lock:
            if self._linked:
                state = REFUSED if self._relink_offered else LINKED
            elif self._series >= CHECK_CAP:
                state = PAUSED
            else:
                state = self._last_answer
            return {"state": state, "linked": self._linked, "showCode": not self._linked,
                    "relinkOffered": self._linked and self._relink_offered,
                    "buttonRefused": self._button_refused, "typedRefused": self._typed_refused,
                    "configFailed": self._config_failed}

    def _window_open(self, now):
        while self._recent and self._recent[0] <= now - RATE_WINDOW:
            self._recent.popleft()
        return len(self._recent) < RATE_LIMIT

    def _reserve(self, now):
        self._series += 1
        self._last_check = now
        self._recent.append(now)
        self._button_refused = False
        return self._generation, self._secret

    def _run(self, ticket):
        generation, secret = ticket
        result = self._check(secret)
        with self._lock:
            if generation != self._generation or self._linked:
                return result  # the pair changed while the check was out: its answer is not this pair's
            if isinstance(result, worker.Linked):
                self._secret = self._store.set_link_id(result.link_id).secret
                self._linked = True
                self._config_failed = False
            elif isinstance(result, worker.Throttled):
                self._last_answer = WAIT
            elif isinstance(result, worker.Refused):
                self._last_answer = WAITING
            else:
                self._last_answer = OFFLINE
        return result

    def _reset(self, pair):
        self._generation += 1
        self._secret, self._linked = pair.secret, pair.link_id is not None
        self._series, self._last_check, self._last_answer = 0, None, WAITING
        self._refused_pings, self._relink_offered = 0, False
        self._button_refused = self._typed_refused = self._config_failed = False
