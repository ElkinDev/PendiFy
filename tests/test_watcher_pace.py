"""InGamePaceTest: the pace of the watcher's turns during a match, one read of the client's phase every five seconds.

Once the match has started, or was already under way when the client was found, no watch runs and the phase is read
only to see a boundary phase end the game: a turn that reads InProgress and leaves no watch running answers
IN_GAME_PAUSE. The loading screen up to the start, while the watch asks the game its clock, every other phase,
Reconnect, the hold, no client and a lost client answer what they answered before. The cost: a pause from the page
during a match is honored at the next turn, up to five seconds later; a stop is still immediate. The fakes are
WatcherFixture's, on 127.0.0.1. IN_GAME is the pace as a number, so a watcher with no such pace reds on its answers.
"""
import threading
import time
import unittest

import support
from support import LINK_ID, SECRET
from test_watcher import MATCH_STARTED, PHASE, Credentials, WatcherFixture

watcher = support.module("watcher")

ACCEPT = support.CLIENT_ACCEPT_PATH
STEP, NO_CLIENT, IN_GAME = 0.3, 3.0, 5.0


class InGamePaceTest(WatcherFixture, unittest.TestCase):
    def answers(self, subject, *phases):
        """One step per phase, 0.3 s apart as WatcherFixture.read; the pause each step answered."""
        answered = []
        for phase in phases:
            self.fake.phase = phase
            self.clock.advance(0.3)
            answered.append(subject.step())
        return answered

    def started(self, subject):
        """A game seen from the lobby to its true start: the loading screen, then the game's clock above zero."""
        self.answers(subject, "Lobby", "ChampSelect", "InProgress")
        self.game.clock(2.5)
        self.answers(subject, *["InProgress"] * 4)  # the game's clock is asked again 1 s after the arrival
        self.assertEqual((len(self.beeps), self.pings), (2, [(LINK_ID, SECRET, MATCH_STARTED)]))

    def test_after_the_start_alert_a_turn_that_reads_in_progress_answers_the_in_game_pause(self):
        # (a) Mutation: the condition dropped, always STEP_PAUSE. Red: the turns of the match answer 0.3.
        subject = self.watcher()
        self.started(subject)
        asked = self.game.count()
        self.assertEqual(self.answers(subject, *["InProgress"] * 5), [IN_GAME] * 5)
        self.assertEqual((len(self.beeps), len(self.pings), self.game.count()), (2, 1, asked))  # the game: nothing
        self.assertEqual((watcher.IN_GAME_PAUSE, watcher.STEP_PAUSE), (IN_GAME, STEP))

    def test_from_the_loading_screen_to_the_start_while_the_watch_runs_every_turn_answers_the_step_pause(self):
        # (b) Mutation: the condition without the watch check. Red: the loading screen's turns answer 5.0.
        subject = self.watcher()
        answered = self.answers(subject, "Lobby", "ChampSelect", *["InProgress"] * 40)  # 12 s, the game's port closed
        self.assertEqual((len(self.beeps), self.pings), (1, []))  # the loading screen: the watch asks the game's clock
        self.assertEqual(answered, [STEP] * 42)
        self.assertEqual(self.game.count(), 10)  # once a second: the watch ran on every one of those turns
        self.game.clock(2.5)
        self.assertEqual(self.answers(subject, "InProgress"), [IN_GAME])  # the start: the watch ends on this turn
        self.assertEqual([kind for *_, kind in self.pings], [MATCH_STARTED])

    def test_a_first_read_of_in_progress_a_game_already_under_way_answers_the_in_game_pause(self):
        # (c) Mutation: the condition dropped, always STEP_PAUSE. Red: the game under way is read every 0.3 s.
        self.game.clock(30.0)
        subject = self.watcher()
        self.assertEqual(self.answers(subject, *["InProgress"] * 3), [IN_GAME] * 3)
        self.assertEqual((self.beeps, self.pings, self.game.count()), ([], [], 0))
        self.assertEqual(self.lines, [watcher.CONNECTED_LINE])

    def test_every_other_phase_and_reconnect_answer_the_step_pause(self):
        # (d) Mutation: IN_GAME_PAUSE on any turn with no watch. Red: the lobby's turns answer 5.0.
        subject = self.watcher()
        phases = ("None", "Lobby", "Matchmaking", "ChampSelect", "GameStart", "Reconnect", "PreEndOfGame",
                  "WaitingForStats", "EndOfGame", "TerminatedInError", "Lobby")
        self.assertEqual(self.answers(subject, *phases), [STEP] * len(phases))
        under_way = self.watcher(credentials=Credentials(self.fake.port))
        self.assertEqual(self.answers(under_way, "Reconnect", "InProgress", "Reconnect", "Reconnect", "InProgress"),
                         [STEP, IN_GAME, STEP, STEP, IN_GAME])

    def test_the_turn_that_reads_a_boundary_phase_after_a_match_answers_the_step_pause(self):
        # (e) Mutation: the match's pace kept until the next loading screen. Red: the end of the game answers 5.0.
        subject = self.watcher()
        self.started(subject)
        self.assertEqual(self.answers(subject, "InProgress", "EndOfGame", "Lobby", "ChampSelect"),
                         [IN_GAME, STEP, STEP, STEP])
        self.game.answer = None  # the next game's port, closed until its match starts
        self.assertEqual(self.answers(subject, "InProgress", "InProgress"), [STEP, STEP])  # the next game's loading
        self.assertEqual(len(self.beeps), 3)

    def test_with_no_client_the_turn_answers_the_no_client_pause_and_the_hold_the_step_pause(self):
        # (f) Mutation: IN_GAME_PAUSE on the hold's early answer. Red: the hold's turns answer 5.0.
        credentials = Credentials(None)
        subject = self.watcher(credentials=credentials)
        self.assertEqual(self.answers(subject, "InProgress", "InProgress"), [NO_CLIENT] * 2)
        credentials.port = self.fake.port
        self.assertEqual(self.answers(subject, "ReadyCheck"), [STEP])  # accepted: the hold of 15 s starts
        self.assertEqual(self.fake.count("POST", ACCEPT), 1)
        reads = self.fake.count("GET", PHASE)
        self.assertEqual(self.answers(subject, *["InProgress"] * 3), [STEP] * 3)
        self.assertEqual(self.fake.count("GET", PHASE), reads)  # the hold: nothing read

    def test_a_client_lost_during_a_match_answers_the_step_pause_then_the_no_client_pause(self):
        # (f) Mutation: the lost turn answering IN_GAME_PAUSE. Red: the lost client's turn answers 5.0.
        credentials = Credentials(self.fake.port)
        subject = self.watcher(credentials=credentials)
        self.started(subject)
        self.assertEqual(self.answers(subject, "InProgress"), [IN_GAME])
        self.fake.dropping = True
        self.assertEqual(self.answers(subject, "InProgress"), [STEP])
        self.assertEqual(self.lines.count(watcher.LOST_LINE), 1)
        credentials.port = None
        self.assertEqual(self.answers(subject, "InProgress"), [NO_CLIENT])

    def test_run_sleeps_the_pause_the_step_answered(self):
        # (g) Mutation: run() sleeping STEP_PAUSE after every step. Red: no 5.0 among the sleeps.
        script = ["Lobby", "ChampSelect", *["InProgress"] * 7, "EndOfGame", "Lobby"]
        stop, sleeps = threading.Event(), []

        def sleep(seconds):
            sleeps.append(seconds)
            self.clock.advance(seconds)
            if len(sleeps) == 5:  # the third turn of the loading screen: the match's clock runs from now on
                self.game.clock(2.5)
            if len(sleeps) == len(script):
                stop.set()
                return
            self.fake.phase = script[len(sleeps)]

        self.fake.phase = script[0]
        self.watcher(sleep=sleep, stop=stop).run()
        self.assertEqual(sleeps, [STEP] * 6 + [IN_GAME] * 3 + [STEP] * 2)
        self.assertEqual((len(self.beeps), [kind for *_, kind in self.pings]), (2, [MATCH_STARTED]))
        self.assertEqual(self.fake.count("GET", PHASE), len(script))

    def test_a_pause_set_during_a_match_is_honored_at_the_next_turn_up_to_five_seconds_later(self):
        # (h) The cost, pinned: the paused line and the resting state come at the turn after the match's five seconds.
        # Mutation: the condition dropped. Red: the pause honored 0.3 s after it landed.
        stop, sleeps = threading.Event(), []
        credentials = Credentials(self.fake.port)

        def sleep(seconds):
            sleeps.append((seconds, self.clock(), watcher.PAUSED_LINE in self.lines))
            if len(sleeps) == 1:
                subject.pause()  # the page's pause lands at the start of the match's sleep
            self.clock.advance(seconds)
            if len(sleeps) == 3:
                stop.set()

        self.fake.phase = "InProgress"  # a game already under way when the client is found
        subject = self.watcher(credentials=credentials, sleep=sleep, stop=stop)
        paused_at = self.clock()
        subject.run()
        self.assertEqual(sleeps, [(IN_GAME, paused_at, False), (STEP, paused_at + IN_GAME, True),
                                  (STEP, paused_at + IN_GAME + STEP, True)])
        self.assertEqual(self.lines, [watcher.CONNECTED_LINE, watcher.PAUSED_LINE])
        self.assertEqual((credentials.reads, self.fake.count("GET", PHASE)), (1, 1))
        self.assertEqual(subject.snapshot(), {**self.shown(None, client_state="waiting"), "paused": True})

    def test_a_stop_during_the_match_s_sleep_ends_the_loop_at_once_with_no_other_step(self):
        # (h) Mutation: run() sleeping the pause with time.sleep. Red: the loop ends five seconds later.
        self.fake.phase = "InProgress"  # a game already under way: the step answers IN_GAME_PAUSE
        credentials = Credentials(self.fake.port)
        subject = self.watcher(credentials=credentials, sleep=None)  # the real sleep: the stop event's wait
        loop = threading.Thread(target=subject.run, daemon=True)
        loop.start()
        deadline = time.monotonic() + 5.0
        while self.fake.count("GET", PHASE) < 1 and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(self.fake.count("GET", PHASE), 1)
        stopped = time.monotonic()
        subject.stop()
        loop.join(2.0)
        self.assertFalse(loop.is_alive())
        self.assertLess(time.monotonic() - stopped, 2.0)
        self.assertEqual((credentials.reads, self.fake.count("GET", PHASE)), (1, 1))


if __name__ == "__main__":
    unittest.main()
