"""The hold after the start of a TFT app's match: once the watcher alerts that start, it reads nothing for
MATCH_HOLD_SECONDS, 15 minutes (neither the app's log nor its process, no alert, no click), then goes on as ever. A
pause drops the hold as it drops any, so the resume reads at once, and a stop ends the loop at once. The League
client's start holds nothing. The app's chain is test_tft_repop's AppPopFixture; the League client's fakes are
test_watcher's WatcherFixture."""
import threading
import time
import unittest

import support
from test_tft_app import LINES, POP_AT
from test_tft_repop import AppPopFixture, stamped
from test_watcher import MATCH_STARTED, WatcherFixture

watcher = support.module("watcher")

HOLD = watcher.MATCH_HOLD_SECONDS
POP_LINE = b"Match found, showing AFK Check Modal"


class GameLog:
    """The game's own log as the watch asks it: a start line once a case says so, never a join."""

    joined = False

    def __init__(self):
        self.started = False

    def reset(self):
        pass

    def __call__(self, since):
        return self.started


class AppMatchHoldTest(AppPopFixture, unittest.TestCase):
    def setUp(self):
        self.seconds, self.game_log = None, GameLog()  # before the fixture builds the watcher with them
        super().setUp()

    def watcher(self, **kwargs):
        return super().watcher(live=lambda: "http://127.0.0.1:9", game_clock=lambda base: self.seconds,
                               game_log=self.game_log, **kwargs)

    def started(self, how):
        """A pop accepted by the click, the loading screen, then the start told by `how`: the game's clock, the game's
        log, or neither for LIVE_FALLBACK_SECONDS; the time of the start's alert."""
        self.pop()
        self.turn()
        self.write(LINES["loading"])
        self.turn()
        self.assertEqual(self.lines[-1], watcher.LOADING_LINE)
        self.write(LINES["ingame"])
        if how == "clock":
            self.seconds = 2.5
        elif how == "log":
            self.game_log.started = True
        for _ in range(1000):  # the wait's 120 s at the most
            if any(kind == MATCH_STARTED for kind, _ in self.alerts):
                return self.now
            self.turn()
        self.fail("no start alerted")

    def check_the_hold(self, how, line):
        start = self.started(how)
        self.assertEqual((self.lines[-1], len(self.alerts), self.clicks), (line, 2, [1.3]))
        reads = self.reads
        self.now = start + 100
        self.write(stamped(self.now, POP_LINE))  # a pop inside the hold
        self.turns_until(start + HOLD - 0.01)
        self.assertEqual((self.reads, len(self.alerts), self.clicks), (reads, 2, [1.3]))
        self.now = start + HOLD
        self.subject.step()  # the hold over: the log read, the pop inside it too old to alert
        self.assertEqual((self.reads, len(self.alerts), self.clicks), (reads + 1, 2, [1.3]))
        self.now = start + HOLD + 1
        self.write(stamped(self.now, POP_LINE))
        self.turn()
        self.assertEqual(self.alerts[2:], [(watcher.QUEUE_FOUND, round(start + HOLD + 1.3 - POP_AT, 3))])
        self.assertEqual(len(self.clicks), 2)

    def test_a_start_told_by_the_game_s_clock_holds_15_minutes_and_only_a_pop_after_them_alerts(self):
        # Mutation: no hold after the start. Red: the log read on each turn of the 15 minutes. Mutation: a hold of
        # 899 s. Red: the log read at start + 899.x. Mutation: a hold of 901 s. Red: no read at start + 900.
        self.assertEqual(HOLD, 900.0)
        self.check_the_hold("clock", watcher.STARTED_LINE)

    def test_a_start_told_by_the_game_s_log_holds_the_same(self):
        self.check_the_hold("log", watcher.STARTED_LINE)

    def test_a_start_alerted_on_the_wait_holds_the_same(self):
        self.check_the_hold("wait", watcher.STARTED_ON_WAIT_LINE)

    def test_a_pause_and_a_resume_inside_the_hold_work_and_the_resume_reads_at_once(self):
        # Mutation: the hold kept through the pause. Red: the turn after the resume reads nothing.
        self.subject, run = self.looped()
        start = self.started("clock")
        reads = self.reads
        self.subject.pause()
        run(2)
        self.subject.resume()
        run(1)
        self.assertEqual(self.reads, reads + 1)
        self.assertLess(self.now - start, 1.0 + watcher.IN_GAME_PAUSE)  # read on the resume's turn, then its pause
        self.assertEqual(self.lines[-3:], [watcher.PAUSED_LINE, watcher.RESUMED_LINE, watcher.CONNECTED_LINE])
        self.assertEqual(len(self.alerts), 2)  # a match under way at the resume alerts nothing

    def test_a_stop_inside_the_hold_ends_the_loop_at_once(self):
        # Mutation: the hold as one wait of 15 minutes that no stop ends. Red: the loop still runs 2 s after the stop.
        self.subject = self.watcher(sleep=None)  # the loop's sleep is the stop's own wait, as in the program
        self.started("clock")
        loop = threading.Thread(target=self.subject.run, daemon=True)
        loop.start()
        deadline = time.monotonic() + 2.0
        while not any(kind == "started" for _, _, kind, _ in self.subject.events()) and time.monotonic() < deadline:
            time.sleep(0.01)
        time.sleep(0.4)  # inside a turn's wait, the clock frozen inside the hold
        asked = time.monotonic()
        self.subject.stop()
        loop.join(2.0)
        self.assertFalse(loop.is_alive())
        self.assertLess(time.monotonic() - asked, 1.0)


class LeagueStartTest(WatcherFixture, unittest.TestCase):
    def test_a_league_match_start_holds_nothing_and_its_next_turn_reads_the_client(self):
        # Mutation: every start holds 15 minutes. Red: the turn IN_GAME_PAUSE after the start reads nothing.
        subject = self.watcher()
        self.read(subject, "Lobby", "ChampSelect", "InProgress")
        self.game.clock(2.5)
        self.read(subject, *["InProgress"] * 4)
        self.assertEqual([kind for *_, kind in self.pings], [MATCH_STARTED])
        asked = len(self.fake.requests)
        self.clock.advance(watcher.IN_GAME_PAUSE)
        self.assertEqual(subject.step(), watcher.IN_GAME_PAUSE)
        self.assertEqual(len(self.fake.requests), asked + 1)


if __name__ == "__main__":
    unittest.main()
