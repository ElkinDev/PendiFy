"""RunFileTest: the run file's record, {pid, port}, and the closing mark its holder writes beside them at its stop."""
import json
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
