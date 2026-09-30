"""MainCommandTest: the two commands, `ping <kind>` for the bench and the page with its driver."""
import contextlib
import html
import http.client
import io
import json
import os
import re
import signal
import socket
import stat
import subprocess
import sys
import threading
import time
import unittest
import urllib.parse
import urllib.request
from pathlib import Path

import support
from support import LINK_ID, SECRET, error

config = support.module("config")
entry = support.module("__main__")
page = support.module("page")
watcher = support.module("watcher")
worker = support.module("worker")

SENT = (200, {"result": "sent", "devices": 1, "sent": 1, "reasons": {"sent": 1}})
TOKEN = "fake-Token_0123"  # a test vector, never a client's
RUN_FILE = "run.json"
RACE_WINDOWS_ONLY = ("a remove refused while another handle holds the file, a sharing violation, is a Windows "
                     "behavior; elsewhere the remove succeeds and this race does not arise")
NO_SIGBREAK = "Ctrl+Break is a Windows console signal: this platform has no signal.SIGBREAK"
READ_ONLY_WINDOWS_ONLY = ("the read-only attribute refuses a remove on Windows only; elsewhere the folder's "
                          "mode decides and this file is removed")
ROOT_WRITES = "root writes into a folder whatever its mode, so no folder can refuse it a new file"
EVERYONE = "*S-1-1-0"  # the well-known SID of Everyone: a deny for it binds this user, no account name looked up
QUIT_TOKEN = re.compile(r'<form method="post" action="/quit"><input type="hidden" name="token" value="([^"]+)">')


def answers(port):
    """True when something accepts a connection on 127.0.0.1:port."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


class FakeClock:
    """The re-read windows of a start on a clock that moves only when the start waits; each wait first plays
    the next of `moves`, the other start's steps, in order."""

    def __init__(self, *moves):
        self.now, self.moves = 0.0, list(moves)

    def clock(self):
        return self.now

    def sleep(self, seconds):
        if self.moves:
            self.moves.pop(0)()
        self.now += seconds


