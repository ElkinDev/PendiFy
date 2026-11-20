"""The click on the TFT app's accept button, macOS only, turned on from the page.

The app serves no API to accept a match and takes no key for it, so its player's own click is made: the app is
brought to the front, its window read, the mouse moved onto the button, clicked, and put back where it was. The
button's place is a share of the window, the window's centre across and ACCEPT_DOWN of its height down, measured on
a MacBook Pro's full screen; the game scales its interface with the window's height, so the share holds at any size.
Nothing is clicked unless the app is the frontmost one and its window is found. Posting a click needs the
Accessibility permission (System Settings, Privacy & Security, Accessibility); without it nothing is clicked.

Standard library only: CoreGraphics, CoreFoundation and ApplicationServices through ctypes, and osascript (JXA) for
the window's bounds and lsappinfo for the frontmost app.
"""
import ctypes
import json
import subprocess
import sys
import time

APP_PATH = "/Applications/Teamfight Tactics.app"
OWNER_NAME = "TFTClient-Mac-Shipping"
ACCEPT_ACROSS = 0.5
ACCEPT_DOWN = 0.6085
FRONT_WAIT_SECONDS = 2.5  # the switch to the app's full-screen space takes about a second
SETTLE_SECONDS = 0.4
TIMEOUT = 5

PERMISSION_LINES = {True: "autoaccept: on, the Accessibility permission is granted",
                    False: "autoaccept: on, waiting for the Accessibility permission (System Settings, Privacy & "
                           "Security, Accessibility)"}
# Why a click was not made, said on one line.
NO_PERMISSION, NO_WINDOW, NOT_FRONT, BROKE = "no_permission", "no_window", "not_front", "broke"

_FRAMEWORKS = "/System/Library/Frameworks/{0}.framework/{0}"
_WINDOWS_JXA = """
ObjC.import("CoreGraphics"); ObjC.import("Foundation");
var list = ObjC.castRefToObject($.CGWindowListCopyWindowInfo(0, 0)), out = [];
for (var i = 0; i < list.count; i++) {
  var w = list.objectAtIndex(i);
  if (ObjC.unwrap(w.objectForKey("kCGWindowOwnerName")) !== "%s") continue;
  if (ObjC.unwrap(w.objectForKey("kCGWindowLayer")) !== 0) continue;
  var b = w.objectForKey("kCGWindowBounds");
  out.push([ObjC.unwrap(b.objectForKey("X")), ObjC.unwrap(b.objectForKey("Y")),
            ObjC.unwrap(b.objectForKey("Width")), ObjC.unwrap(b.objectForKey("Height"))]);
}
JSON.stringify(out)
""" % OWNER_NAME


class CGPoint(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]


def available():
    return sys.platform == "darwin"


def _frameworks():
    """(CoreGraphics, CoreFoundation, ApplicationServices), typed."""
    cg = ctypes.CDLL(_FRAMEWORKS.format("CoreGraphics"))
    cf = ctypes.CDLL(_FRAMEWORKS.format("CoreFoundation"))
    ax = ctypes.CDLL(_FRAMEWORKS.format("ApplicationServices"))
    cg.CGEventCreateMouseEvent.argtypes = (ctypes.c_void_p, ctypes.c_uint32, CGPoint, ctypes.c_uint32)
    cg.CGEventCreateMouseEvent.restype = ctypes.c_void_p
    cg.CGEventPost.argtypes = (ctypes.c_uint32, ctypes.c_void_p)
    cg.CGEventCreate.argtypes = (ctypes.c_void_p,)
    cg.CGEventCreate.restype = ctypes.c_void_p
    cg.CGEventGetLocation.argtypes = (ctypes.c_void_p,)
    cg.CGEventGetLocation.restype = CGPoint
    cf.CFRelease.argtypes = (ctypes.c_void_p,)
    cf.CFDictionaryCreate.argtypes = (ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p),
                                      ctypes.POINTER(ctypes.c_void_p), ctypes.c_long, ctypes.c_void_p,
                                      ctypes.c_void_p)
    cf.CFDictionaryCreate.restype = ctypes.c_void_p
    ax.AXIsProcessTrusted.restype = ctypes.c_bool
    ax.AXIsProcessTrustedWithOptions.argtypes = (ctypes.c_void_p,)
    ax.AXIsProcessTrustedWithOptions.restype = ctypes.c_bool
    return cg, cf, ax


