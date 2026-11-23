"""The League client and the TFT app open together: the one where the queue was launched is followed (lines of a
real session, test_tft_app.LINES, written to a scratch file)."""
import json
import unittest

import support
from test_tft_app import LINES, POP_AT

both_clients = support.module("both_clients")
client = support.module("client")
tft_app = support.module("tft_app")


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

        self.either = both_clients.EitherClient(self.league, self.app, get=get, addresses=client.loopback_addresses)

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
