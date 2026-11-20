"""The standalone TFT app read from its own log: the lines of a real session on macOS, written to a scratch file."""
import calendar
import time
import unittest

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


class EitherClientTest(unittest.TestCase):
    def test_the_league_client_comes_first_and_the_app_only_while_it_runs(self):
        league = type("L", (), {"value": None, "read": lambda self: self.value})()
        app = type("P", (), {"on": False, "running": lambda self: self.on})()
        either = tft_app.EitherClient(league, app)
        self.assertIsNone(either.read())
        app.on = True
        self.assertIs(either.read(), app)
        league.value = "league"
        self.assertEqual(either.read(), "league")


if __name__ == "__main__":
    unittest.main()
