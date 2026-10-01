"""The watcher of the game client: the loop of S:728-800 as a class.

Everything that touches the world is injected: the credentials reader, the address builder, the HTTP
getter and poster, the clock, the wall clock, the sleep, the random delay, the alert and the console.
Its `alert` is an Alerter: snapshot() reads its last_ping(). One step() is one turn of the script's loop
and answers the pause before the next; run() is the loop around it. The script's 15 s sleep after a ready
check (S:778, S:793) is a hold on the injected clock, so a stop never waits for it. The arrival of
InProgress after a read of another phase is the loading screen: it beeps on the PC only, once per game, and
starts a watch that asks the game's own loopback port its clock at most once a second. The clock above zero
is the match's true start: a beep and match_started. A game that never answers its clock is announced after
120 s of the watch with InProgress read; a read of a boundary phase, or 120 s with no client, ends the watch
with no alert. A reconnect is the same game. The console gets fixed lines only: no phase, no port, no clock,
no token.
"""
import json
import random
import threading
import time

from . import client, worker

IN_PROGRESS, READY_CHECK, RECONNECT = "InProgress", "ReadyCheck", "Reconnect"
# The phases that end or precede a game: only a read of one of them resets the start latch.
GAME_BOUNDARY_PHASES = frozenset({"None", "Lobby", "Matchmaking", READY_CHECK, "ChampSelect", "EndOfGame",
                                  "PreEndOfGame", "WaitingForStats"})
ACCEPT_DELAY = (1.0, 2.5)  # S:136
HOLD_SECONDS = 15.0  # S:778, S:793
STEP_PAUSE = 0.3  # S:799
NO_CLIENT_PAUSE = 3.0  # S:739
LIVE_POLL_SECONDS = 1.0  # the game's clock is asked at most once a second during the watch
LIVE_FALLBACK_SECONDS = 120.0  # restarted by each clock not above zero, the wait announces a game giving no clock
QUEUE_FOUND, MATCH_STARTED = worker.KINDS

CONNECTED_LINE = "connected to the game client"
LOST_LINE = "lost the game client, looking for it again"
ACCEPTING_LINE = "match found: accepting"
ACCEPTED_LINE = "match found: accepted"
DRY_LINE = "match found: not accepting (--dry)"
STEP_FAILED_LINE = "watcher: a step failed ({}), looking for the game client again"
LOADING_LINE = "loading screen: waiting for the match to start"
STARTED_LINE = "match started: alerting"
STARTED_ON_WAIT_LINE = "match started: the game gave no clock, alerting on the wait"

WAITING, CONNECTED = "waiting", "connected"
_ALERT_NAMES = {QUEUE_FOUND: "queue", MATCH_STARTED: "started"}
LOADING = "loading"  # the last alert's name at the loading screen, said on the PC only


def accept_delay():
    """A delay drawn inside the script's two bounds (S:780)."""
    return random.uniform(*ACCEPT_DELAY)


def _print(line):
    print(line, flush=True)


def _phase(raw):
    """The phase the client answered, a JSON string, or None for anything else."""
    try:
        phase = json.loads(raw.decode("utf-8"))
    except ValueError:  # a body that is not JSON; UnicodeDecodeError is a ValueError
        return None
    return phase if isinstance(phase, str) else None


