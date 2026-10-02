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

The record's `started` is the holder's creation time: a newer copy whose post gets no 202 ends the older one by
its pid only once terminate proves the pid is still that holder, and evicts the stale file of a holder whose
pid another process has now.
"""
import errno
import json
import ntpath
import os
import secrets
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
# terminate's three answers: the holder ended; the pid names another process, so the run file is stale; the
# system refused to open or end it.
ENDED, NOT_OURS, REFUSED = "ended", "not-ours", "refused"
# The images an older record's holder runs, the python of its install, compared lower-cased.
_PYTHON_IMAGES = ("python.exe", "pythonw.exe")
# An older record's file proves its holder started after the boot only when written this long after it.
BOOT_MARGIN_SECONDS = 2.0


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


class Answer(str):
    """One of terminate's three answers, ENDED, NOT_OURS or REFUSED, compared as its word; `error` is the OSError
    behind REFUSED, which the caller prints, else None."""

    def __new__(cls, word, error=None):
        answer = super().__new__(cls, word)
        answer.error = error
        return answer


def _kernel32():
    """kernel32 through ctypes, the calls terminate and process_started make typed."""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.GetCurrentProcess.argtypes = ()
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    kernel32.GetProcessTimes.argtypes = (wintypes.HANDLE,) + (ctypes.POINTER(wintypes.FILETIME),) * 4
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.QueryFullProcessImageNameW.argtypes = (wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                    ctypes.POINTER(wintypes.DWORD))
    kernel32.TerminateProcess.restype = wintypes.BOOL
    kernel32.TerminateProcess.argtypes = (wintypes.HANDLE, wintypes.UINT)
    kernel32.GetTickCount64.restype = ctypes.c_ulonglong
    kernel32.GetTickCount64.argtypes = ()
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    return kernel32


def _creation_time(kernel32, handle):
    """The creation time of the process `handle` opens, the 100 ns FILETIME integer GetProcessTimes gives."""
    import ctypes
    from ctypes import wintypes

    created, ended, kernel, user = (wintypes.FILETIME() for _ in range(4))
    if not kernel32.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(ended), ctypes.byref(kernel),
                                    ctypes.byref(user)):
        raise ctypes.WinError(ctypes.get_last_error())
    return created.dwHighDateTime << 32 | created.dwLowDateTime


def _image_name(kernel32, handle):
    """The file name of the image the process `handle` opens runs: QueryFullProcessImageNameW's path, no folder."""
    import ctypes
    from ctypes import wintypes

    size = wintypes.DWORD(32768)
    path = ctypes.create_unicode_buffer(size.value)
    if not kernel32.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(size)):
        raise ctypes.WinError(ctypes.get_last_error())
    return ntpath.basename(path.value)


def _open_to_end(kernel32, pid):
    """The process `pid` opened to read its times and image and to end it; OSError when the system refuses."""
    import ctypes

    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION | _PROCESS_TERMINATE, False, pid)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    return handle


def _off_windows(pid):
    return OSError(errno.ENOSYS, "a process is told apart from another one only on Windows", str(pid))


def process_started():
    """This process's creation time, the 100 ns FILETIME integer GetProcessTimes gives for GetCurrentProcess: the
    holder's identity in its record. None on a platform with no kernel32."""
    if os.name != "nt":
        return None
    kernel32 = _kernel32()
    return _creation_time(kernel32, kernel32.GetCurrentProcess())


def _probe(pid):
    """(creation time, image file name) of the process `pid`, opened as terminate opens it; OSError when the system
    refuses to open or read it."""
    if os.name != "nt":
        raise _off_windows(pid)
    kernel32 = _kernel32()
    handle = _open_to_end(kernel32, pid)
    try:
        return _creation_time(kernel32, handle), _image_name(kernel32, handle)
    finally:
        kernel32.CloseHandle(handle)


def _end(pid, created):
    """Ends the process `pid` while it is still the one created at `created`: opened again, its creation time read on
    that same handle before TerminateProcess. True when ended; False when the pid names another process by now;
    OSError when the system refuses to open or end it."""
    if os.name != "nt":
        raise _off_windows(pid)
    import ctypes

    kernel32 = _kernel32()
    handle = _open_to_end(kernel32, pid)
    try:
        if _creation_time(kernel32, handle) != created:
            return False
        if not kernel32.TerminateProcess(handle, 1):
            raise ctypes.WinError(ctypes.get_last_error())
        return True
    finally:
        kernel32.CloseHandle(handle)


def _boot_instant():
    """The instant this system booted, in seconds since the epoch: now less GetTickCount64's milliseconds."""
    if os.name != "nt":
        raise _off_windows(0)
    return time.time() - _kernel32().GetTickCount64() / 1000


def _written_after(path, instant):
    """True when the run file at `path` was last written after `instant`, in seconds since the epoch; False when it
    is gone."""
    try:
        return os.stat(path).st_mtime > instant
    except FileNotFoundError:
        return False


def _started_before_written(path, created):
    """True when the process created at `created`, a FILETIME (100 ns ticks since 1601), started before the run file
    at `path` was last written, as its true holder did: it wrote the file after it started. False when it is gone."""
    try:
        return created / 10**7 - 11644473600 < os.stat(path).st_mtime
    except FileNotFoundError:
        return False


