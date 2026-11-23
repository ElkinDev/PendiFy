"""The TFT app's pops through the watcher, the app read from its own log: a new pop is handled on the next turn after
one accepted or one whose click did not take, and the same pop is never alerted or clicked twice. The chain is the
real one (Watcher, both_clients.EitherClient with no League client, tft_app.AppSession on a scratch log written with
the lines of a real session, test_tft_app.LINES); the time, the waits, the alert and the click are faked.

The owner's case on 2026-11-23: a pop accepted by the click, a stranger who declined 13.0 s after the pop, and the
pop again 0.9 s later, seen only when the 15 s hold after the accept ended."""
import threading
import time
import unittest

import support
from test_tft_app import LINES, POP_AT

both_clients = support.module("both_clients")
tft_app = support.module("tft_app")
watcher = support.module("watcher")

DECLINED = (b"[2026.11.20-15.48.04:490][601]TFT: [Standalone] TFTLobbySubsystem_1: Stranger declined AFK Check. "
            b"Resuming matchmaking.\n")
REPOP_AT = POP_AT + 13.968
REPOP = b"[2026.11.20-15.48.05:424][640]TFT: [Standalone] TFTLobbySubsystem_1: Match found, showing AFK Check Modal\n"
STEP = watcher.STEP_PAUSE


def stamped(at, text):
    """A line of the app's log stamped at the epoch `at` to the millisecond, in the log's own shape."""
    seconds, millis = divmod(round(at * 1000), 1000)
    moment = time.strftime("%Y.%m.%d-%H.%M.%S", time.gmtime(seconds)).encode()
    return b"[%s:%03d][528]TFT: [Standalone] TFTLobbySubsystem_1: %s\n" % (moment, millis, text)


