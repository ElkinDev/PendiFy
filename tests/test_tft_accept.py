"""The click on the TFT app's accept after a pop, timed against the game's own check: the log written to a scratch
file, the clock, the waits, the click and its jitter faked (lines of a real session, test_tft_app.LINES)."""
import unittest
from unittest import mock

import support
from test_tft_app import LINES, POP_AT

tft_app = support.module("tft_app")


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


if __name__ == "__main__":
    unittest.main()
