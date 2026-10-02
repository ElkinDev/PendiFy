"""One instance per config folder: the running process holds a run file with its pid and its page's port.

A start creates the file exclusively. When the file is already there and names a live process other than
this one, the start stops; a file whose pid is gone, or that names no pid, is taken over. A live pid is
asked of the system through ctypes on Windows (os.kill there would end the process) and os.kill elsewhere.
A refused remove or create is told apart before it is read as another start that won the race: a config
folder that takes no new file, or a run file still there with no live holder when the re-read window ends,
stops this start; only a live holder's record read within the window is that other start.

At its stop the holder marks its record closing, beside its pid and port, until it removes the file: a start that
meets a closing holder waits for the file to go instead of opening a page that is closing. A record with no mark,
as an older copy writes it, reads as not closing.

The record also carries the holder's version and a replace secret, written at the publish and kept by the mark: a
newer copy started over it posts the secret to the holder's page, POST /replace, and the holder stops as its quit
does (__main__.py). A record with neither, as an older copy writes it, reads version None and secret None.
"""
import errno
import json
import os
import secrets
import signal
import tempfile
import time
from pathlib import Path

from . import update

FILE_NAME = "run.json"
# How long a start re-reads the other start's record: the winner of a race writes its pid right after its
# create and its port once its page listens.
REREAD_SECONDS = 1.0
REREAD_STEP = 0.05
# A start re-reading the file holds it for about a millisecond, and Windows refuses this holder's remove or replace
# meanwhile: each is tried REFUSED_TRIES times, REFUSED_STEP apart, 40 ms of waits in all.
REFUSED_TRIES = 5
REFUSED_STEP = 0.01
_STILL_ACTIVE = 259
_ERROR_ACCESS_DENIED = 5
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_PROCESS_TERMINATE = 0x0001


class FolderNotWritable(PermissionError):
    """The config folder takes no new file, so no start can hold its run file there; `filename` names it."""


def pid_alive(pid):
    """True when `pid` names a running process."""
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == "nt":
        return _windows_alive(pid)
    try:
        os.kill(pid, 0)  # signal 0 only asks
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _windows_alive(pid):
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ctypes.get_last_error() == _ERROR_ACCESS_DENIED  # a process this user may not open still runs
    try:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return True
        return code.value == _STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def terminate(pid):
    """Ends the process `pid`, named by its pid alone, never by its name or its command line: TerminateProcess
    through ctypes on Windows, opened as pid_alive opens it, and SIGTERM elsewhere. OSError when it cannot be ended;
    this process is never ended."""
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0 or pid == os.getpid():
        raise ProcessLookupError(errno.ESRCH, "no other process has this pid", str(pid))
    if os.name != "nt":
        os.kill(pid, signal.SIGTERM)
        return
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.TerminateProcess.restype = wintypes.BOOL
    kernel32.TerminateProcess.argtypes = (wintypes.HANDLE, wintypes.UINT)
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    handle = kernel32.OpenProcess(_PROCESS_TERMINATE, False, pid)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if not kernel32.TerminateProcess(handle, 1):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        kernel32.CloseHandle(handle)