def terminate(pid, started, path, probe=_probe, boot_instant=_boot_instant, end=_end):
    """Ends the process `pid` the run file at `path` names only once that process is proven its holder, and answers
    ENDED, NOT_OURS or REFUSED. The pid is opened with PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_TERMINATE and its
    creation time read: with `started`, the record's creation time, it is ended only when the two are equal; with
    None, as an older record (0.1.5 and before) has it, only when its image is python.exe or pythonw.exe, the run
    file was written more than BOOT_MARGIN_SECONDS after the boot, and the process started before that write, so the
    pid of a holder that died with the system, another process's now, is never ended, also after a Fast Startup
    shutdown, which keeps GetTickCount64 counting from the last cold boot. NOT_OURS: the pid is not the holder's and
    its run file is stale. REFUSED: an OSError opening or ending it, kept on the answer. The pid the record names is
    the one process opened, never a process searched by its name or its command line, and this process is never
    ended. The seams a test fakes, so no test opens a process to end it: `probe(pid)` gives (creation time, image
    file name), `boot_instant()` the boot in seconds since the epoch, and `end(pid, created)` ends the pid only while
    it still names the process created then (False otherwise)."""
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0 or pid == os.getpid():
        return Answer(NOT_OURS)
    try:
        if started is None and not _written_after(path, boot_instant() + BOOT_MARGIN_SECONDS):
            return Answer(NOT_OURS)  # whatever the pid shows: the holder died with the boot
        created, image = probe(pid)
        if started is not None:
            ours = created == started
        else:
            ours = str(image).lower() in _PYTHON_IMAGES and _started_before_written(path, created)
        if not ours or not end(pid, created):  # the end reads the creation time again on the handle it ends
            return Answer(NOT_OURS)
    except OSError as error:
        return Answer(REFUSED, error)
    return Answer(ENDED)


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
        # This process's creation time, written at the publish: a newer copy ends this one by its pid only when
        # the pid's own creation time equals it.
        self.started = None

    def claim(self):
        """None when this process now holds the file; the live holder's record, {pid, port, closing, version,
        secret, started}, when another does. A refused remove or create (PermissionError on Windows: the file held
        open, or pending its removal, by another start) is that other start only when its live record shows within
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
                record = self._read()
                if self._live(record) is not None:
                    return record
                try:  # a holder that is gone, or a file that names none: taken over while it holds the record read
                    self._take_aside(record)
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
        """This process's pid, its page's port, its version, the replace secret and its creation time, written over
        the claim in one move; PermissionError when the move is still refused after its retries. The secret is never
        logged or shown."""
        if self.secret is None:
            self.secret = secrets.token_hex(16)
        if self.started is None:
            self.started = process_started()
        self._write(self._record(port))
        self._port = port

    def mark_closing(self):
        """This process's record marked closing, in the same one move: a start that meets it waits for the
        file to go. Nothing when this process does not hold the file."""
        if self._held:
            self._write({**self._record(self._port), "closing": True})

    def _record(self, port):
        return {"pid": self._pid, "port": port, "version": update.RUNNING_VERSION, "secret": self.secret,
                "started": self.started}

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

    def evict(self, holder):
        """Removes the run file `holder` was read from, a record terminate answered NOT_OURS for, so stale: only while
        the file still holds that record, compared and removed in one step (_take_aside). True when it removed it;
        False when the file holds another record by then, put back under its name, or when another start moved it
        first (FileNotFoundError) or holds it at the move aside (PermissionError). A remove still refused after its
        retries (a read-only file) raises its PermissionError with the record back under its name, so the start ends
        on the line that names the file, never on that stale record read as a live holder."""
        try:
            aside = self._moved_aside()
        except (FileNotFoundError, PermissionError):  # another newcomer won: the claim that follows reads its record
            return False
        try:
            return self._settled(aside, holder)  # a refused remove or put-back raises: the start cannot go on
        except FileNotFoundError:
            return False

    def _take_aside(self, record):
        """The run file removed only while it holds `record`: moved aside first, to <path>.evict-<this pid>, so no
        other start's file can take its place between the compare and the remove, then read there. `record`: the
        moved file removed, True. Another record, a claim written since `record` was read: moved back under its
        name, False. Each move and the remove under the retries of release; FileNotFoundError when the file is gone
        at the move aside, PermissionError when a step is still refused after its retries, the file moved back under
        its name first when the remove is the step refused (a read-only file), so it stays as it was found."""
        return self._settled(self._moved_aside(), record)

    def _moved_aside(self):
        """The run file moved to <path>.evict-<this pid> under the retries of release; that name answered."""
        aside = f"{self.path}.evict-{self._pid}"
        self._retried(os.replace, self.path, aside)
        return aside

    def _settled(self, aside, record):
        """The file moved to `aside` removed when it holds `record`, True; moved back under its name when it holds
        another, False, or when its remove stays refused, the refusal raised."""
        if self._read(aside) != record:
            self._retried(os.replace, aside, self.path)
            return False
        try:
            self._retried(os.remove, aside)
        except PermissionError:  # a file this user cannot remove (read-only): put back as it was, the refusal raised
            self._retried(os.replace, aside, self.path)
            raise
        return True

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
        return self._live(self._read())

    def _live(self, holder):
        """`holder`, a record read, when it names a live process other than this one; else None."""
        if holder is not None and holder["pid"] != self._pid and self._alive(holder["pid"]):
            return holder
        return None

    def _read(self, path=None):
        """The record in the run file, or in `path`; None when it is gone, unreadable or names no pid."""
        try:
            data = json.loads(Path(self.path if path is None else path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        pid = data.get("pid") if isinstance(data, dict) else None
        if isinstance(pid, bool) or not isinstance(pid, int):
            return None
        port = data.get("port")
        valid = isinstance(port, int) and not isinstance(port, bool) and 0 < port < 65536
        version, secret, started = data.get("version"), data.get("secret"), data.get("started")
        return {"pid": pid, "port": port if valid else None, "closing": data.get("closing") is True,
                "version": version if isinstance(version, str) and version else None,
                "secret": secret if isinstance(secret, str) and secret else None,
                "started": started if isinstance(started, int) and not isinstance(started, bool) else None}
