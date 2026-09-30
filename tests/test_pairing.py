"""PairingStateTest: the state of design P5 with a hand-moved clock and a scripted check; nothing sleeps."""
import json
import unittest
from pathlib import Path

import support
from support import LINK_ID, SECRET, FakeClock

config = support.module("config")
pairing = support.module("pairing")
worker = support.module("worker")


class PairingStateTest(unittest.TestCase):
    def setUp(self):
        tmp = support.temp_dir()
        self.addCleanup(tmp.cleanup)
        self.store = config.ConfigStore(Path(tmp.name))
        self.clock = FakeClock()
        self.answers, self.calls = [], []
        self.state = pairing.PairingState(self.store, self.check, clock=self.clock)
        self.secret = self.store.read().secret

    def check(self, secret):
        self.calls.append(secret)
        answer = self.answers.pop(0) if self.answers else worker.Refused()
        return answer() if callable(answer) else answer

    def open_tick(self, advance=0.0):
        self.clock.advance(advance)
        self.state.page_seen()
        self.state.tick()

    def test_the_constants_of_p5(self):
        # Mutation: the interval set to 20 s. Red: the constant differs from P5's 30 s.
        self.assertEqual((pairing.CHECK_INTERVAL, pairing.CHECK_CAP, pairing.RATE_LIMIT, pairing.RATE_WINDOW),
                         (30.0, 20, 2, 60.0))

    def test_checks_every_30_s_while_the_page_is_open_and_none_while_it_is_closed(self):
        # Mutation: the interval set to 20 s. Red: a second call at +29 s.
        self.state.tick()
        self.assertEqual(self.calls, [])  # the page was never seen
        self.open_tick()
        self.open_tick(29)
        self.assertEqual(self.calls, [self.secret])
        self.open_tick(1)
        self.assertEqual(self.calls, [self.secret] * 2)
        self.clock.advance(pairing.PAGE_OPEN_WINDOW + 1)
        self.state.tick()
        self.assertEqual(len(self.calls), 2)
        self.open_tick()
        self.assertEqual(len(self.calls), 3)

    def test_at_most_20_checks_then_a_pause_until_asked(self):
        # Mutation: the cap removed. Red: 25 calls in 25 ticks.
        for _ in range(25):
            self.open_tick(30)
        self.assertEqual(len(self.calls), 20)
        self.assertEqual(self.state.snapshot()["state"], "paused")
        self.clock.advance(30)
        self.assertTrue(self.state.ask())
        self.assertEqual(len(self.calls), 21)
        self.open_tick(30)
        self.assertEqual(len(self.calls), 22)
        self.assertEqual(self.state.snapshot()["state"], "waiting")

    def test_the_button_asks_at_once_but_never_more_than_twice_a_minute(self):
        # Mutation: the button bypasses the rate window. Red: a third call at +2 s.
        self.assertTrue(self.state.ask())
        self.clock.advance(1)
        self.assertTrue(self.state.ask())
        self.clock.advance(1)
        self.assertFalse(self.state.ask())
        self.assertEqual(len(self.calls), 2)
        self.assertTrue(self.state.snapshot()["buttonRefused"])
        self.open_tick(29.5)  # +31.5: the automatic check is due, and it waits for the window too
        self.assertEqual(len(self.calls), 2)
        self.open_tick(29)  # +60.5: the first call left the window
        self.assertEqual(len(self.calls), 3)
        self.assertFalse(self.state.snapshot()["buttonRefused"])
        self.clock.advance(0.5)
        self.assertTrue(self.state.ask())  # +61: the second call left the window
        self.clock.advance(1)
        self.assertFalse(self.state.ask())
        self.assertEqual(len(self.calls), 4)

    def test_throttled_is_a_wait_never_a_refusal(self):
        # Mutation: Throttled stops the polling like a terminal refusal. Red: no call 30 s later.
        self.answers = [worker.Throttled()]
        self.open_tick()
        snapshot = self.state.snapshot()
        self.assertEqual((snapshot["state"], snapshot["showCode"], snapshot["linked"]), ("wait", True, False))
        self.open_tick(30)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.state.snapshot()["state"], "waiting")

    def test_a_refusal_is_never_terminal_inside_the_first_minute_or_after(self):
        # Mutation: a Refused after the first minute stops the polling. Red: no call at +120 s.
        self.answers = [worker.Refused()] * 5 + [worker.Linked(LINK_ID)]
        self.open_tick()
        self.assertEqual(self.state.snapshot()["state"], "waiting")
        for _ in range(5):
            self.open_tick(30)
        self.assertEqual(len(self.calls), 6)
        self.assertEqual(self.store.read().link_id, LINK_ID)

    def test_linked_stores_the_link_id_and_stops_checking(self):
        # Mutation: Linked shown but not stored. Red: the file holds no link id.
        self.answers = [worker.Linked(LINK_ID)]
        self.open_tick()
        self.assertEqual((self.store.read().secret, self.store.read().link_id), (self.secret, LINK_ID))
        snapshot = self.state.snapshot()
        self.assertEqual((snapshot["state"], snapshot["linked"], snapshot["showCode"]), ("linked", True, False))
        self.assertIsNone(self.state.secret_for_display())
        self.open_tick(30)
        self.assertFalse(self.state.ask())
        self.assertEqual(len(self.calls), 1)

    def test_a_failure_is_shown_and_the_polling_goes_on(self):
        # Mutation: Failed stops the polling. Red: no call 30 s later.
        self.answers = [worker.Failed("TimeoutError")]
        self.open_tick()
        self.assertEqual(self.state.snapshot()["state"], "offline")
        self.open_tick(30)
        self.assertEqual(len(self.calls), 2)

    def test_three_refused_pings_offer_a_relink_that_keeps_the_same_secret(self):
        # Mutation: accepting the relink forgets the secret. Red: the check after it carries a new secret.
        self.state.typed(LINK_ID, self.secret)
        self.assertFalse(self.state.accept_relink())  # nothing offered yet: no-op
        for _ in range(2):
            self.state.record_ping(worker.Refused())
        self.assertFalse(self.state.snapshot()["relinkOffered"])
        self.state.record_ping(worker.Refused())
        snapshot = self.state.snapshot()
        self.assertEqual((snapshot["state"], snapshot["relinkOffered"], snapshot["showCode"]), ("refused", True, False))
        self.assertTrue(self.state.accept_relink())
        self.assertEqual((self.store.read().secret, self.store.read().link_id), (self.secret, None))
        self.assertEqual(self.state.secret_for_display(), self.secret)
        self.open_tick()
        self.assertEqual(self.calls, [self.secret])

    def test_one_sent_resets_the_refused_count(self):
        # Mutation: Sent leaves the count. Red: the fourth refusal in five pings offers the relink.
        self.state.typed(LINK_ID, self.secret)
        for result in (worker.Refused(), worker.Refused(), worker.Sent(), worker.Refused(), worker.Refused()):
            self.state.record_ping(result)
        self.assertFalse(self.state.snapshot()["relinkOffered"])
        self.state.record_ping(worker.NotDelivered(429))  # neither refused nor sent: the count stands
        self.state.record_ping(worker.Refused())
        self.assertTrue(self.state.snapshot()["relinkOffered"])
        self.state.record_ping(worker.Sent())
        self.assertEqual(self.state.snapshot()["state"], "linked")

    def test_forget_and_the_typed_road(self):
        # Mutation: a check answered for the old secret stores its link id after a forget. Red: a link id is kept.
        self.answers = [lambda: (self.state.forget(), worker.Linked(LINK_ID))[1]]
        self.open_tick()
        after = self.store.read()
        self.assertNotEqual(after.secret, self.secret)
        self.assertIsNone(after.link_id)
        self.assertEqual(self.state.secret_for_display(), after.secret)
        self.assertFalse(self.state.typed("WXYZ6789ABC", SECRET))
        self.assertTrue(self.state.snapshot()["typedRefused"])
        self.assertEqual(self.store.read(), after)
        self.assertTrue(self.state.typed("wxyz-6789-abcd", "abcd-2345-efgh"))
        self.assertEqual((self.store.read().link_id, self.store.read().secret), (LINK_ID, SECRET))
        self.assertFalse(self.state.snapshot()["typedRefused"])

    def test_the_snapshot_never_holds_the_secret_or_the_link_id(self):
        # Mutation: the snapshot carries the pair for the page script. Red: the secret is in the JSON.
        self.answers = [worker.Linked(LINK_ID)]
        before = json.dumps(self.state.snapshot())
        self.open_tick()
        for text in (before, json.dumps(self.state.snapshot())):
            self.assertNotIn(self.secret, text)
            self.assertNotIn(LINK_ID, text)


if __name__ == "__main__":
    unittest.main()
