"""TrueStartTest: the match's true start, read as the game's own clock passing zero on its loopback port.

The arrival of InProgress after a read of another phase is the loading screen: a beep on the PC only and the
last alert "loading". From there the watcher asks the game's port one thing, its clock, at most once a
second, until the clock is above zero (match_started, a beep and a ping) or 120 s pass with InProgress read
(the same alert, on the wait). A game that serves no clock is read from its own log on the same turns, a start line
stamped no earlier than 30 s before the loading screen the start; while that log shows the game joined its match and
no start line, the wait holds, up to 300 s from the loading screen. The game's port is a fake on 127.0.0.1 over
plain http, the game's log a fake reader, the clock is the injected one and nothing sleeps. AlerterSoundTest: the
loading screen's sound is a beep and nothing else.
"""
import contextlib
import inspect
import io
import json
import threading
import unittest

import support
from support import LINK_ID, SECRET
from test_watcher import (GAME_WORDS, MATCH_STARTED, QUEUE_FOUND, TOKEN, WALL, Credentials, FakeGameLog,
                          WatcherFixture)

client = support.module("client")
watcher = support.module("watcher")

CLOCK = 4321.5  # a game clock no line, port or snapshot value of these tests could hold
STARTED_PING = (LINK_ID, SECRET, MATCH_STARTED)


def body(value):
    return json.dumps(value).encode()


class JoinedGameLog(FakeGameLog):
    """FakeGameLog with the real reader's `joined`: False when built and after reset(), set by the test between steps
    as the reader sets it when it reads a join line of the game's own log."""

    def __init__(self, *answers):
        super().__init__(*answers)
        self.joined = False

    def reset(self):
        super().reset()
        self.joined = False


