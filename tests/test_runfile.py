"""RunFileTest: the run file's record, {pid, port}, the closing mark its holder writes beside them at its stop,
the bounded retry of a write or remove a reader refuses, and the wait's end when the closing holder's pid dies."""
import json
import os
import time
import unittest
from pathlib import Path
from unittest import mock

import support

runfile = support.module("runfile")
update = support.module("update")

HOLDER, OTHER = 4242, 4343  # two pids the alive seam answers for: no process is asked
PORT = 54321
HOLDER_VERSION = "1.2.3"  # a test vector, the version the holder runs
STARTED = 133_000_000_000_000_000  # a test vector: a creation time as GetProcessTimes gives it, 100 ns ticks
MTIME = 1_000_000_000.0  # a test vector: the run file's last write, in seconds since the epoch
# Creation times a minute before and after MTIME, as FILETIME counts them: 100 ns ticks since 1601.
BEFORE_MTIME = (int(MTIME) - 60 + 11_644_473_600) * 10**7
AFTER_MTIME = (int(MTIME) + 60 + 11_644_473_600) * 10**7
DAYS_BACK = MTIME - 3 * 86_400  # the boot GetTickCount64 gives after a Fast Startup shutdown: the last cold boot


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
        published = {"pid": HOLDER, "port": PORT, "version": update.RUNNING_VERSION, "secret": held.secret,
                     "started": held.started}
        self.assertEqual(self.record(), published)  # a running holder's record
        held.mark_closing()
        self.assertEqual(self.record(), {**published, "closing": True})
        self.assertEqual(self.start(OTHER).claim(), {**published, "closing": True})
        self.assertEqual(sorted(path.name for path in self.folder.iterdir()), [runfile.FILE_NAME])  # no temp left

    def test_a_record_without_the_mark_reads_as_not_closing(self):
        # Mutation: a record with no closing key read as closing. Red: an older copy's running page is waited for.
        self.folder.mkdir(parents=True)
        for name, record in (("written by an older copy", {"pid": HOLDER, "port": PORT}),
                             ("marked false", {"pid": HOLDER, "port": PORT, "closing": False})):
            with self.subTest(record=name):
                self.path.write_text(json.dumps(record), encoding="utf-8")
                self.assertEqual(self.start(OTHER).claim(),
                                 {"pid": HOLDER, "port": PORT, "closing": False, "version": None, "secret": None,
                                  "started": None})
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
        self.assertEqual(self.record(), {"pid": HOLDER, "port": PORT, "version": update.RUNNING_VERSION,
                                         "secret": held.secret, "started": held.started})
        self.assertEqual((len(calls), sleeps),
                         (runfile.REFUSED_TRIES, [runfile.REFUSED_STEP] * (runfile.REFUSED_TRIES - 1)))
        self.assertLess(runfile.REFUSED_STEP * (runfile.REFUSED_TRIES - 1), 1.0)  # well under a second in all

    def test_a_replace_refused_by_a_reader_is_retried_and_then_writes_the_record(self):
        # Mutation: _write calls os.replace once. Red: the closing mark is refused and the record reads not closing.
        held, sleeps = self.held_start()
        refusal, calls = self.refused("replace", 2)
        with refusal:
            held.mark_closing()
        self.assertEqual(self.record(), {"pid": HOLDER, "port": PORT, "version": update.RUNNING_VERSION,
                                         "secret": held.secret, "started": held.started, "closing": True})
        self.assertEqual((len(calls), sleeps, self.temps()), (3, [runfile.REFUSED_STEP] * 2, []))

    def test_a_publish_refused_to_the_end_raises_and_leaves_no_temp(self):
        # Mutation: the last refusal swallowed. Red: a start that cannot publish runs unseen.
        sleeps = []
        start = runfile.RunFile(self.folder, pid=HOLDER, alive=lambda pid: True, sleep=sleeps.append)
        self.assertIsNone(start.claim())
        refusal, calls = self.refused("replace", runfile.REFUSED_TRIES)
        with refusal, self.assertRaises(PermissionError):
            start.publish(PORT)
        # the claim's record, untouched
        self.assertEqual(self.record(), {"pid": HOLDER, "port": None, "version": update.RUNNING_VERSION})
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
        self.assertEqual(holder, {"pid": HOLDER, "port": PORT, "closing": True, "version": None, "secret": None,
                                  "started": None})
        self.assertTrue(start.wait_released(holder, 15.0))
        self.assertEqual((self.record()["pid"], now[0]), (HOLDER, runfile.REREAD_STEP))  # ended by the pid alone
        self.assertIsNone(start.claim())  # taken over
        self.assertEqual(self.record(), {"pid": OTHER, "port": None, "version": update.RUNNING_VERSION})

    def test_publish_writes_the_version_and_a_secret_and_claim_hands_them_back(self):
        # Mutation: publish writes {pid, port} as before. Red: the record carries no version and no secret.
        # Mutation: mark_closing writes {pid, port, closing} as before. Red: the closing record loses both.
        with mock.patch.object(update, "RUNNING_VERSION", HOLDER_VERSION):
            held = self.start(HOLDER)
            self.assertIsNone(held.claim())
            held.publish(PORT)
            record = self.record()
            secret = record.get("secret")
            self.assertRegex(str(secret), r"\A[0-9a-f]{32}\Z")
            self.assertEqual(getattr(held, "secret", None), secret)  # the secret the page's /replace answers to
            started = runfile.process_started()  # this process's creation time, the holder's identity
            self.assertEqual(record, {"pid": HOLDER, "port": PORT, "version": HOLDER_VERSION, "secret": secret,
                                      "started": started})
            claimed = {"pid": HOLDER, "port": PORT, "closing": False, "version": HOLDER_VERSION, "secret": secret,
                       "started": started}
            self.assertEqual(self.start(OTHER).claim(), claimed)
            held.mark_closing()
            self.assertEqual(self.record(), {**record, "closing": True})
            self.assertEqual(self.start(OTHER).claim(), {**claimed, "closing": True})

    def test_an_older_record_without_them_reads_version_none_and_secret_none(self):
        # Mutation: the record's version and secret handed back unchecked. Red: a number or an empty text comes
        # back as the holder's version or secret.
        self.folder.mkdir(parents=True)
        for name, record in (("an older copy's", {"pid": HOLDER, "port": PORT}),
                             ("neither a text", {"pid": HOLDER, "port": PORT, "version": 2, "secret": ["x"]}),
                             ("both empty", {"pid": HOLDER, "port": PORT, "version": "", "secret": ""})):
            with self.subTest(record=name):
                self.path.write_text(json.dumps(record), encoding="utf-8")
                self.assertEqual(self.start(OTHER).claim(),
                                 {"pid": HOLDER, "port": PORT, "closing": False, "version": None, "secret": None,
                                  "started": None})

    def stale(self, mtime=MTIME):
        """A run file a holder left, last written at `mtime`."""
        self.folder.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"pid": HOLDER, "port": PORT}), encoding="utf-8")
        os.utime(self.path, (mtime, mtime))

    def terminate(self, started, created, image, boot, ends=True):
        """runfile.terminate of HOLDER, `started` read from its record, every Windows call faked: the pid's process
        created at `created` from the image `image`, the system booted at `boot`, `ends` what the end answers. The
        answer and the (pid, creation time) pairs TerminateProcess was asked to end."""
        ended = []

        def end(pid, at):
            ended.append((pid, at))
            return ends

        answer = runfile.terminate(HOLDER, started, self.path, probe=lambda pid: (created, image),
                                   boot_instant=lambda: boot, end=end)
        return answer, ended

    def test_terminate_ends_the_pid_only_when_its_creation_time_equals_the_records(self):
        # Mutation: terminate without the creation-time compare. Red: a reused pid, its process created after the
        # record's holder, is ended.
        # Mutation: the end's answer ignored. Red: a pid reused between the read and the end reads ended.
        self.stale()
        for name, created, ends, expected in (
                ("the holder", STARTED, True, ("ended", [(HOLDER, STARTED)])),
                ("a reused pid", STARTED + 1, True, ("not-ours", [])),
                ("a pid reused between the read and the end", STARTED, False, ("not-ours", [(HOLDER, STARTED)]))):
            with self.subTest(process=name):
                self.assertEqual(self.terminate(STARTED, created, "pythonw.exe", MTIME - 3600, ends), expected)
        never = mock.Mock(side_effect=AssertionError("this process opened"))
        self.assertEqual(runfile.terminate(os.getpid(), STARTED, self.path, probe=never, boot_instant=never, end=never),
                         "not-ours")

    def test_an_older_record_is_ended_only_for_a_python_image_younger_than_the_boot(self):
        # Mutation: the image not read. Red: explorer.exe, the pid's image now, is ended.
        # Mutation: the boot not read. Red: the reused pid of a holder that died with the boot is ended.
        self.stale()
        for name, image, boot, word in (("python.exe, written after the boot", "python.exe", MTIME - 3, "ended"),
                                        ("pythonw.exe in capitals", "PythonW.EXE", MTIME - 3, "ended"),
                                        ("another image", "explorer.exe", MTIME - 3, "not-ours"),
                                        ("written within the margin", "pythonw.exe", MTIME - 1, "not-ours"),
                                        ("written before the boot", "pythonw.exe", MTIME + 3600, "not-ours")):
            with self.subTest(record=name):
                ended = [(HOLDER, BEFORE_MTIME)] if word == "ended" else []
                self.assertEqual(self.terminate(None, BEFORE_MTIME, image, boot), (word, ended))
        self.path.unlink()  # the file gone: nothing proves the pid is the holder's
        self.assertEqual(self.terminate(None, BEFORE_MTIME, "pythonw.exe", MTIME - 3600), ("not-ours", []))

    def test_an_older_record_is_not_ours_when_the_pid_started_after_the_file_was_written(self):
        # Mutation: the created-before-mtime test removed. Red: another python.exe of the user, started after a Fast
        # Startup shutdown left the record, holds its pid and is ended.
        self.stale()
        self.assertEqual(self.terminate(None, AFTER_MTIME, "python.exe", DAYS_BACK), ("not-ours", []))
        ended = []

        def gone(pid):  # the file removed between the boot test and the creation-time read: nothing proves the pid
            self.path.unlink()
            return BEFORE_MTIME, "python.exe"

        answer = runfile.terminate(HOLDER, None, self.path, probe=gone, boot_instant=lambda: DAYS_BACK,
                                   end=lambda pid, at: ended.append((pid, at)) or True)
        self.assertEqual((answer, ended), ("not-ours", []))

    def test_an_older_record_is_ended_when_the_pid_started_before_the_file_was_written(self):
        self.stale()
        self.assertEqual(self.terminate(None, BEFORE_MTIME, "python.exe", DAYS_BACK),
                         ("ended", [(HOLDER, BEFORE_MTIME)]))

    def test_terminate_answers_refused_on_an_oserror(self):
        # Mutation: the OSError raised out of terminate, as before. Red: no answer.
        self.stale()
        denied = PermissionError(13, "access denied")

        def refuse(*args):
            raise denied

        for name, probe, end in (("opening the pid", refuse, lambda pid, at: True),
                                 ("ending it", lambda pid: (STARTED, "pythonw.exe"), refuse)):
            with self.subTest(call=name):
                try:
                    answer = runfile.terminate(HOLDER, STARTED, self.path, probe=probe,
                                               boot_instant=lambda: MTIME - 3600, end=end)
                except OSError as error:
                    self.fail(f"raised {error!r}")
                self.assertEqual((answer, answer.error), ("refused", denied))

    def test_publish_writes_the_holders_start_time_and_claim_hands_it_back(self):
        # Mutation: publish writes no start time. Red: the record and the claim carry none.
        # Mutation: the start time handed back unchecked. Red: a text, a truth value or a fraction comes back.
        with mock.patch.object(runfile, "process_started", lambda: STARTED, create=True):
            held = self.start(HOLDER)
            self.assertIsNone(held.claim())
            held.publish(PORT)
            self.assertEqual((self.record().get("started"), self.start(OTHER).claim().get("started")),
                             (STARTED, STARTED))
            held.mark_closing()  # kept by the mark
            self.assertEqual((self.record().get("started"), self.start(OTHER).claim().get("started")),
                             (STARTED, STARTED))
        for name, value in (("a text", str(STARTED)), ("a truth value", True), ("a fraction", 1.5)):
            with self.subTest(started=name):
                self.path.write_text(json.dumps({"pid": HOLDER, "port": PORT, "started": value}), encoding="utf-8")
                self.assertIsNone(self.start(OTHER).claim().get("started", "missing"))
        own = runfile.process_started()  # this process's own creation time, read on its pseudo handle
        if os.name == "nt":  # FILETIME counts 100 ns from 1601; this test process started less than a day ago
            age = time.time() - (own / 10**7 - 11_644_473_600)
            self.assertTrue(0 < age < 86_400, age)
        else:
            self.assertIsNone(own)

    def test_evict_removes_the_file_only_while_it_still_holds_the_record_read(self):
        # Mutation: evict without the content check. Red: another start's record, written since, is removed.
        self.folder.mkdir(parents=True)
        stale = {"pid": HOLDER, "port": PORT, "version": HOLDER_VERSION, "secret": "s" * 32, "started": STARTED}
        self.path.write_text(json.dumps(stale), encoding="utf-8")
        start = self.start(OTHER)
        holder = start.claim()
        other = {"pid": HOLDER + 1, "port": PORT + 1}  # another start's record, written since the read
        self.path.write_text(json.dumps(other), encoding="utf-8")
        self.assertEqual((start.evict(holder), self.path.exists()), (False, True))
        self.assertEqual(self.record(), other)
        self.path.write_text(json.dumps(stale), encoding="utf-8")
        remove, calls = self.refused("remove", 2)
        with remove:  # refused twice by a reader, retried as release's remove is
            self.assertEqual((start.evict(holder), self.path.exists(), len(calls)), (True, False, 3))
        self.assertFalse(start.evict(holder))  # gone already: nothing to remove

    def evicting(self):
        """A stale record of HOLDER on disk and the start OTHER that read it: (that start, the record it read)."""
        self.folder.mkdir(parents=True)
        stale = {"pid": HOLDER, "port": PORT, "version": HOLDER_VERSION, "secret": "s" * 32, "started": STARTED}
        self.path.write_text(json.dumps(stale), encoding="utf-8")
        start = self.start(OTHER)
        return start, start.claim()

    def test_evict_renames_aside_compares_then_deletes(self):
        start, holder = self.evicting()
        moves, calls = self.refused("replace", 0)
        with moves:
            self.assertEqual((start.evict(holder), self.path.exists(), self.temps()), (True, False, []))
        self.assertEqual([tuple(map(os.fspath, call)) for call in calls],
                         [(os.fspath(self.path), f"{self.path}.evict-{OTHER}")])

    def test_evict_puts_back_a_record_it_did_not_read(self):
        # Mutation: evict's compare removed. Red: the fresh claim is removed.
        # Mutation: evict's put-back removed. Red: the fresh claim is left under the aside name, the run file gone.
        start, holder = self.evicting()
        fresh = {"pid": HOLDER + 1, "port": None}  # a claim written between this start's read and its move aside
        self.path.write_text(json.dumps(fresh), encoding="utf-8")
        moves, calls = self.refused("replace", 0)
        with moves:
            self.assertEqual((start.evict(holder), self.path.exists(), self.temps(), len(calls)), (False, True, [], 2))
        self.assertEqual(self.record(), fresh)

    def test_evict_answers_false_when_another_newcomer_renamed_first(self):
        start, holder = self.evicting()
        stale, real = self.record(), os.replace
        theirs = Path(f"{self.path}.evict-{HOLDER + 1}")

        def theirs_first(source, target):  # another newcomer's move aside lands just before this start's
            if not theirs.exists():
                real(self.path, theirs)
            return real(source, target)

        with mock.patch.object(runfile.os, "replace", theirs_first):
            self.assertEqual((start.evict(holder), self.path.exists()), (False, False))
        self.assertEqual(json.loads(theirs.read_text(encoding="utf-8")), stale)  # theirs, untouched
        theirs.replace(self.path)
        moves, calls = self.refused("replace", runfile.REFUSED_TRIES)
        with moves:  # held by another newcomer to the end of the retries
            self.assertEqual((start.evict(holder), len(calls)), (False, runfile.REFUSED_TRIES))
        self.assertEqual((self.record(), self.temps()), (stale, []))

    def test_claim_takes_a_dead_holders_file_over_only_while_it_still_holds_the_record_read(self):
        # Mutation: the take-over without the compare. Red: another start's fresh claim, written between this start's
        # read and its move aside, is removed and both starts hold the file.
        self.stale()
        fresh, real, written = {"pid": OTHER + 1, "port": None}, os.replace, []

        def claimed_first(source, target):
            if not written:
                written.append(source)
                self.path.write_text(json.dumps(fresh), encoding="utf-8")
            return real(source, target)

        start = runfile.RunFile(self.folder, pid=OTHER, alive=lambda pid: pid != HOLDER)  # HOLDER died
        with mock.patch.object(runfile.os, "replace", claimed_first):
            self.assertEqual(start.claim(), {"pid": OTHER + 1, "port": None, "closing": False, "version": None,
                                             "secret": None, "started": None})
        self.assertEqual((self.record(), self.temps()), (fresh, []))


if __name__ == "__main__":
    unittest.main()
