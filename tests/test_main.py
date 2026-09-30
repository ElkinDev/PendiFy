"""MainCommandTest: the two commands, `ping <kind>` for the bench and the page with its driver."""
import contextlib
import io
import json
import threading
import unittest
import urllib.request
from pathlib import Path

import support
from support import LINK_ID, SECRET, error

config = support.module("config")
entry = support.module("__main__")
worker = support.module("worker")

SENT = (200, {"result": "sent", "devices": 1, "sent": 1, "reasons": {"sent": 1}})


class MainCommandTest(unittest.TestCase):
    def setUp(self):
        tmp = support.temp_dir()
        self.addCleanup(tmp.cleanup)
        self.data = Path(tmp.name) / "a data dir"
        self.store = config.ConfigStore(self.data)

    def fake(self, answer):
        fake = support.FakeWorker(answer)
        self.addCleanup(fake.close)
        return fake

    def run_main(self, *argv, **kwargs):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = entry.main(list(argv), **kwargs)
            except SystemExit as leaving:
                code = leaving.code
        return code, out.getvalue(), err.getvalue()

    def test_ping_sends_the_stored_pair_and_prints_one_line(self):
        # Mutation: ping reads the pair before the options are applied. Red: the real profile is read.
        self.store.set_typed(LINK_ID, SECRET)
        fake = self.fake(lambda path, body: SENT)
        for argv in (["--data-dir", str(self.data), "--worker", fake.base, "ping", "lol_queue_found"],
                     ["ping", "lol_queue_found", "--data-dir", self.data.as_posix(), "--worker", fake.base]):
            with self.subTest(argv=argv):
                self.assertEqual(self.run_main(*argv), (0, "sent\n", ""))
        self.assertEqual(fake.bodies(), [{"linkId": LINK_ID, "secret": SECRET, "kind": "lol_queue_found"}] * 2)

    def test_each_ping_answer_prints_its_fixed_line(self):
        # Mutation: the refused line prints the answer body. Red: a different line.
        self.store.set_typed(LINK_ID, SECRET)
        answers = [(error("link_refused", 403), 1, entry.REFUSED_LINE),
                   (error("throttled", 429), 1, "not delivered (HTTP 429)"),
                   (None, 1, "failed: TimeoutError")]
        for reply, code, line in answers:
            with self.subTest(line=line):
                fake = self.fake(lambda path, body, reply=reply: reply)
                result = self.run_main("--data-dir", str(self.data), "--worker", fake.base, "ping",
                                       "lol_match_started", timeout=0.2)
                self.assertEqual(result, (code, line + "\n", ""))
                self.assertNotIn(SECRET, result[1])
                self.assertNotIn(LINK_ID, result[1])

    def test_ping_without_a_pair_says_so_and_writes_nothing(self):
        # Mutation: ping loads the config, which mints on a first load. Red: a config file appears.
        fake = self.fake(lambda path, body: SENT)
        self.assertEqual(self.run_main("--data-dir", str(self.data), "--worker", fake.base, "ping", "lol_queue_found"),
                         (2, entry.NOT_LINKED_LINE + "\n", ""))
        self.assertFalse(self.store.path.exists())
        self.store.load()
        self.assertEqual(self.run_main("--data-dir", str(self.data), "--worker", fake.base, "ping",
                                       "lol_queue_found")[0], 2)
        self.assertEqual(fake.requests, [])

    def test_hostile_arguments_are_refused_before_any_call(self):
        # Mutation: --worker accepts any address. Red: exit 0 against a non-loopback address.
        self.store.set_typed(LINK_ID, SECRET)
        fake = self.fake(lambda path, body: SENT)
        for argv in (["ping"], ["ping", ""], ["ping", "match_found"], ["ping", "lol_queue_found", "extra"],
                     ["--worker", "https://example.com", "ping", "lol_queue_found"],
                     ["--worker", "", "ping", "lol_queue_found"], ["--data-dir", "", "ping", "lol_queue_found"],
                     ["unknown"]):
            with self.subTest(argv=argv):
                code, out, err = self.run_main("--data-dir", str(self.data), "--worker", fake.base, *argv)
                self.assertEqual((code, out), (2, ""))
                self.assertNotIn(SECRET, err)
        self.assertEqual(fake.requests, [])

    def test_the_page_opens_while_unlinked_and_its_driver_stores_the_link(self):
        # Mutation: the driver loop never ticks. Red: the fake Worker never sees a check.
        checked = threading.Event()
        fake = self.fake(lambda path, body: (checked.set(), (200, {"linkId": LINK_ID}))[1])
        opened, stop, result = [], threading.Event(), {}

        def opener(url):
            opened.append(url)
            request = urllib.request.Request(url + "state")  # the browser's first fetch: the page is open
            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=5) as answer:
                result["state"] = json.loads(answer.read())
            return True

        thread = threading.Thread(target=lambda: result.update(
            run=self.run_main("--data-dir", str(self.data), "--worker", fake.base, opener=opener, stop=stop)))
        thread.start()
        self.assertTrue(checked.wait(5))
        stop.set()
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(opened), 1)
        self.assertRegex(opened[0], r"^http://127\.0\.0\.1:\d+/$")
        self.assertEqual((result["state"]["state"], result["state"]["showCode"]), ("waiting", True))
        self.assertEqual(result["run"], (0, f"page: {opened[0]}\n", ""))
        self.assertEqual(self.store.read().link_id, LINK_ID)
        self.assertEqual(fake.bodies(), [{"secret": self.store.read().secret}])

    def test_the_page_does_not_open_once_linked(self):
        # Mutation: the browser opened whatever the state. Red: the opener is called.
        self.store.set_typed(LINK_ID, SECRET)
        opened, stop = [], threading.Event()
        stop.set()
        code, out, err = self.run_main("--data-dir", str(self.data), "--worker", "http://127.0.0.1:9",
                                       opener=opened.append, stop=stop)
        self.assertEqual((code, opened, err), (0, [], ""))
        self.assertRegex(out, r"^page: http://127\.0\.0\.1:\d+/\n$")


if __name__ == "__main__":
    unittest.main()