class AppPopFixture:
    """One fake time for the watcher's clock and wall and the session's; the click takes (its log says accepted at
    the click's time) unless `answer` is a reason; the alert records each kind with its time after the first pop."""

    def setUp(self):
        tmp = support.temp_dir()
        self.addCleanup(tmp.cleanup)
        self.path = f"{tmp.name}/TFT.log"
        open(self.path, "wb").close()
        self.now, self.on, self.answer, self.landing = POP_AT - 60, True, None, None
        self.alerts, self.lines, self.clicks = [], [], []
        test = self

        class Clicker:
            def click(self):
                test.clicks.append(test.since_pop())
                if test.answer is None:
                    test.write(stamped(test.now, b"Accepted match"))
                return test.answer

        class Alert:
            def __call__(self, kind):
                test.alerts.append((kind, test.since_pop()))

            def sound(self):
                pass

            def last_ping(self):
                return None, None

        def path():
            if self.landing is not None:  # the page's pause lands while the step reads the app's log
                landing, self.landing = self.landing, None
                landing()
            return self.path

        self.app = tft_app.AppSession(path=path, running=lambda: True, clock=self.time, wall=self.time,
                                      autoaccept=lambda: self.on, clicker=Clicker(), sleep=self.advance,
                                      jitter=lambda: 0.0)
        no_league = type("NoLeague", (), {"read": lambda s: None})()
        self.credentials, self.alert = both_clients.EitherClient(no_league, self.app), Alert()
        self.subject = self.watcher()

    def watcher(self, **kwargs):
        kwargs.setdefault("sleep", self.advance)
        return watcher.Watcher(self.credentials, self.alert, post=lambda *a: self.fail("posted an accept to the app"),
                               clock=self.time, wall=self.time, log=self.lines.append, **kwargs)

    def time(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds

    def since_pop(self):
        return round(self.now - POP_AT, 3)

    def write(self, *lines):
        with open(self.path, "ab") as log:
            log.write(b"".join(lines))

    def turn(self):
        """One turn of the watcher, its step pause after the last."""
        self.now += STEP
        return self.subject.step()

    def turns_until(self, moment):
        while self.now + STEP <= moment:
            self.turn()

    def pop(self):
        """The queue, then the pop at its own stamp, read on the next turn."""
        self.write(LINES["queue"])
        self.turn()
        self.now = POP_AT
        self.write(LINES["pop"])
        self.turn()

    def repop(self):
        """A stranger declined 13.0 s after the pop, the pop again 0.9 s later, read on the next turn."""
        self.turns_until(REPOP_AT)
        self.write(DECLINED)
        self.now = REPOP_AT
        self.write(REPOP)
        self.turn()


class AppRepopTest(AppPopFixture, unittest.TestCase):
    def test_a_pop_again_after_an_accepted_one_is_alerted_and_clicked_on_the_next_turn(self):
        # Mutation: the 15 s hold after the click kept. Red: the pop again 13.968 s after the first is read 18.3 s
        # after it (the click's accept read 3.3 s after the pop, then 15 s), past this turn.
        self.pop()
        self.assertEqual((self.alerts, self.clicks), ([(watcher.QUEUE_FOUND, STEP)], [1.3]))
        self.repop()
        self.assertEqual(self.alerts, [(watcher.QUEUE_FOUND, STEP), (watcher.QUEUE_FOUND, 14.268)])
        self.assertEqual(self.clicks, [1.3, 15.268])  # each first click ACCEPT_FIRST_WAIT after its turn, as ever
        self.assertEqual(self.lines, [watcher.CONNECTED_LINE, watcher.CLICKING_LINE, watcher.CLICKED_LINE,
                                      watcher.CLICKING_LINE, watcher.CLICKED_LINE])
        self.assertEqual([detail for _, _, kind, detail in self.subject.events() if kind == "phase"],
                         ["Matchmaking", "ReadyCheck", "Matchmaking", "ReadyCheck"])

    def test_a_pop_whose_clicks_did_not_take_is_never_handled_again_and_the_pop_again_is(self):
        # Mutation: the handled pop's stamp not compared. Red: each turn from 7.6 s to 15 s reads the open pop as
        # ReadyCheck and alerts and clicks it again. Mutation: the hold kept. Red: the pop again is not read.
        self.answer = "no_window"
        self.pop()
        first = [1.3, 3.3, 5.3]  # three clicks, ACCEPT_RETRY_WAIT apart, then the last look at 7.3 s
        self.assertEqual((self.alerts, self.clicks, self.since_pop()), ([(watcher.QUEUE_FOUND, STEP)], first, 7.3))
        self.turns_until(POP_AT + 13.0)
        self.assertEqual(self.app.phase(), "ReadyCheck")  # the check still open, read again: the same pop
        self.assertEqual((len(self.alerts), self.clicks), (1, first))
        self.repop()
        self.assertEqual(self.alerts, [(watcher.QUEUE_FOUND, STEP), (watcher.QUEUE_FOUND, 14.268)])
        self.assertEqual(self.clicks, first + [15.268, 17.268, 19.268])
        failed = watcher.CLICK_FAILED_LINE.format("no_window")
        self.assertEqual(self.lines, [watcher.CONNECTED_LINE, watcher.CLICKING_LINE, failed,
                                      watcher.CLICKING_LINE, failed])

    def test_the_same_pop_read_again_from_a_log_written_again_is_not_alerted_and_a_new_one_is(self):
        # Mutation: the handled pop's stamp not compared, the session alone saying each pop line once. Red: the same
        # pop line, read again from a log the app wrote again, alerts a second time.
        self.on = False  # no click: the alert alone, as with the switch off
        self.write(LINES["lobby"])
        self.pop()
        with open(self.path, "wb") as log:  # a shorter log, read from its start: the same pop line in it
            log.write(LINES["queue"] + LINES["pop"])
        self.turn()
        self.assertEqual((self.alerts, self.clicks), ([(watcher.QUEUE_FOUND, STEP)], []))
        self.repop()
        self.assertEqual(self.alerts, [(watcher.QUEUE_FOUND, STEP), (watcher.QUEUE_FOUND, 14.268)])
        self.assertEqual(self.lines, [watcher.CONNECTED_LINE, watcher.MANUAL_LINE, watcher.MANUAL_LINE])

    def test_a_pop_read_by_a_step_the_pause_lands_inside_is_handled_after_the_resume_and_only_then(self):
        # Mutation: the stamp kept while paused. Red: the pop the pause swallowed is never alerted after the resume.
        stop, left = threading.Event(), [0]

        def turn_sleep(seconds):
            self.now += seconds
            left[0] -= 1
            if left[0] <= 0:
                stop.set()

        self.subject = self.watcher(stop=stop, sleep=turn_sleep)  # run() driven turn by turn on this thread

        def run(count):
            left[0] = count
            self.subject.run()
            stop.clear()

        self.answer = "no_window"
        self.write(LINES["queue"])
        run(1)
        self.now = POP_AT
        self.write(LINES["pop"])
        self.landing = self.subject.pause
        run(1)
        self.assertEqual((self.alerts, self.clicks), ([], []))
        run(2)
        self.subject.resume()
        run(1)
        self.assertEqual((self.alerts, self.clicks), ([(watcher.QUEUE_FOUND, 0.9)], [1.9, 3.9, 5.9]))
        self.subject.pause()  # handled before this pause: the open pop is not handled again after its resume
        run(2)
        self.subject.resume()
        run(3)
        self.assertEqual(self.app.phase(), "ReadyCheck")
        self.assertEqual((len(self.alerts), len(self.clicks)), (1, 3))


if __name__ == "__main__":
    unittest.main()
