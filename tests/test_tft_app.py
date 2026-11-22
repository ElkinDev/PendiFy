"""The standalone TFT app read from its own log: the lines of a real session on macOS, written to a scratch file."""
import calendar
import json
import time
import unittest
from unittest import mock

import support

client = support.module("client")
tft_app = support.module("tft_app")
watcher = support.module("watcher")

POP_AT = calendar.timegm((2026, 11, 20, 15, 47, 51, 0, 0, 0)) + 0.456
LINES = {
    "lobby": b"[2026.11.20-15.25.13:163][ 12]TFT: [Standalone] TFTLobbySubsystem_1: Entering LobbyState[ETFTLobbyState::SocialMode]\n",
    "queue": b"[2026.11.20-15.25.15:741][ 13]TFT: [Standalone] TFTLobbySubsystem_1: Entering LobbyState[ETFTLobbyState::Matchmaking]\n",
    "pop": b"[2026.11.20-15.47.51:456][352]TFT: [Standalone] TFTLobbySubsystem_1: Match found, showing AFK Check Modal\n",
    "accepted": b"[2026.11.20-15.47.54:918][528]TFT: [Standalone] TFTLobbySubsystem_1: Accepted match\n",
    "loading": b"[2026.11.20-15.48.00:530][ 40]LogNet: Welcomed by server (Level: /Game/Maps/Empty, Game: /Mode)\n",
    "ingame": b"[2026.11.20-15.48.45:302][ 41]TFT: [Client] TFTLobbySubsystem_1: Entering LobbyState[ETFTLobbyState::InGame]\n",
    "end": b"[2026.11.20-16.19.27:600][464]TFT: [Standalone] TFTLobbySubsystem_1: Entering LobbyState[ETFTLobbyState::EndOfGameSummary]\n",
}


class AppSessionTest(unittest.TestCase):
    def setUp(self):
        tmp = support.temp_dir()
        self.addCleanup(tmp.cleanup)
        self.path = f"{tmp.name}/TFT.log"
        open(self.path, "wb").close()
        self.alive, self.wall, self.mono = True, POP_AT, 0.0
        self.session = tft_app.AppSession(path=lambda: self.path, running=lambda: self.alive,
                                          clock=lambda: self.mono, wall=lambda: self.wall)

    def write(self, *names):
        with open(self.path, "ab") as log:
            log.write(b"".join(LINES[name] for name in names))

    def test_each_step_of_a_session_reads_as_the_client_s_phase(self):
        self.assertEqual(self.session.phase(), "Lobby")
        for names, phase in ((("lobby",), "Lobby"), (("queue",), "Matchmaking"), (("pop",), "ReadyCheck"),
                             (("accepted",), "Matchmaking"), (("loading",), "InProgress"),
                             (("ingame",), "InProgress"), (("end",), "EndOfGame")):
            with self.subTest(names=names):
                self.write(*names)
                self.assertEqual(self.session.phase(), phase)

    def test_a_pop_accepted_between_two_reads_is_said_once(self):
        self.write("queue", "pop", "accepted")
        self.assertEqual(self.session.phase(), "ReadyCheck")
        self.assertEqual(self.session.phase(), "Matchmaking")

    def test_an_old_pop_is_no_ready_check(self):
        self.wall = POP_AT + tft_app.READY_CHECK_SECONDS + 1
        self.write("queue", "pop")
        self.assertEqual(self.session.phase(), "Matchmaking")

    def test_a_new_log_is_read_from_its_start_and_a_closed_app_is_lost(self):
        self.write("queue", "loading")
        self.assertEqual(self.session.phase(), "InProgress")
        with open(self.path, "wb") as log:  # the app started again: a new, shorter log
            log.write(LINES["lobby"])
        self.assertEqual(self.session.phase(), "Lobby")
        self.alive = False
        self.mono += tft_app.PROCESS_CHECK_SECONDS
        with self.assertRaises(client.ClientUnreachable):
            self.session.phase()

    def test_a_partial_last_line_waits_for_its_end(self):
        line = LINES["queue"]
        with open(self.path, "ab") as log:
            log.write(line[:40])
        self.assertEqual(self.session.phase(), "Lobby")
        with open(self.path, "ab") as log:
            log.write(line[40:])
        self.assertEqual(self.session.phase(), "Matchmaking")


