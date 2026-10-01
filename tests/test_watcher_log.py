"""WatcherLogTest: the watcher keeps its activity as events for the page's log (lane pclog round 1).

A bounded ring of the last LOG_LINES events, each (seq, at, kind, detail), noted where the console line of the same
moment is said; the ping's result comes through the Alerter's listen, with the ping's own time. The world is scripted
turn by turn through run(): the credentials, the phase route, the accept and the game's clock are injected functions
and the sleep only moves the clocks, so nothing reads a socket and nothing waits.
"""
import json
import threading
import types
import unittest

import support
from test_watcher import PING_WALL, TOKEN, WALL, WatcherFixture

alert = support.module("alert")
client = support.module("client")
watcher = support.module("watcher")
worker = support.module("worker")

QUEUE_FOUND = worker.KINDS[0]
SENT_LINE = alert.PING_LINES["sent"]
TURN_WALL = 10.0  # the watcher's wall clock moves this far per turn, so each event's time names its turn
FAILED_LINE = watcher.STEP_FAILED_LINE.format("RuntimeError")


class Listening:
    """An alert that only listens: tell(name, at) is the call the Alerter's ping thread makes."""

    def listen(self, callback):
        self.tell = callback


def at(turn):
    """The watcher's wall time during `turn`."""
    return WALL + TURN_WALL * turn


