"""AlerterListenTest: the Alerter tells one listener each ping's result name and time, right after it records it.

The watcher listens to keep the ping in its log (lane pclog round 1). The ping is a function of the test, never the
Worker; the store holds a typed pair under build/tmp.
"""
import threading
import unittest
from pathlib import Path

import support
from support import LINK_ID, SECRET, FakeClock

alert = support.module("alert")
config = support.module("config")
pairing = support.module("pairing")
worker = support.module("worker")

QUEUE_FOUND = worker.KINDS[0]
PING_WALL = 1_790_000_100.0


class AlerterListenTest(unittest.TestCase):
    def setUp(self):
        tmp = support.temp_dir()
        self.addCleanup(tmp.cleanup)
        self.store = config.ConfigStore(Path(tmp.name))
        self.store.set_typed(LINK_ID, SECRET)
        self.state = pairing.PairingState(self.store, lambda secret: worker.Refused(), clock=FakeClock())
        self.lines, self.heard, self.answers, self.walls = [], [], [], []

    def ping(self, link_id, secret, kind):
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    def wall(self):
        self.walls.append(PING_WALL + len(self.walls))
        return self.walls[-1]

    def alerter(self, **kwargs):
        kwargs.setdefault("start", lambda target: target())
        return alert.Alerter(self.store, self.state, self.ping, beep=lambda: None, log=self.lines.append,
                             wall=self.wall, **kwargs)

    def test_the_listener_hears_each_result_name_and_time_once_after_the_record(self):
        # Mutation: the listener called before the record. Red: the last ping it reads is the one before.
        subject = self.alerter()
        subject.listen(lambda name, at: self.heard.append((name, at, subject.last_ping())))
        self.answers = [worker.Sent(), worker.Refused(), worker.NotDelivered(503), worker.Failed("Timeout"),
                        ValueError("a refused input")]
        for _ in range(5):
            subject(QUEUE_FOUND)
        names = ["sent", "refused", "not_delivered", "failed", "failed"]
        times = [PING_WALL + index for index in range(5)]
        self.assertEqual(self.heard, [(name, time, (name, time)) for name, time in zip(names, times)])
        self.assertEqual(self.lines, [alert.PING_LINES[name] for name in names])

    def test_with_no_listener_nothing_changes(self):
        subject = self.alerter()
        self.answers = [worker.Sent(), worker.Refused()]
        subject(QUEUE_FOUND)
        subject(QUEUE_FOUND)
        self.assertEqual(subject.last_ping(), ("refused", PING_WALL + 1))
        self.assertEqual(self.lines, [alert.PING_LINES["sent"], alert.PING_LINES["refused"]])
        self.assertTrue(subject.flush(1.0))

    def test_a_listener_that_raises_leaves_the_record_and_the_ping_thread_ends_as_before(self):
        # Mutation: the listener's failure not swallowed. Red: the ping's thread breaks before its line.
        broken = []
        saved = threading.excepthook
        threading.excepthook = broken.append
        self.addCleanup(setattr, threading, "excepthook", saved)
        threads = []

        def start(target):  # a thread of its own, as the program runs each ping, kept so the test can join it
            threads.append(threading.Thread(target=target, daemon=True))
            threads[-1].start()
            return threads[-1]

        def refuse(name, at):
            raise RuntimeError("a listener that breaks")
        subject = self.alerter(start=start)
        subject.listen(refuse)
        self.answers = [worker.Sent()]
        subject(QUEUE_FOUND)
        self.assertTrue(subject.flush(2.0))
        threads[0].join(2.0)
        self.assertFalse(threads[0].is_alive())
        self.assertEqual(subject.last_ping(), ("sent", PING_WALL))
        self.assertEqual(self.lines, [alert.PING_LINES["sent"]])
        self.assertEqual(broken, [])
        self.assertEqual(self.state.snapshot()["state"], "linked")


if __name__ == "__main__":
    unittest.main()