class ManualAcceptTest(unittest.TestCase):
    """A client that cannot accept: the pop alerts the phone at once, nothing is posted, then the hold."""

    def test_the_pop_alerts_the_queue_and_posts_nothing(self):
        phases = iter(["Matchmaking", "ReadyCheck"])
        found = type("App", (), {"accepts": False, "port": 0, "token": None, "phase": lambda self: next(phases)})()
        alerts, lines = [], []
        alert = type("A", (), {"__call__": lambda self, kind: alerts.append(kind), "sound": lambda self: None,
                               "last_ping": lambda self: (None, None)})()
        subject = watcher.Watcher(type("C", (), {"read": lambda self: found})(), alert,
                                  post=lambda *a: self.fail("posted an accept"), sleep=lambda s: None,
                                  log=lines.append, clock=lambda: 0.0, wall=lambda: 0.0)
        subject.step()
        subject.step()
        self.assertEqual(alerts, [watcher.QUEUE_FOUND])
        self.assertIn(watcher.MANUAL_LINE, lines)
        self.assertEqual(subject.step(), watcher.STEP_PAUSE)  # the hold


class BothClientsTest(unittest.TestCase):
    """The League client and the TFT app open together: the one where the queue was launched is followed."""

    def setUp(self):
        tmp = support.temp_dir()
        self.addCleanup(tmp.cleanup)
        self.path = f"{tmp.name}/TFT.log"
        with open(self.path, "wb") as log:
            log.write(LINES["lobby"])
        self.app_on, self.league_phase = True, None
        self.app = tft_app.AppSession(path=lambda: self.path, running=lambda: self.app_on, clock=lambda: 0.0,
                                      wall=lambda: POP_AT)
        self.creds = client.Credentials(4242, "token")
        self.league = type("L", (), {"read": lambda s: self.creds if self.league_phase is not None else None})()

        def get(url, token, timeout):
            self.assertEqual((url, token), ("http://127.0.0.1:4242" + client.PHASE_PATH, "token"))
            return 200, json.dumps(self.league_phase).encode()

        self.either = tft_app.EitherClient(self.league, self.app, get=get, addresses=client.loopback_addresses)

    def write(self, *names):
        with open(self.path, "ab") as log:
            log.write(b"".join(LINES[name] for name in names))

    def test_a_queue_in_league_is_followed_with_the_app_open_and_accepted_by_the_api(self):
        self.league_phase = "Lobby"
        both = self.either.read()
        self.assertEqual(both.phase(), "Lobby")
        self.league_phase = "ReadyCheck"
        self.assertEqual(both.phase(), "ReadyCheck")
        self.assertEqual((both.active, both.accepts, both.port, both.token), ("league", True, 4242, "token"))
        self.assertFalse(both.auto_accept_on())

    def test_a_queue_in_the_app_is_followed_with_league_open_and_accepted_by_the_click(self):
        self.league_phase = "Lobby"
        both = self.either.read()
        self.write("queue", "pop")
        self.assertEqual(both.phase(), "ReadyCheck")
        self.assertEqual((both.active, both.accepts), ("app", False))

    def test_either_alone_and_neither_lost(self):
        both = self.either.read()  # the app alone
        self.assertEqual((both.phase(), both.active), ("Lobby", "app"))
        self.app_on = False
        self.app._checked_at = None
        self.assertIsNone(self.either.read())
        with self.assertRaises(client.ClientUnreachable):
            both.phase()
        self.league_phase = "Matchmaking"  # League alone
        self.assertIs(self.either.read(), both)
        self.assertEqual((both.phase(), both.active), ("Matchmaking", "league"))


if __name__ == "__main__":
    unittest.main()


