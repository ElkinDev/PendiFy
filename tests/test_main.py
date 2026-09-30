"""MainCommandTest: the two commands, `ping <kind>` for the bench and the page with its driver."""
import contextlib
import io
import json
import os
import subprocess
import sys
import threading
import unittest
import urllib.request
from pathlib import Path

import support
from support import LINK_ID, SECRET, error

config = support.module("config")
entry = support.module("__main__")
watcher = support.module("watcher")
worker = support.module("worker")

SENT = (200, {"result": "sent", "devices": 1, "sent": 1, "reasons": {"sent": 1}})
TOKEN = "fake-Token_0123"  # a test vector, never a client's
RUN_FILE = "run.json"


class MainCommandTest(unittest.TestCase):
    def setUp(self):
        tmp = support.temp_dir()
        self.addCleanup(tmp.cleanup)
        self.data = Path(tmp.name) / "a data dir"
        self.store = config.ConfigStore(self.data)
        self.run_file = self.store.path.parent / RUN_FILE
        # A lockfile that is never written: every run of the entry point watches a client that is not there,
        # never the real one, unless a case points it at a fake.
        self.no_client = Path(tmp.name) / "no client" / "lockfile"

    def fake(self, answer):
        fake = support.FakeWorker(answer)
        self.addCleanup(fake.close)
        return fake

    def run_main(self, *argv, **kwargs):
        out, err = io.StringIO(), io.StringIO()
        if "--client-lockfile" not in argv:
            argv = ("--client-lockfile", str(self.no_client)) + argv
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

    def read_run(self):
        try:
            return json.loads(self.run_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def run_in_thread(self, *argv, stop, **kwargs):
        result = {}
        thread = threading.Thread(target=lambda: result.update(run=self.run_main(*argv, stop=stop, **kwargs)))
        thread.start()
        return thread, result

    def test_a_second_start_against_a_live_holder_exits_0_with_one_line_and_starts_no_server(self):
        # Mutation: the liveness check removed (every holder read as gone). Red: a second page starts.
        self.run_file.parent.mkdir(parents=True)
        holder = {"pid": os.getppid(), "port": 54321}  # the process that started this test: alive
        self.run_file.write_text(json.dumps(holder), encoding="utf-8")
        opened, stop = [], threading.Event()
        thread, result = self.run_in_thread("--data-dir", str(self.data), "--worker", "http://127.0.0.1:9",
                                            opener=opened.append, stop=stop)
        thread.join(2)
        stop.set()
        thread.join(5)
        self.assertEqual(result["run"], (0, entry.ALREADY_RUNNING_LINE + "\n", ""))
        self.assertEqual(opened, ["http://127.0.0.1:54321/"])
        self.assertEqual(json.loads(self.run_file.read_text(encoding="utf-8")), holder)
        self.assertFalse(self.store.path.exists())  # no page, so no first load of the config

    def test_a_run_file_whose_pid_is_gone_or_that_is_unreadable_is_taken_over_and_removed_at_exit(self):
        # Mutation: the liveness check removed (every holder read as alive). Red: the gone holder blocks the start.
        gone = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"], capture_output=True, text=True,
                              timeout=30)
        self.run_file.parent.mkdir(parents=True)
        for name, text in (("gone pid", json.dumps({"pid": int(gone.stdout), "port": 54321})), ("empty", ""),
                           ("corrupt", "{"), ("no pid", json.dumps({"port": 54321}))):
            with self.subTest(case=name):
                self.run_file.write_text(text, encoding="utf-8")
                seen, stop = {}, threading.Event()

                def opener(url, seen=seen, stop=stop):
                    seen.update(url=url, run=self.read_run())
                    stop.set()
                    return True

                code, out, err = self.run_main("--data-dir", str(self.data), "--worker", "http://127.0.0.1:9",
                                               opener=opener, stop=stop)
                self.assertEqual((code, out, err), (0, f"page: {seen.get('url')}\n", ""))
                self.assertEqual(seen["run"], {"pid": os.getpid(), "port": int(seen["url"].split(":")[2].strip("/"))})
                self.assertFalse(self.run_file.exists())

    def test_the_watcher_runs_beside_the_page_and_dry_turns_the_accept_off(self):
        # Mutation: --dry ignored, the accept left on. Red: an accept posted under --dry.
        self.store.set_typed(LINK_ID, SECRET)
        fake = self.fake(lambda path, body: SENT)
        client_fake = support.FakeClient(phase="ReadyCheck")
        self.addCleanup(client_fake.close)
        lockfile = self.no_client.parent.parent / "a client" / "lockfile"
        lockfile.parent.mkdir(parents=True)
        lockfile.write_text(f"LeagueClient:4242:{client_fake.port}:{TOKEN}:https", encoding="utf-8")
        for argv, posts, line in ((["--dry"], 0, watcher.DRY_LINE), ([], 1, watcher.ACCEPTED_LINE)):
            with self.subTest(argv=argv):
                stop, pings, accepts = threading.Event(), len(fake.requests), client_fake.count("POST",
                                                                                                support.CLIENT_ACCEPT_PATH)
                thread, result = self.run_in_thread("--data-dir", str(self.data), "--worker", fake.base,
                                                    "--client-lockfile", str(lockfile), *argv, opener=None,
                                                    stop=stop, delay=lambda: 0)
                fake.wait_for(pings + 1, limit=3)
                stop.set()
                thread.join(5)
                code, out, err = result["run"]
                self.assertEqual(code, 0)
                self.assertEqual(client_fake.count("POST", support.CLIENT_ACCEPT_PATH) - accepts, posts)
                self.assertEqual(fake.bodies()[pings:], [{"linkId": LINK_ID, "secret": SECRET, "kind": "lol_queue_found"}])
                lines = out.splitlines()
                self.assertRegex(lines[0], r"^page: http://127\.0\.0\.1:\d+/$")
                self.assertIn(watcher.CONNECTED_LINE, lines)
                self.assertIn(line, lines)
                self.assertEqual(err, "")
                for value in (TOKEN, SECRET, LINK_ID):
                    self.assertNotIn(value, out)
                self.assertFalse(self.run_file.exists())


if __name__ == "__main__":
    unittest.main()
