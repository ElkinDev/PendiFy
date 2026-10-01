"""WatcherPauseTest: the pause of the watcher, set from the page's thread and honored by the loop on its own.

Paused, run() makes no step, so nothing reads the client's files or process, the client's port or the game's port.
The first paused turn forgets the client, the watch, the hold and the start latch; the first turn after a resume is
a fresh connection, so a game already under way at the resume alerts nothing. The pause lives in memory only: every
watcher starts active. The fakes are WatcherFixture's, on 127.0.0.1; run() is driven turn by turn on this thread
by a sleep that sets the stop after the turns a case asks for.
"""
import threading
import unittest

import support
from support import LINK_ID, SECRET
from test_watcher import MATCH_STARTED, QUEUE_FOUND, Credentials, WatcherFixture

client = support.module("client")
watcher = support.module("watcher")

ACCEPT = support.CLIENT_ACCEPT_PATH
DELAY = 1.7  # WatcherFixture's accept delay: the one sleep inside a step, never a turn
TURNS = 10


class WatcherPauseTest(WatcherFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.stop = threading.Event()
        self.turn_sleeps, self.left, self.at_delay = [], 0, None

    def turn_sleep(self, seconds):
        """The loop's sleep: the clock moves; the accept delay is no turn and runs `at_delay` when a case sets it;
        after the last turn a case asked for, the stop is set."""
        self.clock.advance(seconds)
        if seconds == DELAY:
            if self.at_delay is not None:
                self.at_delay()
            return
        self.turn_sleeps.append(seconds)
        self.left -= 1
        if self.left <= 0:
            self.stop.set()

    def looped(self, **kwargs):
        self.credentials = Credentials(self.fake.port)
        return self.watcher(credentials=self.credentials, stop=self.stop, sleep=self.turn_sleep, delay=DELAY,
                            **kwargs)

    def turns(self, subject, count, phase=None):
        """`count` turns of run() on this thread, the fake client answering `phase` when one is given."""
        if phase is not None:
            self.fake.phase = phase
        self.left = count
        subject.run()
        self.stop.clear()

    def gets(self):
        return len(self.fake.requests)

    def test_paused_turns_of_run_read_neither_the_client_nor_its_port_nor_the_game_s_port(self):
        # Mutation: the paused branch of run removed (a step on every turn). Red: credential reads and gets counted.
        subject = self.looped()
        self.turns(subject, 3, "Lobby")
        self.assertEqual((self.credentials.reads, self.gets()), (1, 3))
        self.game.clock(2.5)
        subject.pause()
        self.turns(subject, TURNS)
        self.assertEqual((self.credentials.reads, self.gets(), self.game.count()), (1, 3, 0))
        self.assertEqual(self.turn_sleeps[3:], [watcher.STEP_PAUSE] * TURNS)
        self.assertEqual(self.lines, [watcher.CONNECTED_LINE, watcher.PAUSED_LINE])
        self.assertEqual((self.beeps, self.pings), ([], []))
        self.assertEqual(subject.snapshot(), {**self.shown(None, client_state="waiting"), "paused": True})

    def test_a_pause_during_the_loading_watch_ends_it_and_the_true_start_fires_nothing_before_or_after_a_resume(self):
        # Mutation: the watch kept on at the first paused turn. Red: the clock read after the resume pings the start.
        subject = self.looped()
        for phase in ("Lobby", "ChampSelect", "InProgress"):
            self.turns(subject, 1, phase)
        self.assertEqual((len(self.beeps), self.pings), (1, []))  # the loading screen, said on the PC only
        asked = self.game.count()
        self.game.clock(2.5)  # the match's clock runs: any read of it from now on is the true start
        subject.pause()
        self.turns(subject, TURNS)
        self.assertEqual((self.game.count(), len(self.beeps), self.pings), (asked, 1, []))
        subject.resume()
        self.turns(subject, TURNS, "InProgress")  # the same game, still under way at the resume
        self.assertEqual((self.game.count(), len(self.beeps), self.pings), (asked, 1, []))
        self.assertEqual(self.lines.count(watcher.STARTED_LINE), 0)
        self.assertEqual(self.lines[-2:], [watcher.RESUMED_LINE, watcher.CONNECTED_LINE])
        self.assertEqual(subject.snapshot(), {**self.shown("InProgress", "loading"), "paused": False})

    def test_a_pause_inside_the_accept_delay_posts_no_accept_and_fires_nothing(self):
        # Mutation: the pause check after the accept delay removed. Red: the accept is posted.
        subject = self.looped()
        self.at_delay = subject.pause  # the page's pause lands while the step waits out the delay
        self.turns(subject, 1, "Lobby")
        self.turns(subject, TURNS, "ReadyCheck")
        self.assertEqual((self.fake.count("POST", ACCEPT), self.pings, self.beeps), (0, [], []))
        self.assertEqual(self.lines, [watcher.CONNECTED_LINE, watcher.ACCEPTING_LINE, watcher.PAUSED_LINE])
        self.assertEqual((self.credentials.reads, self.gets()), (1, 2))
        self.assertIsNone(subject.snapshot()["alert"])

    def test_a_start_read_by_a_step_the_pause_lands_inside_fires_nothing(self):
        # Mutation: the pause guard in _fire removed. Red: the clock read inside the paused step pings the start.
        landing = []

        def live():
            if landing:
                subject.pause()  # the page's pause lands while the step asks the game its clock
            return self.game.base

        subject = self.looped(live=live)
        for phase in ("Lobby", "ChampSelect", "InProgress"):
            self.turns(subject, 1, phase)
        self.game.clock(2.5)
        landing.append(True)
        self.turns(subject, TURNS, "InProgress")
        self.assertEqual((len(self.beeps), self.pings), (1, []))
        self.assertEqual(subject.snapshot()["alert"], "loading")
        self.assertEqual(self.lines[-1], watcher.PAUSED_LINE)

    def test_an_arrival_read_by_a_step_the_pause_lands_inside_says_nothing_and_starts_no_watch(self):
        # Mutation: the pause guard in _loading removed. Red: the loading screen beeps and the watch asks the clock.
        landing = []

        def get(url, token, timeout):
            answer = client.get(url, token, timeout)
            if landing:
                subject.pause()  # the page's pause lands right after the phase is read
            return answer

        subject = self.looped(get=get)
        for phase in ("Lobby", "ChampSelect"):
            self.turns(subject, 1, phase)
        self.game.clock(2.5)
        landing.append(True)
        self.turns(subject, TURNS, "InProgress")
        self.assertEqual((self.beeps, self.pings, self.game.count()), ([], [], 0))
        self.assertEqual(self.lines, [watcher.CONNECTED_LINE, watcher.PAUSED_LINE])
        self.assertIsNone(subject.snapshot()["alert"])

    def test_a_resume_connects_again_and_the_next_game_alerts_as_ever(self):
        # Mutation: the client kept at the pause. Red: no second credential read and no connected line after it.
        subject = self.looped()
        self.turns(subject, 1, "Lobby")
        subject.pause()
        self.turns(subject, 3)
        subject.resume()
        self.turns(subject, 1, "Lobby")
        self.assertEqual(self.credentials.reads, 2)
        for phase in ("ChampSelect", "InProgress"):
            self.turns(subject, 1, phase)
        self.game.clock(2.5)
        self.turns(subject, 4, "InProgress")  # the game's clock is asked again 1 s after the arrival
        self.assertEqual((len(self.beeps), self.pings), (2, [(LINK_ID, SECRET, MATCH_STARTED)]))
        self.turns(subject, 1, "EndOfGame")
        self.turns(subject, 1, "ReadyCheck")
        self.assertEqual(self.fake.count("POST", ACCEPT), 1)
        self.assertEqual(self.pings[-1], (LINK_ID, SECRET, QUEUE_FOUND))
        self.assertEqual(self.lines, [watcher.CONNECTED_LINE, watcher.PAUSED_LINE, watcher.RESUMED_LINE,
                                      watcher.CONNECTED_LINE, watcher.LOADING_LINE, watcher.STARTED_LINE,
                                      watcher.ACCEPTING_LINE, watcher.ACCEPTED_LINE])

    def test_a_pause_drops_the_hold_so_a_resume_reads_the_client_at_once(self):
        # Mutation: the hold kept at the pause. Red: the turn after the resume reads nothing for 15 s.
        subject = self.looped()
        self.turns(subject, 1, "ReadyCheck")  # accepted: the hold of 15 s starts
        self.assertEqual(self.fake.count("POST", ACCEPT), 1)
        subject.pause()
        self.turns(subject, 2)
        subject.resume()
        self.turns(subject, 1, "Lobby")
        self.assertEqual((self.credentials.reads, self.gets()), (2, 3))

    def test_the_snapshot_says_paused_from_either_thread_and_every_watcher_starts_active(self):
        # Mutation: the snapshot reads a flag the loop sets. Red: paused still False before the next turn.
        subject = self.looped()
        self.assertEqual((subject.paused, subject.snapshot()["paused"]), (False, False))
        subject.pause()
        self.assertEqual((subject.paused, subject.snapshot()["paused"]), (True, True))
        subject.resume()
        self.assertEqual((subject.paused, subject.snapshot()["paused"]), (False, False))
        page_thread = threading.Thread(target=subject.pause)
        page_thread.start()
        page_thread.join(5)
        self.assertEqual(subject.snapshot()["paused"], True)
        self.assertEqual(self.looped().snapshot()["paused"], False)

    def test_each_console_line_is_said_once_per_transition(self):
        # Mutation: the paused line said on every paused turn. Red: ten paused lines.
        subject = self.looped()
        self.turns(subject, 2, "Lobby")
        subject.pause()
        self.turns(subject, TURNS)
        subject.pause()  # a second pause while paused is no transition
        self.turns(subject, 3)
        subject.resume()
        self.turns(subject, 3, "Lobby")
        subject.resume()
        self.turns(subject, 2, "Lobby")
        subject.pause()
        self.turns(subject, 2)
        self.assertEqual(self.lines, [watcher.CONNECTED_LINE, watcher.PAUSED_LINE, watcher.RESUMED_LINE,
                                      watcher.CONNECTED_LINE, watcher.PAUSED_LINE])
        self.assertEqual(watcher.PAUSED_LINE, "paused: not reading the game client")
        self.assertEqual(watcher.RESUMED_LINE, "resumed: looking for the game client again")


if __name__ == "__main__":
    unittest.main()
