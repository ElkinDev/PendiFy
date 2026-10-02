"""The update's pins (lane pfupd, OR-103): the check of PyPI, the rounds and the install, on fakes of the GET, of
pip's process and of the clock, so no test reaches PyPI and none runs pip."""
import json
import subprocess
import threading
import time
import unittest
import urllib.error

import support

update = support.module("update")
worker = support.module("worker")

# The interpreter of a pythonw start and the one beside it that runs pip.
PYTHONW = r"C:\Users\someone\AppData\Local\Programs\Python\Python312\pythonw.exe"
PYTHON = r"C:\Users\someone\AppData\Local\Programs\Python\Python312\python.exe"
CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008
NOTHING = {"state": "none", "version": None, "error": None}
PIP_LINE = "ERROR: Could not find a version that satisfies the requirement pendify==0.1.6 (from versions: none)"


class Answer:
    """What urlopen answers: a body read up to a bound, closed by its with."""

    def __init__(self, body):
        self.body = body

    def read(self, limit=-1):
        return self.body if limit < 0 else self.body[:limit]

    def __enter__(self):
        return self

    def __exit__(self, *failure):
        return False


class FakeGet:
    """urlopen as check() calls it: each call answers the next of `answers` (the last one again once they run out),
    a version served in PyPI's JSON, or an exception raised; every request is kept with its User-Agent and timeout."""

    def __init__(self, *answers):
        self.answers, self.requests = list(answers), []

    def __call__(self, request, timeout):
        self.requests.append((request.full_url, request.get_header("User-agent"), timeout))
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, BaseException):
            raise answer
        return Answer(json.dumps({"info": {"name": "pendify", "version": answer}, "releases": {}}).encode("utf-8"))


class FakeRun:
    """subprocess.run as install() calls it: each call answers the next of `results`, (exit code, stderr) or an
    exception raised; every call is kept with its options and the thread it ran on."""

    def __init__(self, *results):
        self.results, self.calls = list(results), []

    def __call__(self, command, **options):
        self.calls.append((command, options, threading.current_thread()))
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return subprocess.CompletedProcess(command, result[0], "", result[1])


class Waits:
    """The wait before each round: kept, never slept, and answering closed once `rounds` rounds have run."""

    def __init__(self, rounds):
        self.rounds, self.waited = rounds, []

    def __call__(self, seconds):
        self.waited.append(seconds)
        return len(self.waited) > self.rounds


def wait_until(condition, limit=5.0):
    deadline = time.monotonic() + limit
    while not condition() and time.monotonic() < deadline:
        time.sleep(0.01)
    return condition()


