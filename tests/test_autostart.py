"""AutostartTest: the start with Windows, the per-user Run value, no administrator.

The registry is a seam of three calls on one value under one key path. Most cases run on a MemoryRegistry; the
real registry class runs only against a scratch key, HKCU\\Software\\pcnotify-test-<random>, which the case
creates and removes even when it fails. No case reads or writes the real Run key: its path is asserted as text.
"""
import contextlib
import io
import re
import sys
import unittest
import uuid
from pathlib import Path

import support
from support import MemoryRegistry

autostart = support.module("autostart")

try:
    import winreg
except ImportError:  # not Windows
    winreg = None

NEEDS_WINREG = unittest.skipIf(winreg is None, "this platform has no winreg, so no Run value")
SCRATCH_PREFIX = r"Software\pcnotify-test-"


def remove_scratch_key(key_path):
    """The scratch key and its one value gone, whatever the case left; never a key outside the scratch prefix."""
    if not key_path.startswith(SCRATCH_PREFIX):
        raise AssertionError(f"not a scratch key: {key_path}")
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, "pcnotify")
    except FileNotFoundError:
        pass
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key_path)
    except FileNotFoundError:
        pass


def scratch_key(case):
    """A new scratch key under HKCU, created now and removed at the case's cleanup."""
    key_path = SCRATCH_PREFIX + uuid.uuid4().hex
    winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path).Close()
    case.addCleanup(remove_scratch_key, key_path)
    return key_path


class AutostartTest(unittest.TestCase):
    def setUp(self):
        tmp = support.temp_dir()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.folder = self.base / "a python folder"
        self.folder.mkdir()
        self.python = self.folder / "python.exe"
        self.python.write_bytes(b"")

    def subject(self, registry=None, executable=None):
        return autostart.Autostart(registry=registry if registry is not None else MemoryRegistry(),
                                   executable=str(executable or self.python))

    def test_the_run_value_is_the_per_user_one_named_by_the_program_as_text_only(self):
        # Mutation: the machine-wide key or another value name. Red: the texts differ.
        self.assertEqual(autostart.RUN_KEY, r"Software\Microsoft\Windows\CurrentVersion\Run")
        self.assertEqual(autostart.VALUE_NAME, "pcnotify")
        if winreg is not None:  # built, never called: the real class reads nothing until asked
            self.assertEqual(autostart.WindowsRegistry().key_path, autostart.RUN_KEY)
            self.assertEqual(autostart.Autostart(executable=str(self.python)).registry.key_path, autostart.RUN_KEY)

    def test_enable_writes_exactly_the_command_disable_deletes_it_and_enabled_follows(self):
        # Mutation: enable writes the command without --quiet. Red: the value is not command().
        registry = MemoryRegistry()
        subject = self.subject(registry)
        self.assertTrue(subject.available)
        self.assertFalse(subject.enabled())
        self.assertTrue(subject.enable())
        self.assertEqual(registry.value, subject.command())
        self.assertEqual(registry.value, f'"{self.python}" -m pcnotify --quiet')
        self.assertTrue(subject.enabled())
        self.assertTrue(subject.disable())
        self.assertIsNone(registry.value)
        self.assertFalse(subject.enabled())
        self.assertTrue(subject.disable())  # a missing value is no error
        self.assertEqual(registry.calls, ["read", "write", "read", "delete", "read", "delete"])

    def test_a_failing_registry_changes_nothing_raises_nothing_and_says_one_line_each_time(self):
        # Mutation: the failure left to the caller. Red: PermissionError leaves enable.
        registry = MemoryRegistry(value="kept", failing=True)
        subject = self.subject(registry)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            results = (subject.enable(), subject.disable(), subject.enabled())
        self.assertEqual(results, (False, False, False))
        self.assertEqual(registry.value, "kept")
        self.assertEqual(out.getvalue(), "")
        self.assertEqual(err.getvalue().splitlines(),
                         [autostart.FAILED_LINE.format(what="written", kind="PermissionError"),
                          autostart.FAILED_LINE.format(what="removed", kind="PermissionError"),
                          autostart.FAILED_LINE.format(what="read", kind="PermissionError")])
        self.assertNotIn(str(self.python), err.getvalue())
        self.assertNotIn("—", autostart.FAILED_LINE)

    def test_the_command_prefers_pythonw_beside_the_interpreter_and_quotes_a_path_with_spaces(self):
        # Mutation: the interpreter always used. Red: python.exe where pythonw.exe exists.
        self.assertEqual(self.subject().command(), f'"{self.python}" -m pcnotify --quiet')
        windowless = self.folder / "pythonw.exe"
        windowless.write_bytes(b"")
        self.assertEqual(self.subject().command(), f'"{windowless}" -m pcnotify --quiet')
        plain = self.base / "plain"
        plain.mkdir()
        (plain / "python.exe").write_bytes(b"")
        self.assertIsNone(re.search(r"\s", str(plain)), "the precondition: a folder with no space")
        # As install.ps1's Format-Arg: a path with no space is written bare.
        self.assertEqual(self.subject(executable=plain / "python.exe").command(),
                         f"{plain / 'python.exe'} -m pcnotify --quiet")

    def test_where_winreg_cannot_be_imported_it_is_not_available_and_touches_nothing(self):
        # Mutation: the import failure left to the caller. Red: ImportError leaves Autostart().
        saved = sys.modules.get("winreg")
        sys.modules["winreg"] = None  # an import of it now raises ImportError, as on a system with none

        def restore():
            if saved is None:
                sys.modules.pop("winreg", None)
            else:
                sys.modules["winreg"] = saved

        self.addCleanup(restore)
        subject = autostart.Autostart(executable=str(self.python))
        self.assertFalse(subject.available)
        self.assertEqual((subject.enabled(), subject.enable(), subject.disable()), (False, False, False))

    @NEEDS_WINREG
    def test_the_real_registry_writes_reads_and_deletes_one_value_under_a_scratch_key(self):
        # Mutation: delete raising on a missing value. Red: the second disable says a failure.
        key_path = scratch_key(self)
        self.assertNotIn("CurrentVersion", key_path)
        subject = self.subject(autostart.WindowsRegistry(key_path))
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertFalse(subject.enabled())
            self.assertTrue(subject.enable())
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
                self.assertEqual(winreg.QueryValueEx(key, "pcnotify"), (subject.command(), winreg.REG_SZ))
                self.assertEqual(winreg.QueryInfoKey(key)[1], 1)  # one value under the key, no other
            self.assertTrue(subject.enabled())
            self.assertTrue(subject.disable())
            self.assertTrue(subject.disable())
            self.assertFalse(subject.enabled())
        self.assertEqual(err.getvalue(), "")
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            self.assertEqual(winreg.QueryInfoKey(key)[1], 0)


if __name__ == "__main__":
    unittest.main()
