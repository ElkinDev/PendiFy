"""GameLogStartTest: the match's start read from the game's own log, for a game that serves no clock.

The game writes its log live, UTF-8 with a BOM and CRLF lines, and writes one engine line, the welcome, when it
joins the match. The reader answers whether the log holds a welcome stamped at or after a time, reading on from
where it stopped; between calls it keeps an offset and nothing of the file. Every file here is written by the
test, under build/tmp, in the log's shape with lines made up for it; no line of a real log is used.
"""
import calendar
import inspect
import os
import time
import unittest
from pathlib import Path

import support

client = support.module("client")

BOM = b"\xef\xbb\xbf"
WELCOME_TEXT = "LogNet: Welcomed by server (Level: /Fixture/Board, Game: /Fixture/Mode)"
OTHER_TEXT = "LogFixture: a line the test wrote"
JOIN = calendar.timegm((2031, 4, 5, 6, 7, 8, 0, 0, 0)) + 0.5  # the welcome's stamp, 2031.04.05-06.07.08:500 UTC
SINCE = JOIN - 30.0


def line(epoch, text, frame=7):
    """One line of the log's shape: the UTC stamp to the millisecond, the frame, the text, CRLF."""
    whole = int(epoch)
    stamp = time.strftime("%Y.%m.%d-%H.%M.%S", time.gmtime(whole))
    return f"[{stamp}:{round((epoch - whole) * 1000):03d}][{frame:3d}]{text}\r\n".encode("utf-8")


def welcome(epoch):
    return line(epoch, WELCOME_TEXT)


def others(epoch, count=1):
    return b"".join(line(epoch, OTHER_TEXT, frame) for frame in range(count))


class CountingOpener:
    """open() that keeps the mode of each open and the size of each read; nothing of the file is kept."""

    def __init__(self):
        self.modes, self.reads = [], []

    def __call__(self, path, mode="r"):
        self.modes.append(mode)
        return Counted(open(path, mode), self.reads)


class Counted:
    def __init__(self, handle, reads):
        self._handle, self._reads = handle, reads

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self._handle.close()

    def seek(self, *args):
        return self._handle.seek(*args)

    def read(self, size=-1):
        data = self._handle.read(size)
        self._reads.append(len(data))
        return data


