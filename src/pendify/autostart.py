"""The start with Windows: the per-user Run value, no administrator.

The value `PendiFy` under HKEY_CURRENT_USER's Run key holds the start line of install.ps1 plus --quiet, so the
program starts at logon with no browser. Only the page writes it, on its person's ask; a start never writes it,
and install.ps1 only rewrites one that exists. The registry is a seam of three calls on one value under one key
path; the real one imports winreg inside itself and takes the key path as an argument, so a test points it at a
scratch key. Where winreg cannot be imported the start with Windows is not available, and the page shows and
routes nothing of it. A registry failure is one sentence on stderr, by its type only, and changes nothing.
"""
import sys
from pathlib import Path

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "PendiFy"
START_ARGS = "-m pendify --quiet"
FAILED_LINE = "autostart: the start with Windows could not be {what} ({kind})"


class WindowsRegistry:
    """One value under one key path of HKEY_CURRENT_USER: read, write and delete, nothing else. Built, it reads
    nothing; an import of winreg that fails is the caller's ImportError."""

    def __init__(self, key_path=RUN_KEY, name=VALUE_NAME):
        import winreg

        self._winreg, self.key_path, self.name = winreg, key_path, name

    def read(self):
        """The value's text, or None when the key or the value is missing."""
        winreg = self._winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.key_path) as key:
                return winreg.QueryValueEx(key, self.name)[0]
        except FileNotFoundError:
            return None

    def write(self, text):
        winreg = self._winreg
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, self.key_path, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, self.name, 0, winreg.REG_SZ, text)

    def delete(self):
        """The value removed; a missing key or value is nothing to remove, no error."""
        winreg = self._winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.key_path, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, self.name)
        except FileNotFoundError:
            pass


def _say_failure(what, failure):
    print(FAILED_LINE.format(what=what, kind=type(failure).__name__), file=sys.stderr, flush=True)


class Autostart:
    def __init__(self, registry=None, executable=None):
        """`registry` is the seam of the three calls, the real Run value when None; `executable` is the
        interpreter whose start line is written, this one when None."""
        if registry is None:
            try:
                registry = WindowsRegistry()
            except ImportError:  # no winreg on this system: no Run value to keep
                registry = None
        self.registry = registry
        self.available = registry is not None
        self._executable = executable or sys.executable

    def enabled(self):
        """True when the value exists; False when it does not, when not available, or when it cannot be read."""
        if not self.available:
            return False
        try:
            return self.registry.read() is not None
        except OSError as failure:
            _say_failure("read", failure)
            return False

    def enable(self):
        """The value written with command(); True when written."""
        return self._change("written", lambda: self.registry.write(self.command()))

    def disable(self):
        """The value deleted, a missing one being no error; True when it is gone."""
        return self._change("removed", lambda: self.registry.delete())

    def command(self):
        """install.ps1's start line plus --quiet: pythonw.exe beside the interpreter when that file exists, else
        the interpreter, quoted when its path holds a space (install.ps1's Format-Arg)."""
        interpreter = Path(self._executable)
        windowless = interpreter.with_name("pythonw.exe")
        chosen = str(windowless if windowless.is_file() else interpreter)
        if any(character.isspace() for character in chosen):
            chosen = f'"{chosen}"'
        return f"{chosen} {START_ARGS}"

    def _change(self, what, change):
        if not self.available:
            return False
        try:
            change()
        except OSError as failure:
            _say_failure(what, failure)
            return False
        return True
