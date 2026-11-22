"""The Accessibility permission awaited while this copy runs, macOS only, for the page's switch (autoclick.Switch).

A process keeps the answer macOS gave it at its start: AXIsProcessTrusted() never asks again in a process with no run
loop to hear the change, so a grant made while this copy runs is read by a child (trusted_now), which macOS judges as
PendiFy.app too, and the copy then asks for its own restart, whose new copy reads the permission granted. The Poller
does that on its own daemon thread, every few seconds for the first minutes after an ask, then seldom.

Standard library only: the child is this interpreter, isolated, calling the public AXIsProcessTrusted() through
ctypes, with no prompt.
"""
import subprocess
import sys
import threading
import time

IS_MAC = sys.platform == "darwin"
TIMEOUT = 5  # a child that does not answer in it is a no
# While the permission is awaited, how often a child asks macOS whether it was granted: every few seconds for the
# first minutes after an ask, while its person is likely in System Settings, then seldom.
PERMISSION_POLL_SECONDS = 3
PERMISSION_FAST_SECONDS = 10 * 60
PERMISSION_SLOW_POLL_SECONDS = 30
GRANTED_LINE = "autoaccept: the Accessibility permission was granted; restarting so the click can use it"

# The fresh check's program, run by a child of this interpreter: the public AXIsProcessTrusted(), no prompt.
_TRUSTED_NOW = ("import ctypes; ax = ctypes.CDLL(%r); ax.AXIsProcessTrusted.restype = ctypes.c_bool; "
                "print('trusted' if ax.AXIsProcessTrusted() else 'untrusted')"
                % "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")


def trusted_now(run=subprocess.run):
    """True when macOS says now that this app may post clicks, asked by a new process, since this one keeps the
    answer of its start; False for a no, for a child that fails or does not answer in TIMEOUT, and off macOS."""
    if not IS_MAC:
        return False
    try:
        done = run([sys.executable, "-I", "-c", _TRUSTED_NOW], capture_output=True, text=True, timeout=TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0 and done.stdout.strip() == "trusted"


class Poller:
    """The grant awaited on its own daemon thread, one at most. `switched_on()` reads the page's switch; `restart()`
    asks for this copy's restart and answers True once asked, and with none no poller starts; `run` starts the child;
    `wait(seconds)` waits before each round and answers True once closed (the close's own wait when None); `clock`
    times the rounds from the last start."""

    def __init__(self, switched_on, restart, run=subprocess.run, wait=None, clock=time.monotonic):
        self._switched_on, self._restart, self._run = switched_on, restart, run
        self._closed = threading.Event()
        self._wait = self._closed.wait if wait is None else wait
        self._clock, self._asked_at = clock, None
        # Held while the poller starts or ends, so a switch turned on as it ends is never left with none.
        self._lock = threading.Lock()
        self._polling = False
        self.thread = None

    def start(self):
        """After an ask that found the permission not granted: the fast rounds start again, and the poller with them
        unless one runs, the switch is off, no restart can be asked for or the poller is closed."""
        self._asked_at = self._clock()
        with self._lock:
            if self._polling or self._restart is None or self._closed.is_set() or not self._switched_on():
                return
            self._polling = True
            self.thread = threading.Thread(target=self._rounds, name="autoaccept-permission", daemon=True)
            self.thread.start()

    def close(self):
        """This copy stops: the wait ends, and no restart is asked any more."""
        self._closed.set()

    def _interval(self):
        fast = self._clock() - self._asked_at < PERMISSION_FAST_SECONDS
        return PERMISSION_POLL_SECONDS if fast else PERMISSION_SLOW_POLL_SECONDS

    def _turned_off(self):
        """True, and the poller counted ended, once the switch reads off; read under the lock, so an ask that turns it
        on again starts a new one."""
        with self._lock:
            if not self._switched_on():
                self._polling = False
                return True
        return False

    def _rounds(self):
        """While the switch is on, a child asks macOS every PERMISSION_POLL_SECONDS for PERMISSION_FAST_SECONDS after
        the last start, then every PERMISSION_SLOW_POLL_SECONDS; once it says granted and the restart is asked, said
        on one line, the poller ends and none starts again. Ends as well at the close."""
        while not self._wait(self._interval()) and not self._closed.is_set():
            if self._turned_off():
                return
            if trusted_now(self._run) and not self._closed.is_set() and self._restart():
                print(GRANTED_LINE, flush=True)
                return  # still polling: none starts again in a copy that stops for its restart