class TrueStartTest(WatcherFixture, unittest.TestCase):
    def arrive(self, subject):
        """Lobby, ChampSelect, then the arrival of InProgress; answers the injected clock at the arrival."""
        self.read(subject, "Lobby", "ChampSelect", "InProgress")
        return self.clock()

    def step_at(self, subject, at, phase="InProgress"):
        """One step at `at` on the injected clock, the fake client answering `phase`."""
        self.fake.phase = phase
        self.clock.now = at
        subject.step()

    def test_the_names_and_values_of_the_true_start(self):
        # Mutation: the wait made 121 s, or the clock route another one. Red: the values differ.
        self.assertEqual((client.LIVE_PORT, client.LIVE_CLOCK_PATH, client.LIVE_TIMEOUT),
                         (2999, "/liveclientdata/gamestats", 1.0))
        self.assertEqual(client.real_live_address(), "https://127.0.0.1:2999")
        self.assertEqual((watcher.LIVE_POLL_SECONDS, watcher.LIVE_FALLBACK_SECONDS), (1.0, 120.0))
        defaults = inspect.signature(watcher.Watcher).parameters
        self.assertIs(defaults["live"].default, client.real_live_address)
        self.assertIs(defaults["game_clock"].default, client.game_clock)
        self.assertEqual((watcher.LOADING_LINE, watcher.STARTED_LINE, watcher.STARTED_ON_WAIT_LINE),
                         ("loading screen: waiting for the match to start", "match started: alerting",
                          "match started: the game gave no clock, alerting on the wait"))
        self.assertEqual(support.module("worker").KINDS, (QUEUE_FOUND, MATCH_STARTED))  # no new kind

    def test_the_arrival_of_in_progress_beeps_once_pings_nothing_and_shows_the_loading_screen(self):
        # Mutation: the arrival firing match_started at once, as before. Red: a ping and the alert "started".
        # Mutation: the first clock read due a second after the arrival. Red: the game's port counts 0.
        subject = self.watcher()
        self.arrive(subject)
        self.assertEqual((len(self.beeps), self.pings), (1, []))
        self.assertEqual(subject.snapshot(), self.shown("InProgress", "loading"))
        self.assertEqual(self.lines, [watcher.CONNECTED_LINE, watcher.LOADING_LINE])
        self.assertEqual(self.game.count(), 1)  # the first clock read is due at once, in the arrival's own step

    def test_the_clock_passing_zero_is_the_start_one_ping_one_more_beep_then_nothing_for_that_game(self):
        # Mutation: a clock of zero taken as a start (>= 0). Red: the ping at the clock's 0.
        subject = self.watcher()
        since = self.arrive(subject)
        for second in (1, 2):  # the port does not serve yet
            self.step_at(subject, since + second)
        self.game.clock(0)
        for second in (3, 4):
            self.step_at(subject, since + second)
        self.game.clock(-0.5)
        self.step_at(subject, since + 5)
        self.assertEqual((len(self.beeps), self.pings, self.game.count()), (1, [], 6))
        self.assertEqual(subject.snapshot(), self.shown("InProgress", "loading"))
        self.game.clock(0.02)
        self.step_at(subject, since + 6)
        self.assertEqual((len(self.beeps), self.pings, self.game.count()), (2, [STARTED_PING], 7))
        self.assertEqual(subject.snapshot(), self.shown("InProgress", "started", "sent"))
        self.assertEqual(self.lines, [watcher.CONNECTED_LINE, watcher.LOADING_LINE, watcher.STARTED_LINE,
                                      "alert: sent to the phone"])
        self.game.clock(CLOCK)
        for second in range(7, 400, 3):  # however long InProgress lasts, the wait included
            self.step_at(subject, since + second)
        self.assertEqual((len(self.beeps), len(self.pings), self.game.count()), (2, 1, 7))
        self.assertNotIn(watcher.STARTED_ON_WAIT_LINE, self.lines)

    def assert_not_a_start(self, status, data):
        subject = self.watcher()
        since = self.arrive(subject)
        self.game.answer = (status, data)
        for second in range(1, 11):
            self.step_at(subject, since + second)
        self.assertEqual((len(self.beeps), self.pings, self.game.count()), (1, [], 11))
        self.assertEqual(subject.snapshot(), self.shown("InProgress", "loading"))
        self.assertIsNone(client.game_clock(self.game.base))

    def test_a_404_is_not_a_start(self):
        # Mutation: the status not read. Red: the 404's gameTime pings.
        self.assert_not_a_start(404, body({"gameTime": 50.0}))

    def test_a_body_that_is_not_json_is_not_a_start(self):
        # Mutation: ValueError not caught. Red: the decode error raises out of the step.
        self.assert_not_a_start(200, b"gameTime 50")

    def test_a_json_list_is_not_a_start(self):
        # Mutation: the object check removed. Red: a list raises AttributeError out of the step.
        self.assert_not_a_start(200, body([{"gameTime": 50.0}]))

    def test_an_object_without_game_time_is_not_a_start(self):
        # Mutation: the number check removed. Red: float(None) raises TypeError out of the step.
        self.assert_not_a_start(200, body({"time": 50.0}))

    def test_game_time_as_a_string_is_not_a_start(self):
        # Mutation: the number check removed. Red: the string "50.0" is read as 50.0 and pings.
        self.assert_not_a_start(200, body({"gameTime": "50.0"}))

    def test_game_time_true_is_not_a_start(self):
        # Mutation: the bool exclusion removed. Red: true is an int above zero and pings.
        self.assert_not_a_start(200, body({"gameTime": True}))

    def test_the_game_clock_never_raises_and_answers_a_number_or_none(self):
        # Mutation: the RecursionError, or the OverflowError, not caught. Red: it raises out of game_clock.
        seen = []

        def get(url, token, timeout):
            seen.append((url, token, timeout))
            return answers.pop(0)

        answers = [(200, body({"gameTime": 3, "other": "x"})),  # the clock alone, as a float
                   (200, b"[" * 5000),  # nested past the parser's depth
                   (200, b'{"gameTime": 1' + b"0" * 400 + b"}"),  # an int no float holds
                   (200, b"\xff\xfe"),  # not UTF-8
                   (204, b"")]
        self.assertIs(inspect.signature(client.game_clock).parameters["get"].default, client.get)
        answer = client.game_clock("http://127.0.0.1:9", get=get)  # an injected get: no request leaves the test
        self.assertEqual((answer, type(answer)), (3.0, float))
        for _ in range(4):
            self.assertIsNone(client.game_clock("http://127.0.0.1:9", get=get))
        self.assertEqual(seen, [("http://127.0.0.1:9" + client.LIVE_CLOCK_PATH, None, client.LIVE_TIMEOUT)] * 5)

        def unreachable(url, token, timeout):
            raise client.ClientUnreachable("RemoteDisconnected")

        self.assertIsNone(client.game_clock("http://127.0.0.1:9", get=unreachable))
        self.assertIsNone(client.game_clock(self.game.base))  # the port closes with no answer
        self.assertEqual(self.game.count(), 1)

    def test_the_wait_fires_the_start_at_120_s_of_in_progress_with_no_clock_and_not_at_119_s(self):
        # Mutation: the wait made 121 s. Red: no ping at 120 s.
        subject = self.watcher()
        since = self.arrive(subject)
        for second in range(1, 120):
            self.step_at(subject, since + second)
        self.assertEqual((len(self.beeps), self.pings, self.game.count()), (1, [], 120))
        self.assertEqual(subject.snapshot(), self.shown("InProgress", "loading"))
        self.step_at(subject, since + 120.0)
        self.assertEqual((len(self.beeps), self.pings), (2, [STARTED_PING]))
        self.assertEqual(subject.snapshot(), self.shown("InProgress", "started", "sent"))
        self.assertEqual((self.lines.count(watcher.STARTED_ON_WAIT_LINE), self.lines.count(watcher.STARTED_LINE)),
                         (1, 0))
        asks = self.game.count()
        self.game.clock(CLOCK)
        for second in range(121, 300, 2):
            self.step_at(subject, since + second)
        self.assertEqual((len(self.beeps), len(self.pings), self.game.count()), (2, 1, asks))

    def test_a_clock_of_zero_on_every_read_never_fires_the_wait_and_a_clock_above_zero_is_then_the_start(self):
        # Mutation: the restart on a number not above zero dropped. Red: the ping on the wait at 120 s.
        self.game.clock(0)
        subject = self.watcher()
        since = self.arrive(subject)
        for second in range(1, 301):
            self.step_at(subject, since + second)
        self.assertEqual((len(self.beeps), self.pings, self.game.count()), (1, [], 301))
        self.assertEqual(subject.snapshot(), self.shown("InProgress", "loading"))
        self.assertNotIn(watcher.STARTED_ON_WAIT_LINE, self.lines)
        self.game.clock(CLOCK)
        self.step_at(subject, since + 301)
        self.assertEqual((len(self.beeps), self.pings), (2, [STARTED_PING]))
        self.assertEqual((self.lines.count(watcher.STARTED_ON_WAIT_LINE), self.lines.count(watcher.STARTED_LINE)),
                         (0, 1))

    def assert_the_wait_runs_from_the_last_number(self, seconds):
        """The clock answering `seconds` until second 100, then no clock: the wait fires at 220 s, not at 219 s."""
        self.game.clock(seconds)
        subject = self.watcher()
        since = self.arrive(subject)
        for second in range(1, 101):
            self.step_at(subject, since + second)
        self.game.answer = None
        for second in range(101, 220):
            self.step_at(subject, since + second)
        self.assertEqual((len(self.beeps), self.pings, self.game.count()), (1, [], 220))
        self.assertNotIn(watcher.STARTED_ON_WAIT_LINE, self.lines)
        self.step_at(subject, since + 220.0)
        self.assertEqual((len(self.beeps), self.pings), (2, [STARTED_PING]))
        self.assertEqual((self.lines.count(watcher.STARTED_ON_WAIT_LINE), self.lines.count(watcher.STARTED_LINE)),
                         (1, 0))

    def test_the_wait_runs_from_the_last_clock_of_zero_and_fires_120_s_after_it_with_no_clock(self):
        # Mutation: the restart on a number not above zero dropped. Red: the ping on the wait at 120 s.
        self.assert_the_wait_runs_from_the_last_number(0)

    def test_a_negative_clock_restarts_the_wait_as_zero_does(self):
        # Mutation: the restart on zero only (== 0). Red: the ping on the wait at 120 s.
        self.assert_the_wait_runs_from_the_last_number(-0.5)

    def test_the_wait_fires_only_on_a_step_whose_phase_read_was_in_progress(self):
        # Mutation: the wait firing on any phase. Red: the ping at 120 s while the phase read is Reconnect.
        subject = self.watcher()
        since = self.arrive(subject)
        self.step_at(subject, since + 120.0, "Reconnect")
        self.step_at(subject, since + 121.0, "Reconnect")
        self.assertEqual((len(self.beeps), self.pings), (1, []))
        self.step_at(subject, since + 122.0)
        self.assertEqual((len(self.beeps), self.pings), (2, [STARTED_PING]))
        self.assertIn(watcher.STARTED_ON_WAIT_LINE, self.lines)

    def test_a_boundary_phase_during_the_watch_ends_it_with_no_alert_until_the_next_arrival(self):
        # Mutation: the boundary leaving the watch on. Red: the game's port asked after the boundary, and a ping.
        for boundary in sorted(watcher.GAME_BOUNDARY_PHASES - {watcher.READY_CHECK}):
            with self.subTest(boundary=boundary):
                self.beeps.clear()
                self.pings.clear()
                self.game.requests.clear()
                self.game.answer = None
                subject = self.watcher()
                since = self.arrive(subject)
                self.step_at(subject, since + 1.0)
                self.step_at(subject, since + 2.0, boundary)
                self.assertEqual(self.game.count(), 2)
                self.game.clock(CLOCK)
                for second in range(3, 200, 10):
                    self.step_at(subject, since + second, boundary)
                self.assertEqual((len(self.beeps), self.pings, self.game.count()), (1, [], 2))
                self.assertEqual(subject.snapshot(), self.shown(boundary, "loading"))
                self.step_at(subject, since + 201.0)  # the next arrival asks at once, and the clock runs
                self.assertEqual((len(self.beeps), self.pings, self.game.count()), (3, [STARTED_PING], 3))

    def test_the_watch_outlives_a_lost_client_and_a_clock_above_zero_is_the_start(self):
        # Mutation: _forget ending the watch. Red: no ask and no ping once the client is lost.
        credentials = Credentials(self.fake.port)
        subject = self.watcher(credentials=credentials)
        since = self.arrive(subject)
        self.fake.dropping = True
        self.step_at(subject, since + 1.0)
        self.assertEqual((self.lines.count(watcher.LOST_LINE), subject.snapshot()["client"]), (1, "waiting"))
        credentials.port = None
        self.step_at(subject, since + 4.0)
        self.assertEqual((len(self.beeps), self.pings, self.game.count()), (1, [], 3))
        self.game.clock(CLOCK)
        self.step_at(subject, since + 7.0)
        self.assertEqual((len(self.beeps), self.pings), (2, [STARTED_PING]))
        self.assertEqual(subject.snapshot(), self.shown(None, "started", "sent", client_state="waiting"))

    def test_a_fresh_client_whose_first_read_is_in_progress_leaves_a_running_watch_running(self):
        # Mutation: a first read of InProgress ending the watch. Red: no ping on the wait.
        credentials = Credentials(self.fake.port)
        subject = self.watcher(credentials=credentials)
        since = self.arrive(subject)
        self.fake.dropping = True
        self.step_at(subject, since + 1.0)
        self.fake = self.client_fake(phase="InProgress")
        credentials.port = self.fake.port
        for second in range(4, 120, 5):
            self.step_at(subject, since + second)
        self.assertEqual((len(self.beeps), self.pings, self.lines.count(watcher.CONNECTED_LINE)), (1, [], 2))
        self.step_at(subject, since + 120.0)
        self.assertEqual((len(self.beeps), self.pings), (2, [STARTED_PING]))
        self.assertIn(watcher.STARTED_ON_WAIT_LINE, self.lines)

    def test_a_client_lost_for_the_whole_wait_ends_the_watch_with_no_alert(self):
        # Mutation: the wait firing with no client. Red: a ping at 120 s while no client answers.
        credentials = Credentials(self.fake.port)
        subject = self.watcher(credentials=credentials)
        since = self.arrive(subject)
        credentials.port, self.fake.dropping = None, True
        for second in range(1, 120, 3):
            self.step_at(subject, since + second)
        self.step_at(subject, since + 120.0)
        self.assertEqual((len(self.beeps), self.pings), (1, []))
        asks = self.game.count()
        self.game.clock(CLOCK)
        for second in range(123, 300, 3):
            self.step_at(subject, since + second)
        self.fake = self.client_fake(phase="InProgress")  # the game met again under way: no watch, no alert
        credentials.port = self.fake.port
        for second in range(300, 320, 2):
            self.step_at(subject, since + second)
        self.assertEqual((len(self.beeps), self.pings, self.game.count()), (1, [], asks))
        self.assertEqual(subject.snapshot(), self.shown("InProgress", "loading"))

    def test_a_first_read_of_in_progress_or_reconnect_never_calls_the_game_s_port(self):
        # Mutation: the first-read branch starting a watch. Red: the game's port counts requests.
        self.game.clock(CLOCK)
        for first in ("InProgress", "Reconnect"):
            with self.subTest(first=first):
                subject = self.watcher()
                self.step_at(subject, self.clock() + 0.3, first)
                for _ in range(30):  # 210 s of InProgress, past the wait
                    self.step_at(subject, self.clock() + 7)
                self.assertEqual((self.beeps, self.pings, self.game.count()), ([], [], 0))

    def test_the_game_s_port_is_asked_at_most_once_a_second_only_inside_a_watch_and_never_in_the_hold(self):
        # Mutation: the poll pause dropped. Red: one request per 0.3 s step.
        subject = self.watcher(accept=False)
        self.read(subject, "Lobby", "ChampSelect")
        self.assertEqual(self.game.count(), 0)
        asked = []
        for _ in range(40):  # 12 s of InProgress, a step every 0.3 s
            before = self.game.count()
            self.read(subject, "InProgress")
            self.assertLessEqual(self.game.count() - before, 1)
            if self.game.count() > before:
                asked.append(self.clock())
        gaps = [later - earlier for earlier, later in zip(asked, asked[1:])]
        self.assertEqual(len(asked), 10)
        self.assertTrue(all(1.0 <= gap < 1.3 for gap in gaps), gaps)
        self.read(subject, "ReadyCheck")  # a boundary, then the 15 s hold of the accept off
        held_from, asks = self.clock(), self.game.count()
        self.steps_until(subject, held_from + 15.0)
        self.read(subject, "Lobby", "Matchmaking")
        self.assertEqual((self.game.count(), [kind for *_, kind in self.pings]), (asks, [QUEUE_FOUND]))

    def test_the_request_to_the_game_s_port_carries_no_authorization_and_reads_the_clock_path_only(self):
        # Mutation: the token passed to the game's port. Red: an Authorization header on the game's port.
        subject = self.watcher()
        since = self.arrive(subject)
        for second in range(1, 5):
            self.step_at(subject, since + second)
        self.game.clock(CLOCK)
        self.step_at(subject, since + 5)
        self.assertEqual(len(self.pings), 1)
        self.assertEqual(len(self.game.requests), 6)
        self.assertEqual({(call["method"], call["path"]) for call in self.game.requests},
                         {("GET", client.LIVE_CLOCK_PATH)})
        self.assertEqual([call for call in self.game.requests if "authorization" in call["headers"]], [])
        self.assertIn("authorization", self.fake.requests[-1]["headers"])  # the client's own port keeps its token

    def test_nothing_printed_logged_or_raised_holds_the_clock_a_port_the_token_the_secret_or_the_link_id(self):
        # Mutation: the clock's value in the started line. Red: 4321 in a console line.
        out, err, raised = io.StringIO(), io.StringIO(), []
        credentials = Credentials(self.fake.port)
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            subject = self.watcher(credentials=credentials)
            since = self.arrive(subject)  # the clock road
            self.game.clock(CLOCK)
            self.step_at(subject, since + 1.0)
            self.game.answer = None
            self.read(subject, "EndOfGame", "Lobby", "InProgress")  # the wait road
            since = self.clock()
            self.step_at(subject, since + 120.0)
            self.read(subject, "EndOfGame", "Lobby", "InProgress")  # the lost road
            since, credentials.port, self.fake.dropping = self.clock(), None, True
            for second in range(3, 124, 3):
                self.step_at(subject, since + second)
            try:
                client.get(self.game.base + client.LIVE_CLOCK_PATH, None, client.LIVE_TIMEOUT)
            except client.ClientUnreachable as failure:
                raised.append(f"{failure!r} {failure}")
        self.assertEqual([kind for *_, kind in self.pings], [MATCH_STARTED, MATCH_STARTED])
        for line in (watcher.LOADING_LINE, watcher.STARTED_LINE, watcher.STARTED_ON_WAIT_LINE, watcher.LOST_LINE):
            self.assertIn(line, self.lines)
        self.assertEqual(len(raised), 1)
        shown = [*self.lines, out.getvalue(), err.getvalue(), *raised, repr(subject.snapshot())]
        for value in (TOKEN, SECRET, LINK_ID, "4321", str(self.game.port), str(self.fake.port), str(client.LIVE_PORT)):
            for text in shown:
                self.assertNotIn(value, text)
        for line in self.lines:
            self.assertIsNone(GAME_WORDS.search(line), line)

    def test_the_names_and_values_of_the_start_read_from_the_game_s_log(self):
        # Mutation: the grace made 60 s, or a watcher built with no reader. Red: the values differ.
        self.assertEqual(watcher.LIVE_LOG_GRACE_SECONDS, 30.0)
        self.assertIsNone(inspect.signature(watcher.Watcher).parameters["game_log"].default)
        built = watcher.Watcher(Credentials(self.fake.port), self.alerter())
        self.assertIs(type(built._game_log), client.GameLogStart)  # None builds the reader of the game's own log

    def test_a_game_with_no_clock_whose_log_shows_a_start_line_starts_on_that_turn_not_on_the_wait(self):
        # (h) Mutation: the log not asked. Red: the alert at 120 s, on the wait.
        log = FakeGameLog(False, False, True)
        subject = self.watcher(game_log=log)
        since = self.arrive(subject)  # the first due turn
        fired = []
        for second in range(1, 131):
            self.step_at(subject, since + second)
            if self.pings and not fired:
                fired.append(second)
        self.assertEqual(fired, [2])  # the third due turn: the arrival's, then 1 s and 2 s after it
        self.assertEqual((len(self.beeps), self.pings, len(log.asks), self.game.count()), (2, [STARTED_PING], 3, 3))
        self.assertEqual(subject.snapshot(), self.shown("InProgress", "started", "sent"))
        self.assertEqual(self.lines, [watcher.CONNECTED_LINE, watcher.LOADING_LINE, watcher.STARTED_LINE,
                                      "alert: sent to the phone"])

    def test_the_log_is_asked_from_the_wall_time_of_the_loading_less_30_s_on_every_due_turn(self):
        # (i) Mutation: the wall read at each ask, or the grace added. Red: the since moves, or is 60 s later.
        log = FakeGameLog()
        subject = self.watcher(game_log=log, wall=lambda: WALL + self.clock())
        since = self.arrive(subject)
        for second in (1, 2, 3):
            self.step_at(subject, since + second)
        self.assertEqual(log.calls, ["reset", *[WALL + since - 30.0] * 4])

    def test_a_clock_above_zero_starts_the_match_first_and_the_log_is_not_asked_on_that_turn(self):
        # (j) Mutation: the log asked before the clock. Red: an ask on the clock's turn.
        log = FakeGameLog(True, True)
        self.game.clock(CLOCK)
        subject = self.watcher(game_log=log)
        self.arrive(subject)
        self.assertEqual((len(self.beeps), self.pings, log.calls), (2, [STARTED_PING], ["reset"]))
        self.assertEqual(self.lines.count(watcher.STARTED_LINE), 1)

    def test_a_clock_at_or_below_zero_restarts_the_wait_and_the_log_is_still_asked_on_that_turn(self):
        # (j) Mutation: the log asked only when the clock gave no number. Red: no ask on a clock of zero.
        log = FakeGameLog(False, True)
        self.game.clock(0)
        subject = self.watcher(game_log=log)
        since = self.arrive(subject)
        self.assertEqual((self.pings, len(log.asks), self.game.count()), ([], 1, 1))
        self.step_at(subject, since + 1.0)
        self.assertEqual((self.pings, len(log.asks), self.game.count()), ([STARTED_PING], 2, 2))
        self.assertEqual((self.lines.count(watcher.STARTED_LINE), self.lines.count(watcher.STARTED_ON_WAIT_LINE)),
                         (1, 0))

    def test_a_log_that_never_shows_a_start_line_leaves_the_120_s_wait_as_it_was(self):
        # (k) Mutation: any answer of the log taken as a start. Red: a ping on the arrival's turn.
        log = JoinedGameLog()  # never joined: the wait as before the hold
        subject = self.watcher(game_log=log)
        since = self.arrive(subject)
        for half in range(1, 240):  # a step every 0.5 s up to 119.5 s: one ask per due turn, once a second
            self.step_at(subject, since + half / 2)
        self.assertEqual((len(self.beeps), self.pings, len(log.asks), self.game.count()), (1, [], 120, 120))
        self.assertEqual(subject.snapshot(), self.shown("InProgress", "loading"))
        self.step_at(subject, since + 120.0)
        self.assertEqual((len(self.beeps), self.pings, len(log.asks)), (2, [STARTED_PING], 121))
        self.assertEqual(subject.snapshot(), self.shown("InProgress", "started", "sent"))
        self.assertEqual((self.lines.count(watcher.STARTED_ON_WAIT_LINE), self.lines.count(watcher.STARTED_LINE)),
                         (1, 0))

    def test_a_pause_during_the_watch_resets_the_log_reader_and_asks_nothing_while_paused(self):
        # (l) Mutation: _rest leaving the reader as it was. Red: no reset after the pause.
        log, stop, turns = FakeGameLog(), threading.Event(), []

        def sleep(seconds):
            self.clock.advance(seconds)
            turns.append(seconds)
            if len(turns) == 500:  # 150 s of paused turns, past the wait
                stop.set()

        subject = self.watcher(game_log=log, sleep=sleep, stop=stop)
        since = self.arrive(subject)
        self.step_at(subject, since + 1.0)
        self.assertEqual(log.calls, ["reset", WALL - 30.0, WALL - 30.0])
        subject.pause()
        subject.run()
        self.assertEqual(len(turns), 500)
        self.assertEqual(log.calls, ["reset", WALL - 30.0, WALL - 30.0, "reset"])
        self.assertEqual((len(self.beeps), self.pings, self.game.count()), (1, [], 2))
        self.assertIn(watcher.PAUSED_LINE, self.lines)

    def test_a_second_game_s_loading_resets_the_log_reader_before_its_first_ask(self):
        # (m) Mutation: the reset dropped from _loading. Red: the second game asks the reader where the first left it.
        log = FakeGameLog()
        subject = self.watcher(game_log=log)
        since = self.arrive(subject)
        self.step_at(subject, since + 1.0)
        self.read(subject, "EndOfGame", "Lobby", "ChampSelect", "InProgress")
        self.assertEqual(log.calls, ["reset", WALL - 30.0, WALL - 30.0, "reset", WALL - 30.0])
        self.assertEqual((len(self.beeps), self.pings), (2, []))  # two loading screens, no start

    def step_joined(self, subject, log, since, seconds):
        """A step once a second at each of `seconds` after the loading screen, InProgress read; the log shows the
        game's join from second 9 on, a game whose start comes up to 140 s after its join."""
        for second in seconds:
            if second == 9:
                log.joined = True
            self.step_at(subject, since + second)

    def test_a_game_whose_log_shows_its_join_holds_the_wait_at_120_s(self):
        # (p) Mutation: the hold dropped. Red: the ping on the wait at 120 s.
        log = JoinedGameLog()
        subject = self.watcher(game_log=log)
        since = self.arrive(subject)
        self.step_joined(subject, log, since, range(1, 121))
        self.assertIs(log.joined, True)
        self.assertEqual((len(self.beeps), self.pings, len(log.asks), self.game.count()), (1, [], 121, 121))
        self.assertEqual(subject.snapshot(), self.shown("InProgress", "loading"))
        self.assertEqual(self.lines, [watcher.CONNECTED_LINE, watcher.LOADING_LINE])

    def test_a_joined_game_whose_log_shows_a_start_line_at_140_s_starts_on_that_turn(self):
        # (q) Mutation: the hold placed before the ask of the log. Red: no ping at 140 s.
        log = JoinedGameLog()
        subject = self.watcher(game_log=log)
        since = self.arrive(subject)
        self.step_joined(subject, log, since, range(1, 140))
        self.assertEqual((self.pings, len(log.asks)), ([], 140))
        log.answers.append(True)  # the next ask, at 140 s, finds a start line
        self.step_at(subject, since + 140)
        self.assertEqual((len(self.beeps), self.pings, len(log.asks)), (2, [STARTED_PING], 141))
        self.assertEqual(subject.snapshot(), self.shown("InProgress", "started", "sent"))
        self.assertEqual(self.lines, [watcher.CONNECTED_LINE, watcher.LOADING_LINE, watcher.STARTED_LINE,
                                      "alert: sent to the phone"])
        for second in range(141, 400, 7):  # past the ceiling: nothing more for that game
            self.step_at(subject, since + second)
        self.assertEqual((len(self.beeps), len(self.pings), len(log.asks)), (2, 1, 141))

    def test_a_joined_game_that_never_starts_is_announced_on_the_wait_at_300_s_from_the_loading(self):
        # (r) Mutation: the ceiling made 301 s. Red: no ping at 300 s.
        self.assertEqual(watcher.LIVE_JOINED_CEILING_SECONDS, 300.0)
        log = JoinedGameLog()
        subject = self.watcher(game_log=log)
        since = self.arrive(subject)
        self.step_joined(subject, log, since, range(1, 300))
        self.assertEqual((len(self.beeps), self.pings, len(log.asks)), (1, [], 300))
        self.assertEqual(subject.snapshot(), self.shown("InProgress", "loading"))
        self.step_at(subject, since + 300.0)
        self.assertEqual((len(self.beeps), self.pings, len(log.asks)), (2, [STARTED_PING], 301))
        self.assertEqual(subject.snapshot(), self.shown("InProgress", "started", "sent"))
        self.assertEqual((self.lines.count(watcher.STARTED_ON_WAIT_LINE), self.lines.count(watcher.STARTED_LINE)),
                         (1, 0))

    def test_a_clock_of_zero_in_the_hold_restarts_the_wait_and_the_ceiling_stays_300_s_from_the_loading(self):
        # (s) Mutation: the ceiling counted from the last restart of the wait. Red: no ping at 300 s.
        log = JoinedGameLog()
        subject = self.watcher(game_log=log)
        since = self.arrive(subject)
        self.step_joined(subject, log, since, range(1, 130))
        self.game.clock(0)  # one clock of zero at 130 s: the wait restarts from there, due at 250 s
        self.step_at(subject, since + 130)
        self.game.answer = None
        self.step_joined(subject, log, since, range(131, 300))
        self.assertEqual((len(self.beeps), self.pings, len(log.asks), self.game.count()), (1, [], 300, 300))
        self.assertNotIn(watcher.STARTED_ON_WAIT_LINE, self.lines)
        self.step_at(subject, since + 300.0)
        self.assertEqual((len(self.beeps), self.pings), (2, [STARTED_PING]))
        self.assertEqual((self.lines.count(watcher.STARTED_ON_WAIT_LINE), self.lines.count(watcher.STARTED_LINE)),
                         (1, 0))


class AlerterSoundTest(WatcherFixture, unittest.TestCase):
    def test_sound_beeps_once_and_sends_nothing_queues_nothing_and_leaves_the_last_ping(self):
        # Mutation: sound() as the alert of match_started. Red: a ping thread started and a ping queued.
        started = []
        subject = self.alerter(start=started.append)
        subject.sound()
        self.assertEqual((len(self.beeps), self.pings, started, self.lines), (1, [], [], []))
        self.assertEqual(subject.last_ping(), (None, None))
        self.assertTrue(subject.flush(0.0))


if __name__ == "__main__":
    unittest.main()
