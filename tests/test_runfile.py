"""RunFileTest: the run file's record, {pid, port}, the closing mark its holder writes beside them at its stop,
the bounded retry of a write or remove a reader refuses, and the wait's end when the closing holder's pid dies."""
import json
import os
import unittest
from pathlib import Path
from unittest import mock

import support

runfile = support.module("runfile")

HOLDER, OTHER = 4242, 4343  # two pids the alive seam answers for: no process is asked
PORT = 54321


class RunFileTest(unittest.TestCase):
    def setUp(self):
        tmp = support.temp_dir()
        self.addCleanup(tmp.cleanup)
        self.folder = Path(tmp.name) / "a config folder"
        self.path = self.folder / runfile.FILE_NAME

    def start(self, pid):
        return runfile.RunFile(self.folder, pid=pid, alive=lambda pid: True)

    def record(self):
        return json.loads(self.path.read_text(encoding="utf-8"))

    def held_start(self):
        """A start holding the file with its port published, whose waits are only recorded: no real sleep."""
        sleeps = []
        held = runfile.RunFile(self.folder, pid=HOLDER, alive=lambda pid: True, sleep=sleeps.append)
        self.assertIsNone(held.claim())
        held.publish(PORT)
        return held, sleeps

    def refused(self, name, times):
        """os.<name> refused `times` times, as Windows refuses it while a reader holds the file, then the real
        one; the calls are counted."""
        real, calls = getattr(os, name), []

        def move(*args):
            calls.append(args)
            if len(calls) <= times:
                raise PermissionError(13, "the file is held by a reader", str(args[-1]))
            return real(*args)

        return mock.patch.object(runfile.os, name, move), calls

    def temps(self):
        return sorted(path.name for path in self.folder.iterdir() if path.name != runfile.FILE_NAME)

    def test_the_closing_mark_is_written_beside_pid_and_port_and_read_back(self):
        # Mutation: the mark writes the record without its closing key. Red: the record reads as not closing.
        held = self.start(HOLDER)
        self.assertIsNone(held.claim())
        held.publish(PORT)
        self.assertEqual(self.record(), {"pid": HOLDER, "port": PORT})  # a running holder's record, as before
        held.mark_closing()
        self.assertEqual(self.record(), {"pid": HOLDER, "port": PORT, "closing": True})
        self.assertEqual(self.start(OTHER).claim(), {"pid": HOLDER, "port": PORT, "closing": True})
        self.assertEqual(sorted(path.name for path in self.folder.iterdir()), [runfile.FILE_NAME])  # no temp left

    def test_a_record_without_the_mark_reads_as_not_closing(self):
        # Mutation: a record with no closing key read as closing. Red: an older copy's running page is waited for.
        self.folder.mkdir(parents=True)
        for name, record in (("written by an older copy", {"pid": HOLDER, "port": PORT}),
                             ("marked false", {"pid": HOLDER, "port": PORT, "closing": False})):
            with self.subTest(record=name):
                self.path.write_text(json.dumps(record), encoding="utf-8")
                self.assertEqual(self.start(OTHER).claim(), {"pid": HOLDER, "port": PORT, "closing": False})
                self.assertEqual(self.record(), record)  # read, never rewritten

    def test_release_after_the_mark_removes_the_file_as_before(self):
        # Mutation: release keeps a record that carries the mark. Red: the file is still there after the stop.
        held = self.start(HOLDER)
        self.assertIsNone(held.claim())
        held.publish(PORT)
        held.mark_closing()
        held.release()
        self.assertFalse(self.path.exists())
        self.assertIsNone(self.start(OTHER).claim())  # the next start claims it as a first one does


    def test_a_remove_refused_by_a_reader_is_retried_and_then_removes_the_file(self):
        # Mutation: release catches only FileNotFoundError. Red: the refusal raises out of the stop.
        held, sleeps = self.held_start()
        refusal, calls = self.refused("remove", 2)
        with refusal:
            held.release()
        self.assertFalse(self.path.exists())
        self.assertEqual((len(calls), sleeps), (3, [runfile.REFUSED_STEP] * 2))

    def test_a_remove_refused_to_the_end_leaves_the_file_and_does_not_raise(self):
        # Mutation: the last refusal raised. Red: the stop ends on a traceback; the waiter takes the file over
        # once this pid is gone, so the file left is the base's outcome.
        held, sleeps = self.held_start()
        refusal, calls = self.refused("remove", runfile.REFUSED_TRIES)
        with refusal:
            held.release()
        self.assertEqual(self.record(), {"pid": HOLDER, "port": PORT})
        self.assertEqual((len(calls), sleeps),
                         (runfile.REFUSED_TRIES, [runfile.REFUSED_STEP] * (runfile.REFUSED_TRIES - 1)))
        self.assertLess(runfile.REFUSED_STEP * (runfile.REFUSED_TRIES - 1), 1.0)  # well under a second in all

    def test_a_replace_refused_by_a_reader_is_retried_and_then_writes_the_record(self):
        # Mutation: _write calls os.replace once. Red: the closing mark is refused and the record reads not closing.
        held, sleeps = self.held_start()
        refusal, calls = self.refused("replace", 2)
        with refusal:
            held.mark_closing()
        self.assertEqual(self.record(), {"pid": HOLDER, "port": PORT, "closing": True})
        self.assertEqual((len(calls), sleeps, self.temps()), (3, [runfile.REFUSED_STEP] * 2, []))

    def test_a_publish_refused_to_the_end_raises_and_leaves_no_temp(self):
        # Mutation: the last refusal swallowed. Red: a start that cannot publish runs unseen.
        sleeps = []
        start = runfile.RunFile(self.folder, pid=HOLDER, alive=lambda pid: True, sleep=sleeps.append)
        self.assertIsNone(start.claim())
        refusal, calls = self.refused("replace", runfile.REFUSED_TRIES)
        with refusal, self.assertRaises(PermissionError):
            start.publish(PORT)
        self.assertEqual(self.record(), {"pid": HOLDER, "port": None})  # the claim's record, untouched
        self.assertEqual((len(calls), sleeps, self.temps()),
                         (runfile.REFUSED_TRIES, [runfile.REFUSED_STEP] * (runfile.REFUSED_TRIES - 1), []))

    def test_the_wait_ends_when_the_closing_holders_pid_dies_and_the_start_takes_the_file_over(self):
        # Mutation: the wait reads the record (_read) instead of its live holder. Red: the wait runs to its bound
        # and returns False while the file still names a pid that is gone.
        self.folder.mkdir(parents=True)
        self.path.write_text(json.dumps({"pid": HOLDER, "port": PORT, "closing": True}), encoding="utf-8")
        alive, now = {HOLDER}, [0.0]

        def sleep(seconds):  # the closing copy dies at the start's first wait; its file stays
            alive.discard(HOLDER)
            now[0] += seconds

        start = runfile.RunFile(self.folder, pid=OTHER, alive=lambda pid: pid in alive, clock=lambda: now[0],
                                sleep=sleep)
        holder = start.claim()
        self.assertEqual(holder, {"pid": HOLDER, "port": PORT, "closing": True})
        self.assertTrue(start.wait_released(holder, 15.0))
        self.assertEqual((self.record()["pid"], now[0]), (HOLDER, runfile.REREAD_STEP))  # ended by the pid alone
        self.assertIsNone(start.claim())  # taken over
        self.assertEqual(self.record(), {"pid": OTHER, "port": None})


if __name__ == "__main__":
    unittest.main()