class AutoAcceptTest(unittest.TestCase):
    """The click on accept after a pop: clicked until the log says accepted, never once the check is over."""

    def setUp(self):
        tmp = support.temp_dir()
        self.addCleanup(tmp.cleanup)
        self.path = f"{tmp.name}/TFT.log"
        open(self.path, "wb").close()
        self.wall, self.clicks, self.on = POP_AT, [], True
        self.answer, self.jitter, self.jitters, self.click_seconds = None, 0.0, 0, 0.0

        def click():
            self.clicks.append(self.wall)
            self.wall += self.click_seconds
            if self.answer is None:  # the game takes the click: its log says accepted
                with open(self.path, "ab") as log:
                    log.write(LINES["accepted"])
            return self.answer

        def sleep(seconds):
            self.wall += seconds

        def jitter():
            self.jitters += 1
            return self.jitter

        self.session = tft_app.AppSession(path=lambda: self.path, running=lambda: True, clock=lambda: 0.0,
                                          wall=lambda: self.wall, autoaccept=lambda: self.on,
                                          clicker=type("K", (), {"click": lambda s: click()})(), sleep=sleep,
                                          jitter=jitter)

    def offsets(self):
        """Each click's time after the pop, to the millisecond."""
        return [round(at - POP_AT, 3) for at in self.clicks]

    def pop(self):
        with open(self.path, "ab") as log:
            log.write(LINES["queue"] + LINES["pop"])
        self.assertEqual(self.session.phase(), "ReadyCheck")

    def test_one_click_that_takes_is_accepted(self):
        self.pop()
        self.assertTrue(self.session.auto_accept_on())
        self.assertIs(self.session.auto_accept(), True)
        self.assertEqual(len(self.clicks), 1)

    def test_a_click_that_does_not_take_is_made_again_inside_the_check_only(self):
        self.answer = tft_app.NOT_ACCEPTED
        self.pop()
        self.assertEqual(self.session.auto_accept(), tft_app.NOT_ACCEPTED)
        self.assertEqual(len(self.clicks), tft_app.ACCEPT_ATTEMPTS)
        self.assertTrue(all(at - POP_AT <= tft_app.READY_CHECK_SECONDS for at in self.clicks))

    def test_each_click_waits_a_jitter_more_and_at_its_most_all_three_and_the_last_check_fall_inside_the_check(self):
        # Mutation: no jitter. Red: clicks at 1, 3 and 5 s. Mutation: the jitter once, at the first click only. Red: 3,
        # 5 and 7 s. Mutation: the jitter added to the last check as well. Red: four jitters, the end at 15 s.
        self.assertEqual((tft_app.ACCEPT_FIRST_WAIT, tft_app.ACCEPT_RETRY_WAIT, tft_app.ACCEPT_JITTER_SECONDS,
                          tft_app.ACCEPT_ATTEMPTS, tft_app.READY_CHECK_SECONDS), (1.0, 2.0, 2.0, 3, 15.0))
        self.answer = tft_app.NOT_ACCEPTED
        for jitter, clicks in ((0.0, [1.0, 3.0, 5.0]), (0.457, [1.457, 3.914, 6.371]), (2.0, [3.0, 7.0, 11.0])):
            with self.subTest(jitter=jitter):
                self.wall, self.clicks, self.jitter, self.jitters = POP_AT, [], jitter, 0
                self.pop()
                self.assertEqual(self.session.auto_accept(), tft_app.NOT_ACCEPTED)
                self.assertEqual((self.offsets(), self.jitters), (clicks, 3))
                # The last look at the log, ACCEPT_RETRY_WAIT after the last click: 13 s at the most, inside 15.
                self.assertEqual(round(self.wall - POP_AT, 3), round(clicks[-1] + tft_app.ACCEPT_RETRY_WAIT, 3))
                self.assertLessEqual(self.wall - POP_AT, tft_app.READY_CHECK_SECONDS)

    def test_clicks_that_take_long_at_the_most_jitter_never_click_once_the_check_is_over(self):
        # Mutation: the check's end not read before a click. Red: a third click 17 s after the pop.
        self.answer, self.jitter, self.click_seconds = tft_app.NOT_ACCEPTED, tft_app.ACCEPT_JITTER_SECONDS, 3.0
        self.pop()
        self.assertEqual(self.session.auto_accept(), tft_app.NOT_ACCEPTED)
        self.assertEqual(self.offsets(), [3.0, 10.0])  # the third would be at 17 s, past READY_CHECK_SECONDS
        self.assertEqual(self.jitters, 3)

    def test_the_jitter_is_from_0_to_2_seconds_to_the_millisecond(self):
        # Mutation: a fixed jitter. Red: the real draws are all one value. Mutation: not rounded. Red: 1.23456789.
        # Mutation: the bound not ACCEPT_JITTER_SECONDS. Red: uniform asked for another range.
        for drawn, kept in ((0.0, 0.0), (2.0, 2.0), (1.23456789, 1.235), (0.0004, 0.0)):
            with self.subTest(drawn=drawn):
                with mock.patch.object(tft_app.random, "uniform", return_value=drawn) as uniform:
                    self.assertEqual(tft_app.accept_jitter(), kept)
                uniform.assert_called_once_with(0, tft_app.ACCEPT_JITTER_SECONDS)
        draws = [tft_app.accept_jitter() for _ in range(2000)]
        self.assertTrue(all(0 <= jitter <= tft_app.ACCEPT_JITTER_SECONDS for jitter in draws))
        self.assertTrue(all(jitter == round(jitter, 3) for jitter in draws))
        self.assertGreater(len(set(draws)), 1000)  # milliseconds apart, not a handful of values
        self.assertLess(min(draws), 0.5)
        self.assertGreater(max(draws), 1.5)

    def test_no_permission_clicks_once_and_says_why(self):
        self.answer = "no_permission"
        self.pop()
        self.assertEqual(self.session.auto_accept(), "no_permission")
        self.assertEqual(len(self.clicks), 1)

    def test_a_player_who_accepted_first_gets_no_click_and_off_is_off(self):
        self.pop()
        with open(self.path, "ab") as log:
            log.write(LINES["accepted"])
        self.assertIs(self.session.auto_accept(), True)
        self.assertEqual(self.clicks, [])
        self.on = False
        self.assertFalse(self.session.auto_accept_on())