class RunFile:
    def __init__(self, folder, pid=None, alive=pid_alive, clock=time.monotonic, sleep=time.sleep):
        self.path = Path(folder) / FILE_NAME
        self._pid = os.getpid() if pid is None else pid
        self._alive = alive
        self._clock = clock
        self._sleep = sleep
        self._held = False
        self._port = None
        # The replace secret, made at the publish: the page's /replace answers only a post that carries it.
        self.secret = None

    def claim(self):
        """None when this process now holds the file; the live holder's record, {pid, port, closing, version,
        secret}, when another does. A refused remove or create (PermissionError on Windows: the file held open, or
        pending its removal, by another start) is that other start only when its live record shows within
        REREAD_SECONDS. FolderNotWritable when the config folder takes no new file; OSError when the file
        can neither be created nor taken over."""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except PermissionError:
            raise FolderNotWritable(errno.EACCES, "the config folder cannot be made", str(self.path.parent)) from None
        except FileExistsError:  # what Windows raises when a regular file stands where the folder or a parent goes
            raise FolderNotWritable(errno.EACCES, "the config folder cannot be made", str(self.path.parent)) from None
        for _ in range(3):
            try:
                handle = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                holder = self._live_holder()
                if holder is not None:
                    return holder
                try:
                    os.remove(self.path)  # a holder that is gone, or a file that names none: taken over
                except FileNotFoundError:
                    pass
                except PermissionError:  # held open by another start, or a file this user cannot remove
                    holder = self._refused()
                    if holder is not None:
                        return holder
                continue
            except PermissionError:  # pending its removal under another handle, or a folder that takes no file
                holder = self._refused()
                if holder is not None:
                    return holder
                continue
            with os.fdopen(handle, "w", encoding="utf-8") as out:
                # The version from the claim on: a start of the same version that meets this one before its publish
                # reads it as the same version, never as an older copy to replace.
                json.dump({"pid": self._pid, "port": None, "version": update.RUNNING_VERSION}, out)
            self._held = True
            return None
        raise FileExistsError("the run file came back after every take-over")

    def _refused(self):
        """A refused remove or create, told apart: the live holder's record when it shows within the window;
        None when the file is gone by then, so the claim tries again. A folder in the file's place, a config
        folder that takes no new file, or a file still there naming no live holder: this start cannot go on."""
        if self.path.is_dir():
            raise IsADirectoryError(errno.EISDIR, "the run file is a folder", str(self.path))
        self._probe_folder()
        deadline = self._clock() + REREAD_SECONDS
        while True:
            holder = self._live_holder()
            if holder is not None:
                return holder
            if not os.path.lexists(self.path):
                return None
            if self._clock() >= deadline:
                raise PermissionError(errno.EACCES, "the run file names no live holder and cannot be replaced",
                                      str(self.path))
            self._sleep(REREAD_STEP)

    def _probe_folder(self):
        """FolderNotWritable when the config folder takes no new file: a probe name, made and removed at once."""
        probe = self.path.parent / f".probe-{self._pid}-{secrets.token_hex(4)}.tmp"
        try:
            handle = os.open(probe, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except PermissionError:
            raise FolderNotWritable(errno.EACCES, "the config folder takes no new file",
                                    str(self.path.parent)) from None
        os.close(handle)
        os.remove(probe)

    def holder_port(self, holder):
        """The port of `holder`, a live holder claim() returned, re-read from its record for up to
        REREAD_SECONDS; None when it has published none by then."""
        deadline = self._clock() + REREAD_SECONDS
        while True:
            record = self._read()
            if record is not None and record["pid"] == holder["pid"] and record["port"] is not None:
                return record["port"]
            if self._clock() >= deadline:
                return None
            self._sleep(REREAD_STEP)

    def wait_released(self, holder, seconds):
        """True once the run file no longer names `holder`, a live holder claim() returned closing, as a live
        process: the file gone, its process gone, or another start's record in its place; re-read every
        REREAD_STEP. False when it still does after `seconds`."""
        deadline = self._clock() + seconds
        while True:
            record = self._live_holder()
            if record is None or record["pid"] != holder["pid"]:
                return True
            if self._clock() >= deadline:
                return False
            self._sleep(REREAD_STEP)

    def publish(self, port):
        """This process's pid, its page's port, its version and the replace secret, written over the claim in one
        move; PermissionError when the move is still refused after its retries. The secret is never logged or
        shown."""
        if self.secret is None:
            self.secret = secrets.token_hex(16)
        self._write(self._record(port))
        self._port = port

    def mark_closing(self):
        """This process's record marked closing, in the same one move: a start that meets it waits for the
        file to go. Nothing when this process does not hold the file."""
        if self._held:
            self._write({**self._record(self._port), "closing": True})

    def _record(self, port):
        return {"pid": self._pid, "port": port, "version": update.RUNNING_VERSION, "secret": self.secret}

    def _write(self, record):
        handle, temp = tempfile.mkstemp(dir=self.path.parent, prefix=".run-", suffix=".tmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as out:
                json.dump(record, out)
            self._retried(os.replace, temp, self.path)
        except BaseException:
            if os.path.exists(temp):
                os.remove(temp)
            raise

    def release(self):
        """Removes the file when this process holds it and it still names this process; a remove still refused
        after its retries leaves the file and raises nothing."""
        if not self._held:
            return
        self._held = False
        holder = self._read()
        if holder is not None and holder["pid"] == self._pid:
            try:
                self._retried(os.remove, self.path)
            except FileNotFoundError:
                pass
            except PermissionError:
                pass  # refused to the end: the file stays, and a start waiting on it takes it over once this pid dies

    def _retried(self, move, *args):
        """`move(*args)`, tried again REFUSED_STEP later while a reader makes the system refuse it, REFUSED_TRIES
        times in all; the last refusal raises."""
        for attempt in range(1, REFUSED_TRIES + 1):
            try:
                return move(*args)
            except PermissionError:
                if attempt == REFUSED_TRIES:
                    raise
                self._sleep(REFUSED_STEP)

    def _live_holder(self):
        """The record of a live process other than this one, or None."""
        holder = self._read()
        if holder is not None and holder["pid"] != self._pid and self._alive(holder["pid"]):
            return holder
        return None

    def _read(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        pid = data.get("pid") if isinstance(data, dict) else None
        if isinstance(pid, bool) or not isinstance(pid, int):
            return None
        port = data.get("port")
        valid = isinstance(port, int) and not isinstance(port, bool) and 0 < port < 65536
        version, secret = data.get("version"), data.get("secret")
        return {"pid": pid, "port": port if valid else None, "closing": data.get("closing") is True,
                "version": version if isinstance(version, str) and version else None,
                "secret": secret if isinstance(secret, str) and secret else None}
