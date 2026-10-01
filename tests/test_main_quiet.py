"""QuietStartTest: --quiet, a start by the system at logon, on the seams of MainCommandTest.

A quiet start never calls the opener and shows no message box for a start that ends well (already running, the
page not known yet); a start that fails still shows its line. Without the flag both behave as before.
"""
import json
import os
import threading
import unittest

import support
import test_main as main_test

config = support.module("config")
entry = support.module("__main__")

PAGE_LINE = r"^page: http://127\.0\.0\.1:\d+/\n$"
WORKER = "http://127.0.0.1:9"


class QuietStartTest(unittest.TestCase):
    setUp = main_test.MainCommandTest.setUp
    run_main = main_test.MainCommandTest.run_main

    def started(self, *flags, opened, console=False, **kwargs):
        stop = threading.Event()
        stop.set()  # one tick: the start goes through the opener's place and ends
        return self.run_main("--data-dir", str(self.data), "--worker", WORKER, *flags, stop=stop,
                             opener=lambda url: opened.append(url) or True, console=lambda: console, **kwargs)

    def test_a_quiet_start_with_no_pair_opens_nothing_and_still_prints_the_page_line(self):
        # Mutation: the opener's place at the page left as it was. Red: the browser is opened at logon.
        opened = []
        code, out, err = self.started("--quiet", opened=opened)
        self.assertEqual((code, err, opened, self.boxes), (0, "", [], []))
        self.assertRegex(out, PAGE_LINE)
        self.assertTrue(self.store.read().secret)  # the pairing page served as ever, its pair made
        code, out, err = self.started(opened=opened)  # without the flag, as today: the page opens
        self.assertEqual((code, err, len(opened), self.boxes), (0, "", 1, []))
        self.assertEqual(out, f"page: {opened[0]}\n")

    def holder(self, port):
        self.run_file.parent.mkdir(parents=True, exist_ok=True)
        self.run_file.write_text(json.dumps({"pid": os.getppid(), "port": port}), encoding="utf-8")

    def test_a_quiet_start_against_a_running_instance_opens_nothing_shows_no_box_and_exits_0(self):
        # Mutation: the opener's place at a second start left as it was. Red: the running page is opened.
        # Mutation: the box shown for a quiet start that ends well. Red: a message box at logon.
        # Mutation: the quiet start says the opening line. Red: "opening the page" printed while nothing opens.
        self.holder(54321)
        opened = []
        self.assertEqual(self.started("--quiet", opened=opened), (0, "already running\n", ""))
        self.assertEqual((opened, self.boxes), ([], []))
        self.assertEqual(self.started(opened=opened), (0, entry.ALREADY_RUNNING_LINE + "\n", ""))
        self.assertEqual((opened, self.boxes), (["http://127.0.0.1:54321/"], [entry.ALREADY_RUNNING_LINE]))

    def test_a_quiet_start_whose_running_page_is_not_known_shows_no_box_and_exits_0(self):
        # Mutation: the box shown for the page not known yet. Red: a message box at logon.
        self.run_file.parent.mkdir(parents=True)
        for flags, shown in ((("--quiet",), []), ((), [entry.PAGE_NOT_KNOWN_LINE])):
            with self.subTest(flags=flags):
                del self.boxes[:]
                with open(self.run_file, "w", encoding="utf-8") as held:  # the winner's handle, no port ever

                    def write(held=held):
                        held.seek(0)
                        held.truncate()
                        json.dump({"pid": os.getppid(), "port": None}, held)
                        held.flush()

                    clock, opened = main_test.FakeClock(write), []
                    result = self.started(*flags, opened=opened, clock=clock.clock, sleep=clock.sleep)
                self.assertEqual((result, opened, self.boxes), ((0, entry.PAGE_NOT_KNOWN_LINE + "\n", ""), [], shown))

    def test_a_quiet_start_that_fails_still_shows_its_line(self):
        # Mutation: every box silenced under --quiet. Red: a failed start at logon says nothing.
        self.run_file.parent.mkdir(parents=True)
        self.run_file.mkdir()  # a folder in the run file's place: no start can claim it
        line = entry.CLAIM_FAILED_LINE.format(path=self.run_file)
        opened = []
        self.assertEqual(self.started("--quiet", opened=opened), (1, line + "\n", ""))
        self.assertEqual((opened, self.boxes), ([], [line]))
        self.run_file.rmdir()
        self.store.path.mkdir()  # a folder in the config file's place: it can be neither read nor written
        line = config.UNAVAILABLE.format(path=self.store.path)
        del self.boxes[:]
        self.assertEqual(self.started("--quiet", opened=opened), (1, line + "\n", ""))
        self.assertEqual((opened, self.boxes), ([], [line]))


if __name__ == "__main__":
    unittest.main()