class UpdaterTest(unittest.TestCase):
    def make(self, mode, *answers, current="0.1.5", runs=(), disk="0.1.5", venv=False, waits=None):
        """An updater whose GET, pip and wait are fakes, its log and its restarts kept on the case."""
        get, run = FakeGet(*answers), FakeRun(*runs)
        self.logs, self.restarts = [], []
        made = update.Updater(current, mode, check=lambda: update.check(urlopen=get),
                              install=lambda version: update.install(version, run=run, installed=lambda: disk,
                                                                     executable=PYTHONW, in_venv=venv),
                              restart=lambda: self.restarts.append(True), clock=waits or Waits(1),
                              log=self.logs.append)
        return made, get, run

    def test_a_newer_version_on_pypi_is_seen_and_an_equal_or_older_one_is_not(self):
        # Mutation: the versions compared as strings. Red: 0.1.10 reads older than 0.1.9.
        # Mutation: a version that is not X.Y.Z parsed by its digits. Red: 0.1.6rc1 reads newer.
        for current, remote, seen in (("0.1.5", "0.1.6", "0.1.6"), ("0.1.9", "0.1.10", "0.1.10"),
                                      ("0.1.5", "1.0.0", "1.0.0"), ("0.1.5", "0.1.5", None), ("0.1.5", "0.1.4", None),
                                      ("0.1.5", "0.0.9", None), ("0.1.5", "0.1.6rc1", None), ("0.1.5", "0.2", None),
                                      ("0.1.5", "0.1.6 ", None), ("0.1.5", 16, None), ("source", "9.9.9", None),
                                      ("0.1.5.post1", "0.1.6", None)):
            with self.subTest(current=current, remote=remote):
                made, get, run = self.make("notify", remote, current=current)
                made.round()
                self.assertEqual(made.snapshot(), NOTHING if seen is None else
                                 {"state": "available", "version": seen, "error": None})
                self.assertEqual(get.requests, [("https://pypi.org/pypi/pendify/json", worker.USER_AGENT, 5)])
                self.assertEqual((run.calls, self.restarts, self.logs), ([], [], []))

    def test_auto_mode_installs_in_the_background_and_reads_ready(self):
        # Mutation: --user kept inside a venv. Red: the command inside a venv carries --user.
        # Mutation: pip run with sys.executable, pythonw. Red: the command's first word is pythonw.exe.
        # Mutation: CREATE_NO_WINDOW left out. Red: the flags miss it.
        for venv, user in ((False, ["--user"]), (True, [])):
            with self.subTest(venv=venv):
                made, get, run = self.make("auto", "0.1.6", runs=[(0, "")], venv=venv)
                made.start()
                made.thread.join(5)
                self.assertFalse(made.thread.is_alive())
                self.assertEqual(made.snapshot(), {"state": "ready", "version": "0.1.6", "error": None})
                self.assertEqual(len(run.calls), 1)
                command, options, thread = run.calls[0]
                self.assertEqual(command, [PYTHON, "-m", "pip", "install", *user, "--upgrade", "--force-reinstall",
                                           "--no-deps", "--no-warn-script-location", "pendify==0.1.6"])
                self.assertIs(thread, made.thread)
                self.assertIsNot(thread, threading.main_thread())
                self.assertEqual(options["creationflags"] & (CREATE_NO_WINDOW | DETACHED_PROCESS),
                                 CREATE_NO_WINDOW | DETACHED_PROCESS)
                self.assertEqual((options["timeout"], options["stdin"], options["capture_output"]),
                                 (600, subprocess.DEVNULL, True))
                # Auto mode installs and waits for the page's «Reiniciar ahora»: it never restarts by itself.
                self.assertEqual((self.restarts, made.restart_requested.is_set()), ([], False))

    def test_a_disk_that_already_holds_the_version_skips_pip(self):
        # Mutation: the fresh read of the disk dropped. Red: pip runs for a version the disk holds.
        made, get, run = self.make("auto", "0.1.6", disk="0.1.6")
        made.round()
        self.assertEqual(made.snapshot(), {"state": "ready", "version": "0.1.6", "error": None})
        self.assertEqual(run.calls, [])

    def test_a_failed_pip_reads_failed_with_its_last_line_and_is_not_retried_in_the_same_process(self):
        # Mutation: a failed state checked again at the next round. Red: a second GET and a second pip run.
        # Mutation: the first stderr line kept. Red: the kept line reads "Collecting pendify==0.1.6".
        made, get, run = self.make("auto", "0.1.6", runs=[(1, f"Collecting pendify==0.1.6\n{PIP_LINE}\n\n")])
        made.round()
        failed = {"state": "failed", "version": "0.1.6", "error": PIP_LINE}
        self.assertEqual(made.snapshot(), failed)
        made.round()
        made.round()
        self.assertEqual((made.snapshot(), len(get.requests), len(run.calls)), (failed, 1, 1))
        self.assertEqual(len(self.logs), 1)
        # The next start tries again.
        again, _, run = self.make("auto", "0.1.6", runs=[(0, "")])
        again.round()
        self.assertEqual((again.snapshot()["state"], len(run.calls)), ("ready", 1))
        # A pip that cannot start, or that runs past the bound, reads failed with its exception's name.
        for failure, line in ((FileNotFoundError(2, "not found"), "FileNotFoundError"),
                              (subprocess.TimeoutExpired("pip", 600), "TimeoutExpired"),
                              ((2, ""), "pip ended 2")):
            with self.subTest(line=line):
                made, _, _ = self.make("auto", "0.1.6", runs=[failure])
                made.round()
                self.assertEqual(made.snapshot(), {"state": "failed", "version": "0.1.6", "error": line})

    def test_notify_mode_installs_only_on_the_page_request(self):
        # Mutation: notify installs as auto does. Red: pip runs at the round. Mutation: no restart after the
        # page's install. Red: the restart flag stays down.
        made, get, run = self.make("notify", "0.1.6", runs=[(0, "")])
        self.assertFalse(made.request_install())  # nothing seen yet: nothing to install
        made.round()
        made.round()
        self.assertEqual(made.snapshot(), {"state": "available", "version": "0.1.6", "error": None})
        self.assertEqual((run.calls, self.restarts), ([], []))
        self.assertTrue(made.request_install())
        self.assertTrue(wait_until(lambda: self.restarts))
        self.assertEqual(made.snapshot(), {"state": "ready", "version": "0.1.6", "error": None})
        self.assertTrue(made.restart_requested.is_set())
        self.assertEqual((len(run.calls), self.restarts), (1, [True]))
        self.assertIsNot(run.calls[0][2], threading.main_thread())
        self.assertFalse(made.request_install())  # ready: nothing more to install
        # Auto mode takes no request from the page: it installs by itself.
        made, _, run = self.make("auto", "0.1.6", runs=[(1, PIP_LINE)])
        self.assertFalse(made.request_install())
        self.assertEqual(run.calls, [])

    def test_a_network_failure_stays_silent_and_the_next_round_runs(self):
        # Mutation: the loop ends on a failed check. Red: one GET only and nothing seen.
        for failure in (urllib.error.URLError("unreachable"), TimeoutError("timed out"), ValueError("not json")):
            with self.subTest(failure=type(failure).__name__):
                waits = Waits(2)
                made, get, run = self.make("notify", failure, "0.1.6", waits=waits)
                made.run()
                self.assertEqual(len(get.requests), 2)
                self.assertEqual(made.snapshot(), {"state": "available", "version": "0.1.6", "error": None})
                self.assertEqual(self.logs, [f"the check failed: {type(failure).__name__}"])
        # An answer that is not PyPI's shape fails the same way.
        made = update.Updater("0.1.5", "notify", check=lambda: update.check(urlopen=lambda request, timeout: Answer(
            b"[]")), clock=Waits(1), log=self.logs.append)
        made.round()
        self.assertEqual(made.snapshot(), NOTHING)

    def test_off_mode_never_checks(self):
        # Mutation: off read as notify. Red: a GET at the round.
        made, get, run = self.make("off", "0.1.6", waits=Waits(3))
        made.start()
        self.assertIsNone(made.thread)
        made.run()
        made.round()
        self.assertFalse(made.request_install())
        self.assertEqual((get.requests, run.calls, made.snapshot()), ([], [], NOTHING))
        with self.assertRaises(ValueError):
            update.Updater("0.1.5", "on")

    def test_the_first_round_waits_a_minute_and_the_next_six_hours(self):
        # Mutation: the first round at once. Red: the first wait is 0.
        waits = Waits(3)
        made, get, run = self.make("notify", "0.1.5", waits=waits)
        made.run()
        self.assertEqual(waits.waited, [60, 21600, 21600, 21600])
        self.assertEqual(len(get.requests), 3)
        # With no clock given the wait is the close's: a copy that stops before the minute never checks.
        get = FakeGet("0.1.6")
        made = update.Updater("0.1.5", "auto", check=lambda: update.check(urlopen=get), log=self.logs.append)
        made.start()
        self.assertTrue(made.thread.daemon)
        made.close()
        made.thread.join(2)
        self.assertFalse(made.thread.is_alive())
        self.assertEqual(get.requests, [])


if __name__ == "__main__":
    unittest.main()
