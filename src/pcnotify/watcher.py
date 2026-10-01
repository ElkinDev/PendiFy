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

pause() and resume() come from the page's thread and only set or clear an event; the loop honors it on its own
thread. While paused run() makes no step, so nothing reads the client's files or process, its port or the game's
port; the first paused turn forgets the client, the watch, the hold and the start latch, and the first turn after
a resume is a fresh connection, so a game already under way then alerts nothing. Each pause is counted, and the loop
rests for every count it has not honored, so a pause and a resume that both land inside one sleep or one step still
rest it once. A pause that lands inside a step posts no accept after the delay and starts no alert, and its started
line is not said. The pause lives in memory only: every start is active.

The log (lane pclog): a ring of the last LOG_LINES events, each (seq, at, kind, detail), noted where the console line
of the same moment is said, the ping's result through the Alerter's listen at the ping's own time; seq rises by one
from 1 and never repeats in a run. events() answers a copy for the page. It lives in memory only, as the pause does.
"""
import collections
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
LOG_LINES = 50  # the events the log keeps, the newest

CONNECTED_LINE = "connected to the game client"
LOST_LINE = "lost the game client, looking for it again"
ACCEPTING_LINE = "match found: accepting"
ACCEPTED_LINE = "match found: accepted"
DRY_LINE = "match found: not accepting (--dry)"
STEP_FAILED_LINE = "watcher: a step failed ({}), looking for the game client again"
LOADING_LINE = "loading screen: waiting for the match to start"
STARTED_LINE = "match started: alerting"
STARTED_ON_WAIT_LINE = "match started: the game gave no clock, alerting on the wait"
PAUSED_LINE = "paused: not reading the game client"
RESUMED_LINE = "resumed: looking for the game client again"

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
        self._paused = threading.Event()  # set and cleared by the page's thread, read by the loop
        self._pauses = 0  # every pause counted by the page's thread, under the lock; the loop compares it
        self._honored = 0  # the count of the last pause the loop has rested for; read and written by it only
        self._resting = False  # the loop's own record that it has acted on a pause; read and written by it only
        self._events = collections.deque(maxlen=LOG_LINES)  # the log, (seq, at, kind, detail); under the lock
        self._seq = 0  # the last event's seq, under the lock; never reset in a run of the program
        self._ran = False  # run() has noted the start; read and written by the loop only
        self._waiting_due = True  # the next turn with no client notes waiting: after the start and after a resume
        listen = getattr(alert, "listen", None)
        if callable(listen):  # an Alerter tells each ping's result; the tests' plain fakes have no listen
            listen(self._on_ping)

    def stop(self):
        self._stop.set()

    def pause(self):
        """Stop reading the game; callable from any thread, honored by the loop at its next turn. The event is set
        before the count moves, so a loop that reads the new count reads the event of this pause or of a later
        resume."""
        self._paused.set()
        with self._lock:
            self._pauses += 1

    def resume(self):
        """Read the game again from the next turn, as a fresh connection; callable from any thread."""
        self._paused.clear()

    @property
    def paused(self):
        return self._paused.is_set()

    def run(self):
        """The loop: a step, then its pause, until stopped. A step that breaks is said by its type and the
        client is looked for again, so the watcher never ends while the page says it runs."""
        while not self._stop.is_set():
            if not self._ran:
                self._ran = True
                self._note("started")
            with self._lock:
                pauses = self._pauses
            paused = self._paused.is_set()
            # A pause not yet honored rests the loop even when a resume has already cleared the event.
            if (paused or pauses != self._honored) and not self._resting:
                self._rest()
            self._honored = pauses
            if paused:  # no step: nothing is read while paused
                pause = STEP_PAUSE
            else:
                if self._resting:
                    self._resting = False
                    self._log(RESUMED_LINE)
                    self._note("resumed")
                    self._waiting_due = True
                try:
                    pause = self.step()
                except Exception as failure:
                    connected = self._found is not None  # the log says lost only of a client this turn had
                    self._log(STEP_FAILED_LINE.format(type(failure).__name__))
                    if connected:
                        self._note("lost")
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
                if self._waiting_due:  # once per waiting period; never right after lost, whose line says the search
                    self._waiting_due = False
                    self._note(WAITING)
                self._watch(None)  # the watch outlives a lost client
                return NO_CLIENT_PAUSE
            self._found = found
            self._set_client(CONNECTED)
            self._log(CONNECTED_LINE)
            self._note(CONNECTED)
            self._waiting_due = False
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
            self._note("lost")
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
                    "pingResult": ping, "pingAt": ping_at, "paused": self._paused.is_set()}

    def events(self):
        """The log: the last LOG_LINES events of this run, oldest first, as (seq, at, kind, detail) in a list the
        caller may keep or change."""
        with self._lock:
            return list(self._events)

    def _note(self, kind, detail=None, at=None):
        """One event in the log: the next seq, the wall time unless the event brings its own, the kind and its
        detail; under the lock events() reads with, since a ping is noted from the Alerter's thread."""
        at = self._wall() if at is None else at
        with self._lock:
            self._seq += 1
            self._events.append((self._seq, at, kind, detail))

    def _on_ping(self, name, at):
        """The Alerter's listener: a ping's result name and its own time."""
        self._note("ping", name, at)

    def _on_phase(self, phase, base, token):
        # An arrival is InProgress after a read of another phase. The first read is not one: an alert for a game
        # already under way says nothing (S:755-757). A first read of InProgress or of Reconnect is such a game, so
        # it sets the latch, and the InProgress that follows (after a Reconnect of that same game, or the one the
        # Reconnect ends in) says nothing either. Reconnect, InProgress or an unknown phase keeps the latch.
        # A boundary ends a watch with no alert; a first read of InProgress or Reconnect leaves a running one on.
        # The log notes a phase that differs from the last read, before what it leads to (the accept, the loading).
        if phase != self._last_phase:
            self._note("phase", phase)
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
        if self._paused.is_set():  # paused during the delay: no accept, and the next turn rests
            return
        status = self._post(base + client.ACCEPT_PATH, token, client.CLIENT_TIMEOUT)
        if status in (200, 204):
            self._log(ACCEPTED_LINE)
            self._note("accepted")
            self._fire(QUEUE_FOUND)  # S:790
            self._hold()

    def _loading(self):
        """The loading screen, said on the PC only; the watch for the true start begins, its first read due now.
        Nothing while paused."""
        if self._paused.is_set():
            return
        self._alert.sound()
        with self._lock:
            self._last_alert = (LOADING, self._wall())
        self._log(LOADING_LINE)
        self._note(LOADING)
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
                self._fire(MATCH_STARTED, STARTED_LINE)
                return
            if seconds is not None:
                self._watch_since = now
        if now >= self._watch_since + LIVE_FALLBACK_SECONDS:
            if phase == IN_PROGRESS:
                self._watch_since = None
                self._fire(MATCH_STARTED, STARTED_ON_WAIT_LINE)
            elif self._found is None:
                self._watch_since = None

    def _hold(self):
        self._hold_until = self._clock() + HOLD_SECONDS

    def _fire(self, kind, line=None):
        """The alert of `kind`, its console `line` said first when one is given; nothing, the line included,
        while paused."""
        if self._paused.is_set():  # an alert handed to the Alerter before the pause is delivered; none starts in it
            return
        if line is not None:
            self._log(line)
        if kind == MATCH_STARTED:  # noted on the kind, so a start fired with no line is still in the log
            self._note("match_started")
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

    def _rest(self):
        """The first paused turn: the client, the watch, the hold and the start latch are dropped, so the turn
        after a resume is a fresh connection."""
        self._resting = True
        self._forget()
        self._watch_since = self._next_live = None
        self._hold_until = None
        self._start_alerted = False
        self._log(PAUSED_LINE)
        self._note("paused")

    def _set_client(self, value):
        with self._lock:
            self._client_state = value