class GameLogStartTest(unittest.TestCase):
    def setUp(self):
        tmp = support.temp_dir()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "TFT.log"
        self.opener = CountingOpener()

    def write(self, *parts):
        """The file replaced by `parts`, as bytes."""
        self.path.write_bytes(b"".join(parts))

    def append(self, *parts):
        with open(self.path, "ab") as handle:
            handle.write(b"".join(parts))

    def reader(self, **kwargs):
        kwargs.setdefault("path", lambda: str(self.path))
        kwargs.setdefault("opener", self.opener)
        return client.GameLogStart(**kwargs)

    def test_the_names_and_values_of_the_game_s_log(self):
        # Mutation: the mark made "Welcomed", or the cap 32 MB. Red: the values differ.
        self.assertEqual((client.GAME_LOG_PARTS, client.GAME_LOG_MARK), (("TFT", "Saved", "Logs", "TFT.log"),
                                                                        b"LogNet: Welcomed by server"))
        self.assertEqual((client.GAME_LOG_MAX_BYTES, client.GAME_LOG_CHUNK), (16 * 1024 * 1024, 256 * 1024))
        parameters = inspect.signature(client.GameLogStart).parameters
        self.assertEqual((parameters["path"].default, parameters["opener"].default), (client.real_game_log, open))
        self.assertIs(inspect.signature(client.real_game_log).parameters["environ"].default, os.environ)
        local = r"C:\Fixture\Local"
        self.assertEqual(Path(client.real_game_log({"LOCALAPPDATA": local})),
                         Path(local, "TFT", "Saved", "Logs", "TFT.log"))
        for environ in ({}, {"LOCALAPPDATA": ""}, {"LOCALAPPDATA": "  "}):
            self.assertIsNone(client.real_game_log(environ), environ)

    def test_a_welcome_stamped_at_or_after_since_answers_true(self):
        # (a) Mutation: the milliseconds not added. Red: the welcome at :500 read as :000, before a since at :400.
        self.write(BOM, others(JOIN - 60, 3), welcome(JOIN), others(JOIN + 1, 2))
        self.assertIs(self.reader()(SINCE), True)
        self.assertIs(self.reader()(JOIN - 0.1), True)
        self.assertIs(self.reader()(JOIN), True)  # at the stamp itself
        self.assertIs(self.reader()(JOIN + 0.001), False)
        self.assertEqual(set(self.opener.modes), {"rb"})

    def test_a_welcome_before_since_answers_false_and_a_newer_one_appended_is_found_reading_only_the_new_bytes(self):
        # (b) Mutation: the stamp comparison dropped. Red: the older welcome answers True.
        self.write(BOM, others(JOIN - 3700), welcome(JOIN - 3600), others(JOIN - 3500, 4))
        subject = self.reader()
        self.assertIs(subject(SINCE), False)
        self.assertEqual(sum(self.opener.reads), self.path.stat().st_size)
        appended = others(JOIN - 1) + welcome(JOIN) + others(JOIN + 1)
        self.append(appended)
        first = len(self.opener.reads)
        self.assertIs(subject(SINCE), True)
        self.assertEqual(sum(self.opener.reads[first:]), len(appended))
        self.assertIs(self.reader()(SINCE), True)  # in one scan the older welcome is skipped and the scan goes on

    def test_a_welcome_line_split_across_two_writes_is_found_once_whole(self):
        # (c) Mutation: the offset moved past a partial last line. Red: only the line's end is read, no welcome.
        whole = welcome(JOIN)
        cut = whole.index(b"Welcomed") + 4  # the first write ends inside the mark
        self.write(BOM, others(JOIN - 1, 2), whole[:cut])
        subject = self.reader()
        self.assertIs(subject(SINCE), False)
        self.append(whole[cut:], others(JOIN + 1))
        self.assertIs(subject(SINCE), True)

    def test_a_file_replaced_by_a_shorter_one_is_read_from_its_start(self):
        # (d) Mutation: the size check dropped. Red: the new file is read from the old offset, past its end.
        self.write(BOM, others(JOIN - 7200, 40))
        subject = self.reader()
        self.assertIs(subject(SINCE), False)
        before = self.path.stat().st_size
        self.write(BOM, others(JOIN - 2), welcome(JOIN))
        self.assertLess(self.path.stat().st_size, before)
        self.assertIs(subject(SINCE), True)

    def test_no_path_a_missing_file_a_failing_read_or_a_stamp_that_does_not_parse_answers_false(self):
        # (e) Mutation: OSError not caught, or a bad stamp raising. Red: the call raises.
        self.write(BOM, others(JOIN - 1), welcome(JOIN))
        self.assertIs(self.reader(path=lambda: None)(SINCE), False)
        self.assertIs(self.reader(path=lambda: str(self.path.with_name("missing.log")))(SINCE), False)
        self.assertIs(self.reader(path=lambda: str(self.path.parent))(SINCE), False)  # a folder

        def refusing(path, mode="r"):
            raise PermissionError(13, "refused")

        class Breaking(Counted):
            def read(self, size=-1):
                raise OSError(5, "the read broke")

        self.assertIs(self.reader(opener=refusing)(SINCE), False)
        self.assertIs(self.reader(opener=lambda path, mode="r": Breaking(open(path, mode), []))(SINCE), False)
        self.write(BOM, others(JOIN - 1), welcome(JOIN)[:-2])  # a short read: the welcome has no line end yet
        self.assertIs(self.reader()(SINCE), False)
        for stamp in ("2031.13.05-06.07.08:500", "2031.04.05-06.07.08", "2031.04.05-06.07.08:5",
                      "2031.04.05-06.07.08:5x0", "2031-04-05 06:07:08:500", "not a stamp", ""):
            with self.subTest(stamp=stamp):
                self.write(BOM, others(JOIN - 1), f"[{stamp}][  7]{WELCOME_TEXT}\r\n".encode("utf-8"))
                self.assertIs(self.reader()(SINCE), False)
        mark_line = WELCOME_TEXT.encode("utf-8") + b"\r\n"
        for broken in (mark_line, b"[\xff\xfe][  7]" + mark_line):  # no stamp at all, a stamp that is not text
            with self.subTest(broken=broken[:8]):
                self.write(BOM, broken)
                self.assertIs(self.reader()(SINCE), False)
        # A welcome whose stamp does not parse is no welcome: a whole one after it is still found.
        self.write(BOM, f"[not a stamp][  7]{WELCOME_TEXT}\r\n".encode("utf-8"), welcome(JOIN))
        self.assertIs(self.reader()(SINCE), True)

    def test_one_call_reads_at_most_the_cap_and_the_next_call_reads_on_from_where_it_stopped(self):
        # (f) Mutation: the cap dropped. Red: the first call reads the whole file and finds the welcome.
        block = others(JOIN - 60, 100)
        self.write(BOM, block * (client.GAME_LOG_MAX_BYTES // len(block) + 2), welcome(JOIN))
        self.assertGreater(self.path.stat().st_size, client.GAME_LOG_MAX_BYTES + len(block))
        subject = self.reader()
        self.assertIs(subject(SINCE), False)
        self.assertLessEqual(sum(self.opener.reads), client.GAME_LOG_MAX_BYTES)
        self.assertLessEqual(max(self.opener.reads), client.GAME_LOG_CHUNK)
        self.assertIs(subject(SINCE), True)

    def test_after_a_true_the_reader_keeps_its_offset_and_nothing_of_the_file(self):
        # (g) Mutation: the last chunk kept on the object. Red: bytes of the file in vars().
        self.write(BOM, others(JOIN - 1, 2), welcome(JOIN), others(JOIN + 1))
        subject = self.reader()
        self.assertIs(subject(SINCE), True)
        kept = vars(subject)
        for name, value in kept.items():
            self.assertNotIsInstance(value, (bytes, bytearray, memoryview, str, list, tuple, dict, set), name)
        for text in ("Welcomed", "Fixture"):
            self.assertNotIn(text, repr(kept))
        self.assertEqual(sorted(type(value).__name__ for value in kept.values()),
                         ["CountingOpener", "function", "int"])


if __name__ == "__main__":
    unittest.main()