class Watcher:
    def __init__(self, credentials, alert, *, accept=True, addresses=client.real_addresses, get=client.get,
                 post=client.post, clock=time.monotonic, wall=time.time, sleep=None, delay=accept_delay,
                 log=None, stop=None, live=client.real_live_address, game_clock=client.game_clock):
        self._credentials, self._alert, self._accept = credentials, alert, accept
        self._addresses, self._get, self._post = addresses, get, post
        self._live, self._game_clock = live, game_clock
        self._clock, self._wall, self._delay = clock, wall, delay
        self._stop = stop if stop is not None else threading.Event()
        self._sleep = sleep if sleep is not None else self._stop.wait
        self._log = log if log is not None else _print
        self._lock = threading.Lock()
        self._found = None
        self._last_phase = None
        self._start_alerted = False
        self._hold_until = None
        self._client_state = WAITING
        self._last_alert = None
        self._watch_since = None  # the injected clock when the loading screen opened; None with no watch on
        self._next_live = None  # the injected clock from which the game's clock may be asked again

    def stop(self):
        self._stop.set()

    def run(self):
        """The loop: a step, then its pause, until stopped. A step that breaks is said by its type and the
        client is looked for again, so the watcher never ends while the page says it runs."""
        while not self._stop.is_set():
            try:
                pause = self.step()
            except Exception as failure:
                self._log(STEP_FAILED_LINE.format(type(failure).__name__))
                self._forget()
                pause = NO_CLIENT_PAUSE
            if self._stop.is_set():
                break
            self._sleep(pause)

    def step(self):
        """One turn of S:733-799; the pause before the next turn."""
        if self._hold_until is not None:
            if self._clock() < self._hold_until:
                return STEP_PAUSE
            self._hold_until = None
        if self._found is None:
            found = self._credentials.read()
            if found is None:
                self._watch(None)  # the watch outlives a lost client
                return NO_CLIENT_PAUSE
            self._found = found
            self._set_client(CONNECTED)
            self._log(CONNECTED_LINE)
        base = self._addresses(self._found.port)
        token = self._found.token
        phase = None
        try:
            status, raw = self._get(base + client.PHASE_PATH, token, client.CLIENT_TIMEOUT)
            if status == 200:
                phase = _phase(raw)
                if phase is None:  # requests raises a RequestException on a body that is not JSON (S:750)
                    raise client.ClientUnreachable("ValueError")
                self._on_phase(phase, base, token)
        except client.ClientUnreachable:  # S:796-798
            self._log(LOST_LINE)
            self._forget()
            phase = None
        self._watch(phase)
        return STEP_PAUSE

    def snapshot(self):
        """The watcher's state in plain values: the client waiting or connected, the last phase read as the
        client names it (None before any), the last alert and when, and the last ping's result and when."""
        ping, ping_at = self._alert.last_ping()
        with self._lock:
            name, at = self._last_alert or (None, None)
            return {"client": self._client_state, "phase": self._last_phase, "alert": name, "at": at,
                    "pingResult": ping, "pingAt": ping_at}

    def _on_phase(self, phase, base, token):
        # An arrival is InProgress after a read of another phase. The first read is not one: an alert for a game
        # already under way says nothing (S:755-757). A first read of InProgress or of Reconnect is such a game, so
        # it sets the latch, and the InProgress that follows (after a Reconnect of that same game, or the one the
        # Reconnect ends in) says nothing either. Reconnect, InProgress or an unknown phase keeps the latch.
        # A boundary ends a watch with no alert; a first read of InProgress or Reconnect leaves a running one on.
        if phase in GAME_BOUNDARY_PHASES:
            self._start_alerted = False
            self._watch_since = None
        elif self._last_phase is None and phase in (IN_PROGRESS, RECONNECT):
            self._start_alerted = True
        elif self._last_phase not in (None, IN_PROGRESS) and not self._start_alerted:
            self._start_alerted = True  # before the alert: an alert that breaks is not made again for this game
            self._loading()
        with self._lock:
            self._last_phase = phase
        if phase == READY_CHECK:
            self._ready_check(base, token)

    def _ready_check(self, base, token):
        if not self._accept:  # the script's --dry (S:773-777)
            self._log(DRY_LINE)
            self._fire(QUEUE_FOUND)  # S:776
            self._hold()
            return
        wait = self._delay() if ACCEPT_DELAY[1] else 0
        self._log(ACCEPTING_LINE)
        if wait:
            self._sleep(wait)
        if self._stop.is_set():  # stopped during the delay: the script's Ctrl+C posts nothing either
            return
        status = self._post(base + client.ACCEPT_PATH, token, client.CLIENT_TIMEOUT)
        if status in (200, 204):
            self._log(ACCEPTED_LINE)
            self._fire(QUEUE_FOUND)  # S:790
            self._hold()

    def _loading(self):
        """The loading screen, said on the PC only; the watch for the true start begins, its first read due now."""
        self._alert.sound()
        with self._lock:
            self._last_alert = (LOADING, self._wall())
        self._log(LOADING_LINE)
        self._watch_since = self._next_live = self._clock()

    def _watch(self, phase):
        """One turn of the watch: the game's clock asked when due, its value above zero the start and any other
        number a restart of the wait; then the wait, which fires on a step whose phase read was InProgress and ends
        with no alert with no client."""
        if self._watch_since is None:
            return
        now = self._clock()
        if now >= self._next_live:
            self._next_live = now + LIVE_POLL_SECONDS
            seconds = self._game_clock(self._live())
            if seconds is not None and seconds > 0:
                self._watch_since = None
                self._log(STARTED_LINE)
                self._fire(MATCH_STARTED)
                return
            if seconds is not None:
                self._watch_since = now
        if now >= self._watch_since + LIVE_FALLBACK_SECONDS:
            if phase == IN_PROGRESS:
                self._watch_since = None
                self._log(STARTED_ON_WAIT_LINE)
                self._fire(MATCH_STARTED)
            elif self._found is None:
                self._watch_since = None

    def _hold(self):
        self._hold_until = self._clock() + HOLD_SECONDS

    def _fire(self, kind):
        with self._lock:
            self._last_alert = (_ALERT_NAMES[kind], self._wall())
        self._alert(kind)

    def _forget(self):
        # S:755-757 is per connection: a fresh client already InProgress at its first read alerts nothing.
        # A running watch is left on: the game's clock is proof by itself.
        self._found = None
        with self._lock:
            self._last_phase = None
        self._set_client(WAITING)

    def _set_client(self, value):
        with self._lock:
            self._client_state = value