def deny_new_files(case, folder):
    """The folder refuses a new file while the case runs: a deny entry on Windows, where a read-only attribute
    does not stop a create, and a mode without write elsewhere."""
    if os.name == "nt":
        subprocess.run(["icacls", str(folder), "/deny", EVERYONE + ":(WD,AD)"], check=True, capture_output=True,
                       timeout=30)
        case.addCleanup(subprocess.run, ["icacls", str(folder), "/remove:d", EVERYONE], check=True,
                        capture_output=True, timeout=30)
    else:
        if os.geteuid() == 0:
            case.skipTest(ROOT_WRITES)
        os.chmod(folder, 0o555)
        case.addCleanup(os.chmod, folder, 0o755)
    with case.assertRaises(PermissionError):  # the precondition: this user really cannot add a file there
        open(folder / "a probe of the case", "x").close()


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
        self.beeps = []  # what the entry point's beep seam heard: every run passes it, so no test run beeps
        self.boxes = []  # what the message box seam showed: every run passes it, so no test run shows one

    def fake(self, answer):
        fake = support.FakeWorker(answer)
        self.addCleanup(fake.close)
        return fake

    def run_main(self, *argv, **kwargs):
        out, err = io.StringIO(), io.StringIO()
        kwargs.setdefault("beep", lambda: self.beeps.append("beep"))
        kwargs.setdefault("box", self.boxes.append)
        kwargs.setdefault("console", lambda: True)  # a console is attached unless a case says there is none
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
                stop, pings = threading.Event(), len(fake.requests)
                accepts = client_fake.count("POST", support.CLIENT_ACCEPT_PATH)
                thread, result = self.run_in_thread("--data-dir", str(self.data), "--worker", fake.base,
                                                    "--client-lockfile", str(lockfile), *argv, opener=None,
                                                    stop=stop, delay=lambda: 0)
                fake.wait_for(pings + 1, limit=3)
                stop.set()
                thread.join(5)
                code, out, err = result["run"]
                self.assertEqual(code, 0)
                self.assertEqual(client_fake.count("POST", support.CLIENT_ACCEPT_PATH) - accepts, posts)
                self.assertEqual(fake.bodies()[pings:],
                                 [{"linkId": LINK_ID, "secret": SECRET, "kind": "lol_queue_found"}])
                lines = out.splitlines()
                self.assertRegex(lines[0], r"^page: http://127\.0\.0\.1:\d+/$")
                self.assertIn(watcher.CONNECTED_LINE, lines)
                self.assertIn(line, lines)
                self.assertEqual(err, "")
                for value in (TOKEN, SECRET, LINK_ID):
                    self.assertNotIn(value, out)
                self.assertFalse(self.run_file.exists())

    def test_the_entry_point_beeps_through_its_seam_and_a_test_run_beeps_nothing(self):
        # Mutation: main ignores its beep seam, the Alerter keeps the sounding default. Red: the seam hears
        # no beep.
        self.store.set_typed(LINK_ID, SECRET)
        fake = self.fake(lambda path, body: SENT)
        client_fake = support.FakeClient(phase="ReadyCheck")
        self.addCleanup(client_fake.close)
        lockfile = self.no_client.parent.parent / "a client" / "lockfile"
        lockfile.parent.mkdir(parents=True)
        lockfile.write_text(f"LeagueClient:4242:{client_fake.port}:{TOKEN}:https", encoding="utf-8")
        stop = threading.Event()
        thread, result = self.run_in_thread("--data-dir", str(self.data), "--worker", fake.base,
                                            "--client-lockfile", str(lockfile), "--dry", opener=None, stop=stop)
        fake.wait_for(1, limit=3)
        stop.set()
        thread.join(5)
        self.assertEqual((result["run"][0], fake.bodies(), self.beeps),
                         (0, [{"linkId": LINK_ID, "secret": SECRET, "kind": "lol_queue_found"}], ["beep"]))

    @unittest.skipIf(os.name != "nt", RACE_WINDOWS_ONLY)
    def test_a_start_that_loses_the_race_reads_the_winners_record_and_opens_its_page(self):
        # Mutation: the re-read removed, a refused remove read at once. Red: "cannot start" and exit 1 where the
        # winner's record shows within the window, and no page where its port comes only after the claim.
        self.run_file.parent.mkdir(parents=True)
        winner, port = os.getppid(), 54321  # the process that started this test: alive, and not this start
        url, stop = f"http://127.0.0.1:{port}/", threading.Event()
        stop.set()  # a start that wrongly goes on ends at its first tick
        roads = (("its record shows with its port", [{"pid": winner, "port": port}], entry.ALREADY_RUNNING_LINE,
                  [url]),
                 ("its port comes after the claim", [{"pid": winner, "port": None}, {"pid": winner, "port": port}],
                  entry.ALREADY_RUNNING_LINE, [url]),
                 ("its port never comes", [{"pid": winner, "port": None}], entry.PAGE_NOT_KNOWN_LINE, []))
        for road, records, line, pages in roads:
            with self.subTest(road=road):
                with open(self.run_file, "w", encoding="utf-8") as held:  # the winner's handle, no record yet

                    def write(record, held=held):  # the winner's next step, played at a wait of this start
                        held.seek(0)
                        held.truncate()
                        json.dump(record, held)
                        held.flush()

                    clock, opened = FakeClock(*[lambda record=record: write(record) for record in records]), []
                    result = self.run_main("--data-dir", str(self.data), "--worker", "http://127.0.0.1:9",
                                           opener=opened.append, stop=stop, clock=clock.clock, sleep=clock.sleep)
                    kept = self.run_file.exists()
                self.assertEqual((result, opened, kept), ((0, line + "\n", ""), pages, True))
                self.assertFalse(self.store.path.exists())  # no page, so no first load of the config
        # A folder in the run file's place is no race: that start still cannot go on, and its line names the file.
        self.run_file.unlink()
        self.run_file.mkdir()
        self.assertEqual(self.run_main("--data-dir", str(self.data), "--worker", "http://127.0.0.1:9",
                                       opener=[].append, stop=stop),
                         (1, entry.CLAIM_FAILED_LINE.format(path=self.run_file) + "\n", ""))

    @unittest.skipIf(os.name != "nt", READ_ONLY_WINDOWS_ONLY)
    def test_a_read_only_stale_run_file_naming_no_pid_cannot_start_and_its_line_names_the_file(self):
        # Mutation: the probe removed, the refused remove read as a race at once. Red: "already running" and exit 0.
        self.run_file.parent.mkdir(parents=True)
        stale = {"pid": None, "port": None}
        self.run_file.write_text(json.dumps(stale), encoding="utf-8")
        os.chmod(self.run_file, stat.S_IREAD)  # the read-only attribute: the remove is refused
        self.addCleanup(os.chmod, self.run_file, stat.S_IREAD | stat.S_IWRITE)
        clock, opened, stop = FakeClock(), [], threading.Event()
        stop.set()
        for start in ("first", "next"):  # every start from then on says the same
            with self.subTest(start=start):
                result = self.run_main("--data-dir", str(self.data), "--worker", "http://127.0.0.1:9",
                                       opener=opened.append, stop=stop, clock=clock.clock, sleep=clock.sleep)
                self.assertEqual(result, (1, entry.CLAIM_FAILED_LINE.format(path=self.run_file) + "\n", ""))
        self.assertEqual((opened, json.loads(self.run_file.read_text(encoding="utf-8"))), ([], stale))
        self.assertFalse(self.store.path.exists())  # no page, so no first load of the config

    def test_a_config_folder_that_takes_no_new_file_cannot_start_and_its_line_names_the_folder(self):
        # Mutation: the open's PermissionError read as a race. Red: "already running" and exit 0.
        folder = self.run_file.parent
        folder.mkdir(parents=True)
        deny_new_files(self, folder)
        clock, opened, stop = FakeClock(), [], threading.Event()
        stop.set()
        result = self.run_main("--data-dir", str(self.data), "--worker", "http://127.0.0.1:9",
                               opener=opened.append, stop=stop, clock=clock.clock, sleep=clock.sleep)
        self.assertEqual((result, opened, self.run_file.exists(), self.store.path.exists()),
                         ((1, entry.FOLDER_FAILED_LINE.format(path=folder) + "\n", ""), [], False, False))

    def test_with_no_console_each_start_diagnosis_also_reaches_the_message_box_with_its_line(self):
        # Mutation: the box skipped. Red: the box shows nothing where no console is attached.
        self.run_file.parent.mkdir(parents=True)
        stop = threading.Event()
        stop.set()

        def already_running():
            self.run_file.write_text(json.dumps({"pid": os.getppid(), "port": 54321}), encoding="utf-8")
            return entry.ALREADY_RUNNING_LINE, 0

        def cannot_start():
            self.run_file.unlink()
            self.run_file.mkdir()  # a folder in the run file's place
            return entry.CLAIM_FAILED_LINE.format(path=self.run_file), 1

        def config_sentence():
            self.run_file.rmdir()
            self.store.path.mkdir()  # a folder in the config file's place: it can be neither read nor written
            return config.UNAVAILABLE.format(path=self.store.path), 1

        for diagnosis in (already_running, cannot_start, config_sentence):
            with self.subTest(diagnosis=diagnosis.__name__):
                line, code = diagnosis()
                for attached, shown in ((True, []), (False, [line])):
                    del self.boxes[:]
                    result = self.run_main("--data-dir", str(self.data), "--worker", "http://127.0.0.1:9",
                                           opener=lambda url: True, stop=stop,
                                           console=lambda attached=attached: attached)
                    self.assertEqual((result, self.boxes), ((code, line + "\n", ""), shown))

    @unittest.skipUnless(hasattr(signal, "SIGBREAK"), NO_SIGBREAK)
    def test_ctrl_break_stops_the_page_and_the_watcher_and_removes_the_run_file(self):
        # Mutation: _break_as_interrupt installs no handler. Red: the break reaches this case's guard and the
        # program runs on until the case stops it.
        guard, returned, seen, stop = [], threading.Event(), {}, threading.Event()
        previous = signal.signal(signal.SIGBREAK, lambda number, frame: guard.append(number))
        self.addCleanup(signal.signal, signal.SIGBREAK, previous)
        baseline = threading.active_count()

        def breaker():
            deadline = time.monotonic() + 5
            while (self.read_run() or {}).get("port") is None and time.monotonic() < deadline:
                time.sleep(0.02)
            seen["port"] = (self.read_run() or {}).get("port")
            signal.raise_signal(signal.SIGBREAK)
            if not returned.wait(4):  # the break never reached the program: end it so the case can say so
                seen["fallback"] = True
                stop.set()

        breaking = threading.Thread(target=breaker)
        breaking.start()
        code, out, err = self.run_main("--data-dir", str(self.data), "--worker", "http://127.0.0.1:9",
                                       opener=lambda url: True, stop=stop)
        returned.set()
        breaking.join(5)
        deadline = time.monotonic() + 2
        while threading.active_count() > baseline and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual((code, err, guard, seen.get("fallback", False)), (0, "", [], False))
        self.assertEqual((self.run_file.exists(), answers(seen["port"]), threading.active_count() <= baseline),
                         (False, False, True))

    def test_quit_on_the_page_stops_the_program_removes_the_run_file_and_exits_0(self):
        # Mutation: the run file kept at the stop. Red: the run file is still there after the exit.
        opened, stop = [], threading.Event()
        thread, result = self.run_in_thread("--data-dir", str(self.data), "--worker", "http://127.0.0.1:9",
                                            opener=lambda url: opened.append(url) or True, stop=stop)
        deadline = time.monotonic() + 5
        while not opened and time.monotonic() < deadline:
            time.sleep(0.02)
        port = int(opened[0].split(":")[2].strip("/"))

        def request(method, path, body=None):
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            try:
                headers = {"Host": f"127.0.0.1:{port}", "Accept-Language": "en"}
                if body is not None:
                    headers["Content-Type"] = "application/x-www-form-urlencoded"
                connection.request(method, path, body=body, headers=headers)
                response = connection.getresponse()
                return response.status, response.read().decode("utf-8")
            finally:
                connection.close()

        token = QUIT_TOKEN.search(request("GET", "/")[1])
        status, body = request("POST", "/quit", urllib.parse.urlencode({"token": token.group(1) if token else ""}))
        thread.join(5)
        outcome = (status, thread.is_alive(), self.run_file.exists(), answers(port))
        stop.set()
        thread.join(5)
        self.assertEqual(outcome, (200, False, False, False))
        self.assertEqual(result["run"], (0, f"page: {opened[0]}\n", ""))
        for key in ("stopped", "start_again"):
            self.assertIn(html.escape(page.WORDS["en"][key]), body)


if __name__ == "__main__":
    unittest.main()