def trusted(prompt=False):
    """True when this process may post clicks; with `prompt`, macOS asks its person for the permission once."""
    if not available():
        return False
    try:
        _, cf, ax = _frameworks()
        if not prompt:
            return bool(ax.AXIsProcessTrusted())
        key = ctypes.c_void_p.in_dll(ax, "kAXTrustedCheckOptionPrompt")
        true = ctypes.c_void_p.in_dll(cf, "kCFBooleanTrue")
        keys, values = (ctypes.c_void_p * 1)(key.value), (ctypes.c_void_p * 1)(true.value)
        options = cf.CFDictionaryCreate(None, keys, values, 1,
                                        ctypes.addressof(ctypes.c_char.in_dll(cf, "kCFTypeDictionaryKeyCallBacks")),
                                        ctypes.addressof(ctypes.c_char.in_dll(cf, "kCFTypeDictionaryValueCallBacks")))
        try:
            return bool(ax.AXIsProcessTrustedWithOptions(options))
        finally:
            cf.CFRelease(options)
    except (OSError, ValueError, AttributeError):
        return False


def accept_point(bounds):
    """The button's point on the screen, in points, for the window's (x, y, width, height)."""
    x, y, width, height = bounds
    return x + width * ACCEPT_ACROSS, y + height * ACCEPT_DOWN


def _run(command, run):
    try:
        done = run(command, capture_output=True, text=True, timeout=TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 else None


def window_bounds(run=subprocess.run):
    """The app's largest window as (x, y, width, height) in points, or None."""
    output = _run(["/usr/bin/osascript", "-l", "JavaScript", "-e", _WINDOWS_JXA], run)
    try:
        windows = json.loads(output) if output else []
    except ValueError:
        return None
    windows = [w for w in windows if isinstance(w, list) and len(w) == 4 and w[2] > 200 and w[3] > 200]
    return tuple(max(windows, key=lambda w: w[2] * w[3])) if windows else None


def app_in_front(run=subprocess.run):
    """True when the frontmost app is TFT's."""
    front = _run(["/usr/bin/lsappinfo", "front"], run)
    if not front:
        return False
    info = _run(["/usr/bin/lsappinfo", "info", "-only", "bundlepath", front.strip()], run) or ""
    return "Teamfight Tactics.app" in info


class Clicker:
    """One click on the accept button: click() answers None when made, else why not. Every outside call is a seam."""

    def __init__(self, run=subprocess.run, sleep=time.sleep, clock=time.monotonic, post=None, is_trusted=trusted):
        self._run, self._sleep, self._clock, self._post, self._trusted = run, sleep, clock, post, is_trusted

    def click(self):
        if not self._trusted():
            return NO_PERMISSION
        if not app_in_front(self._run):
            _run(["/usr/bin/open", "-a", APP_PATH], self._run)  # to its space, in front
            deadline = self._clock() + FRONT_WAIT_SECONDS
            while not app_in_front(self._run):
                if self._clock() >= deadline:
                    return NOT_FRONT
                self._sleep(0.2)
            self._sleep(SETTLE_SECONDS)
        bounds = window_bounds(self._run)
        if bounds is None:
            return NO_WINDOW
        try:
            (self._post or _post_click)(*accept_point(bounds))
        except (OSError, ValueError, AttributeError):
            return BROKE
        return None


def _post_click(x, y):
    """The mouse moved to (x, y), pressed and released there, then put back where it was."""
    cg, cf, _ = _frameworks()
    here = cg.CGEventCreate(None)
    back = cg.CGEventGetLocation(here)
    cf.CFRelease(here)
    point = CGPoint(x, y)
    # kCGEventMouseMoved 5, kCGEventLeftMouseDown 1, kCGEventLeftMouseUp 2; kCGHIDEventTap 0; kCGMouseButtonLeft 0.
    for kind, at, pause in ((5, point, 0.08), (1, point, 0.06), (2, point, 0.15), (5, back, 0)):
        event = cg.CGEventCreateMouseEvent(None, kind, at, 0)
        cg.CGEventPost(0, event)
        cf.CFRelease(event)
        if pause:
            time.sleep(pause)


class Switch:
    """The page's switch of the click on accept, kept in config.json; available on macOS only. Turned on, macOS is
    asked for the Accessibility permission, which its person grants once in System Settings."""

    def __init__(self, store, prompt=None):
        self._store = store
        self._prompt = prompt if prompt is not None else (lambda: trusted(prompt=True))
        self.available = available()

    def enabled(self):
        try:
            return self._store.read_autoaccept()
        except (OSError, ValueError):
            return False

    def enable(self):
        granted = self._prompt()
        print(PERMISSION_LINES[bool(granted)], flush=True)
        return self._set(True)

    def disable(self):
        return self._set(False)

    def _set(self, on):
        try:
            self._store.set_autoaccept(on)
        except (OSError, ValueError):  # a file that cannot be written, or no pair yet
            return False
        return True