class WatcherLogTest(WatcherFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.world = {"client": False, "phase": "None", "clock": None}
        self.turn, self.turns, self.subject = 0, [], None

    def read_client(self):
        found = self.world["client"]
        if isinstance(found, Exception):
            raise found
        return client.Credentials(4242, TOKEN) if found else None

    def get(self, url, token, timeout):
        if self.world.pop("pause_at_read", False):  # a pause made by the page while this turn reads the phase
            self.subject.pause()
        phase = self.world["phase"]
        if isinstance(phase, Exception):
            raise phase
        return 200, json.dumps(phase).encode()

    def wall(self):
        return at(self.turn)

    def turn_sleep(self, seconds):
        """The sleep after a turn: the clock moves by it, the next turn's world is entered, or after the last turn
        the run is stopped."""
        self.clock.advance(seconds)
        self.turn += 1
        if self.turn >= len(self.turns):
            self.subject.stop()
            return
        self.enter(self.turns[self.turn])

    def enter(self, turn):
        turn = dict(turn)
        self.clock.advance(turn.pop("advance", 0.0))
        if turn.pop("pause", False):
            self.subject.pause()
        if turn.pop("resume", False):
            self.subject.resume()
        self.world.update(turn)

    def make(self, alerter=None):
        return watcher.Watcher(types.SimpleNamespace(read=self.read_client), alerter or self.alerter(),
                               addresses=lambda port: "http://client", get=self.get,
                               post=lambda url, token, timeout: 204, clock=self.clock, wall=self.wall,
                               sleep=self.turn_sleep, delay=lambda: self.world.get("delay", 0), log=self.lines.append,
                               live=lambda: "http://game", game_clock=lambda address: self.world["clock"])

    def logged(self, *turns):
        """run() over `turns`: the first is the world of turn 0, each next one is entered at the sleep after a turn;
        the events the watcher kept."""
        self.turns, self.subject = list(turns), self.make()
        self.enter(self.turns[0])
        self.subject.run()
        return self.subject.events()

    @staticmethod
    def numbered(*events):
        """The events as the ring holds them, their seq from 1."""
        return [(seq, *event) for seq, event in enumerate(events, start=1)]

    def test_a_whole_session_keeps_each_event_in_order_with_a_rising_seq_and_its_own_time(self):
        # Mutation: the phase noted on every read. Red: Lobby twice. Mutation: the ping's time read from the
        # watcher's wall. Red: the ping at the turn's time, not the Alerter's.
        kept = self.logged({"client": False}, {}, {"client": True, "phase": "Lobby"}, {}, {"phase": "Matchmaking"},
                           {"phase": "ReadyCheck"}, {"phase": "ChampSelect", "advance": watcher.HOLD_SECONDS},
                           {"phase": "InProgress"}, {"clock": 2.5, "advance": watcher.LIVE_POLL_SECONDS})
        self.assertEqual(kept, self.numbered(
            (at(0), "started", None), (at(0), "waiting", None), (at(2), "connected", None), (at(2), "phase", "Lobby"),
            (at(4), "phase", "Matchmaking"), (at(5), "phase", "ReadyCheck"), (at(5), "accepted", None),
            (PING_WALL, "ping", "sent"), (at(6), "phase", "ChampSelect"), (at(7), "phase", "InProgress"),
            (at(7), "loading", None), (at(8), "match_started", None), (PING_WALL, "ping", "sent")))
        # The console lines stay as they were: an event is noted beside its line, never in place of it.
        self.assertEqual(self.lines, [watcher.CONNECTED_LINE, watcher.ACCEPTING_LINE, watcher.ACCEPTED_LINE, SENT_LINE,
                                      watcher.LOADING_LINE, watcher.STARTED_LINE, SENT_LINE])

    def test_waiting_is_noted_once_per_waiting_period_and_never_right_after_a_lost_client(self):
        # Mutation: waiting noted on every turn with no client. Red: three waiting lines, then two after lost.
        # Mutation: waiting due again at lost. Red: a waiting line under the lost one.
        kept = self.logged({"client": False}, {}, {}, {"client": True, "phase": "Lobby"},
                           {"client": False, "phase": client.ClientUnreachable("ValueError")}, {}, {},
                           {"client": True, "phase": "Lobby"})
        self.assertEqual(kept, self.numbered(
            (at(0), "started", None), (at(0), "waiting", None), (at(3), "connected", None), (at(3), "phase", "Lobby"),
            (at(4), "lost", None), (at(7), "connected", None), (at(7), "phase", "Lobby")))
        self.assertEqual(self.lines, [watcher.CONNECTED_LINE, watcher.LOST_LINE, watcher.CONNECTED_LINE])

    def test_a_step_that_fails_notes_lost_only_when_the_client_was_connected_at_that_turn(self):
        # Mutation: lost noted at every failed step. Red: a lost line at turn 0, before any client.
        kept = self.logged({"client": RuntimeError("no read")}, {"client": False}, {"client": True, "phase": "Lobby"},
                           {"phase": RuntimeError("no phase")}, {"client": False, "phase": "Lobby"}, {})
        self.assertEqual(kept, self.numbered(
            (at(0), "started", None), (at(1), "waiting", None), (at(2), "connected", None), (at(2), "phase", "Lobby"),
            (at(3), "lost", None)))
        self.assertEqual(self.lines, [FAILED_LINE, watcher.CONNECTED_LINE, FAILED_LINE])

    def test_a_pause_and_a_resume_are_noted_and_the_resume_opens_a_waiting_period(self):
        # Mutation: resumed noted on every turn after a resume. Red: resumed at turns 4 and 5.
        kept = self.logged({"client": True, "phase": "Lobby"}, {"pause": True}, {}, {"resume": True, "client": False},
                           {}, {"client": True})
        self.assertEqual(kept, self.numbered(
            (at(0), "started", None), (at(0), "connected", None), (at(0), "phase", "Lobby"), (at(1), "paused", None),
            (at(3), "resumed", None), (at(3), "waiting", None), (at(5), "connected", None), (at(5), "phase", "Lobby")))
        self.assertEqual(self.lines, [watcher.CONNECTED_LINE, watcher.PAUSED_LINE, watcher.RESUMED_LINE,
                                      watcher.CONNECTED_LINE])

    def test_the_ring_keeps_the_last_fifty_and_the_seq_keeps_rising_past_it(self):
        # Mutation: the seq reset at the ring's wrap. Red: the seqs start again from 1 after the fiftieth.
        self.assertEqual(watcher.LOG_LINES, 50)
        alerter = self.alerter()
        subject = self.make(alerter)
        for _ in range(watcher.LOG_LINES + 10):
            alerter(QUEUE_FOUND)
        kept = subject.events()
        self.assertEqual([event[0] for event in kept], list(range(11, 61)))
        self.assertEqual({event[1:] for event in kept}, {(PING_WALL, "ping", "sent")})

    def test_events_answers_a_copy(self):
        # Mutation: events answers the ring itself. Red: the caller's clear empties the watcher's log.
        alerter = self.alerter()
        subject = self.make(alerter)
        alerter(QUEUE_FOUND)
        first = subject.events()
        first.clear()
        self.assertEqual(subject.events(), [(1, PING_WALL, "ping", "sent")])
        held = subject.events()
        alerter(QUEUE_FOUND)
        self.assertEqual(held, [(1, PING_WALL, "ping", "sent")])
        self.assertEqual(len(subject.events()), 2)

    def test_a_note_from_another_thread_waits_for_the_held_lock_and_its_seq_stays_whole(self):
        # It proves the note takes the lock, and that 2000 notes from a thread leave every read whole and in order;
        # that events() reads under the lock is the next pin's. Mutation: the lock removed around the note. Red:
        # the ping's thread does not wait for the held lock.
        listening = Listening()
        subject = self.make(listening)
        with subject._lock:  # the lock events() reads under: a note made meanwhile has to wait for it
            noting = threading.Thread(target=listening.tell, args=("sent", PING_WALL), daemon=True)
            noting.start()
            noting.join(0.3)
            self.assertTrue(noting.is_alive())
        noting.join(2)
        self.assertFalse(noting.is_alive())
        self.assertEqual(subject.events(), [(1, PING_WALL, "ping", "sent")])
        # Many notes from a thread while this one reads: every copy is whole and in order, and none is lost.
        count = 2000
        many = threading.Thread(target=lambda: [listening.tell("sent", PING_WALL) for _ in range(count)], daemon=True)
        many.start()
        while many.is_alive():
            seqs = [event[0] for event in subject.events()]
            self.assertEqual(seqs, list(range(seqs[0], seqs[0] + len(seqs))))
        many.join(5)
        self.assertEqual(subject.events()[-1][0], count + 1)

    def test_events_reads_the_ring_under_the_lock_a_note_takes(self):
        # Mutation: events() reads with no lock. Red: the read returns while the lock is held.
        subject = self.make(Listening())
        reads = []
        with subject._lock:
            reading = threading.Thread(target=lambda: reads.append(subject.events()), daemon=True)
            reading.start()
            reading.join(0.3)
            self.assertTrue(reading.is_alive())
            self.assertEqual(reads, [])
        reading.join(2)
        self.assertFalse(reading.is_alive())
        self.assertEqual(reads, [[]])

    def test_a_pause_inside_the_accept_delay_notes_no_accepted(self):
        # Mutation: no pause guard after the accept delay (watcher.py, _ready_check). Red: accepted noted, and the
        # accepted line said, though the pause came before the accept.
        kept = self.logged({"client": True, "phase": "ReadyCheck", "delay": 2.0}, {"pause": True}, {})
        self.assertEqual(kept, self.numbered(
            (at(0), "started", None), (at(0), "connected", None), (at(0), "phase", "ReadyCheck"),
            (at(2), "paused", None)))
        self.assertEqual(self.lines, [watcher.CONNECTED_LINE, watcher.ACCEPTING_LINE, watcher.PAUSED_LINE])

    def test_an_alert_that_finds_the_watcher_paused_notes_no_match_started(self):
        # Mutation: the match's start noted before _fire's pause guard. Red: match_started at turn 2, while paused.
        kept = self.logged({"client": True, "phase": "ChampSelect"}, {"phase": "InProgress"},
                           {"clock": 2.5, "advance": watcher.LIVE_POLL_SECONDS, "pause_at_read": True}, {})
        self.assertEqual(kept, self.numbered(
            (at(0), "started", None), (at(0), "connected", None), (at(0), "phase", "ChampSelect"),
            (at(1), "phase", "InProgress"), (at(1), "loading", None), (at(3), "paused", None)))
        self.assertEqual(self.lines, [watcher.CONNECTED_LINE, watcher.LOADING_LINE, watcher.PAUSED_LINE])

    def test_a_pause_and_a_resume_inside_one_sleep_note_paused_and_resumed_once_each(self):
        # Mutation: the rest keyed on the pause event alone, not on the pause count. Red: neither is noted.
        # Mutation: the count honored never recorded. Red: paused noted again at turn 2.
        kept = self.logged({"client": True, "phase": "Lobby"}, {"pause": True, "resume": True}, {})
        self.assertEqual(kept, self.numbered(
            (at(0), "started", None), (at(0), "connected", None), (at(0), "phase", "Lobby"), (at(1), "paused", None),
            (at(1), "resumed", None), (at(1), "connected", None), (at(1), "phase", "Lobby")))
        self.assertEqual(self.lines, [watcher.CONNECTED_LINE, watcher.PAUSED_LINE, watcher.RESUMED_LINE,
                                      watcher.CONNECTED_LINE])

    def test_the_match_start_is_noted_on_its_kind_not_on_the_line_given_with_it(self):
        # Mutation: match_started noted when a line is given (round 1). Red: a start fired with no line notes
        # nothing.
        def plain(kind):
            self.pings.append(kind)
        plain.sound = lambda: None
        plain.last_ping = lambda: (None, None)
        subject = self.make(plain)
        subject._fire(watcher.MATCH_STARTED)
        subject._fire(QUEUE_FOUND)
        self.assertEqual(subject.events(), [(1, at(0), "match_started", None)])
        self.assertEqual((self.pings, self.lines), ([watcher.MATCH_STARTED, QUEUE_FOUND], []))

    def test_a_watcher_whose_alert_cannot_listen_keeps_its_events_with_no_ping(self):
        # The tests' fakes have no listen: the watcher is built and runs as before, its pings not kept.
        def plain(kind):
            self.pings.append(kind)
        plain.sound = lambda: None
        plain.last_ping = lambda: (None, None)
        self.turns = [{"client": True, "phase": "ReadyCheck"}]
        self.subject = self.make(plain)
        self.enter(self.turns[0])
        self.subject.run()
        self.assertEqual(self.subject.events(), self.numbered(
            (at(0), "started", None), (at(0), "connected", None), (at(0), "phase", "ReadyCheck"),
            (at(0), "accepted", None)))
        self.assertEqual(self.pings, [QUEUE_FOUND])


if __name__ == "__main__":
    unittest.main()