class ClickerTest(unittest.TestCase):
    """The click is made on the button's share of the app's window, and only with the app in front."""

    def setUp(self):
        self.autoclick = support.module("autoclick")

    def runner(self, front, windows='[[0,29,1496,938],[0,0,1496,29]]'):
        calls = []

        def run(command, **kwargs):
            calls.append(command)
            out = ""
            if command[-1] == "front":
                out = "ASN:0x0-0x1:\n"
            elif "bundlepath" in command:
                out = '"LSBundlePath"="/Applications/Teamfight Tactics.app"' if front() else '"LSBundlePath"="/x"'
            elif command[0] == "/usr/bin/osascript":
                out = windows
            return type("Done", (), {"returncode": 0, "stdout": out})()
        return run, calls

    def test_the_button_s_point_is_the_window_s_centre_across_and_its_share_down(self):
        self.assertEqual(self.autoclick.accept_point((0, 29, 1496, 938)), (748.0, 29 + 938 * 0.6085))
        self.assertEqual(self.autoclick.accept_point((100, 50, 1000, 600)), (600.0, 50 + 600 * 0.6085))

    def test_a_click_in_front_on_the_largest_window(self):
        run, calls = self.runner(lambda: True)
        posted = []
        clicker = self.autoclick.Clicker(run=run, sleep=lambda s: None, post=lambda x, y: posted.append((x, y)),
                                         is_trusted=lambda: True)
        self.assertIsNone(clicker.click())
        self.assertEqual(posted, [self.autoclick.accept_point((0, 29, 1496, 938))])
        self.assertNotIn(["/usr/bin/open", "-a", self.autoclick.APP_PATH], calls)

    def test_the_app_is_brought_to_the_front_first_and_never_clicked_behind(self):
        front = {"on": False}
        run, calls = self.runner(lambda: front["on"])
        clock = {"t": 0.0}

        def sleep(seconds):
            clock["t"] += seconds

        posted = []
        clicker = self.autoclick.Clicker(run=run, sleep=sleep, clock=lambda: clock["t"],
                                         post=lambda x, y: posted.append((x, y)), is_trusted=lambda: True)
        self.assertEqual(clicker.click(), self.autoclick.NOT_FRONT)
        self.assertIn(["/usr/bin/open", "-a", self.autoclick.APP_PATH], calls)
        self.assertEqual(posted, [])

    def test_no_permission_or_no_window_clicks_nothing(self):
        posted = []
        run, _ = self.runner(lambda: True)
        denied = self.autoclick.Clicker(run=run, post=lambda x, y: posted.append(1), is_trusted=lambda: False)
        self.assertEqual(denied.click(), self.autoclick.NO_PERMISSION)
        run, _ = self.runner(lambda: True, windows="[]")
        windowless = self.autoclick.Clicker(run=run, post=lambda x, y: posted.append(1), is_trusted=lambda: True)
        self.assertEqual(windowless.click(), self.autoclick.NO_WINDOW)
        self.assertEqual(posted, [])
