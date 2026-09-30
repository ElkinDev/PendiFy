"""One instance per config folder: the running process holds a run file with its pid and its page's port.

A start creates the file exclusively. When the file is already there and names a live process other than
this one, the start stops; a file whose pid is gone, or that names no pid, is taken over. A live pid is
asked of the system through ctypes on Windows (os.kill there would end the process) and os.kill elsewhere.
"""
import json
import os
import tempfile
from pathlib import Path

FILE_NAME = "run.json"
_STILL_ACTIVE = 259
_ERROR_ACCESS_DENIED = 5
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


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


class RunFile:
    def __init__(self, folder, pid=None, alive=pid_alive):
        self.path = Path(folder) / FILE_NAME
        self._pid = os.getpid() if pid is None else pid
        self._alive = alive
        self._held = False

    def claim(self):
        """None when this process now holds the file; the live holder's record, {pid, port}, when another
        does. OSError when the file can neither be created nor taken over."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(3):
            try:
                handle = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                holder = self._read()
                if holder is not None and holder["pid"] != self._pid and self._alive(holder["pid"]):
                    return holder
                try:
                    os.remove(self.path)  # a holder that is gone, or a file that names none: taken over
                except FileNotFoundError:
                    pass
                continue
            with os.fdopen(handle, "w", encoding="utf-8") as out:
                json.dump({"pid": self._pid, "port": None}, out)
            self._held = True
            return None
        raise FileExistsError("the run file came back after every take-over")

    def publish(self, port):
        """This process's pid and its page's port, written over the claim in one move."""
        handle, temp = tempfile.mkstemp(dir=self.path.parent, prefix=".run-", suffix=".tmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as out:
                json.dump({"pid": self._pid, "port": port}, out)
            os.replace(temp, self.path)
        except BaseException:
            if os.path.exists(temp):
                os.remove(temp)
            raise

    def release(self):
        """Removes the file when this process holds it and it still names this process."""
        if not self._held:
            return
        self._held = False
        holder = self._read()
        if holder is not None and holder["pid"] == self._pid:
            try:
                os.remove(self.path)
            except FileNotFoundError:
                pass

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
        return {"pid": pid, "port": port if valid else None}
