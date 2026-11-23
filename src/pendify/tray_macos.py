"""The icon in the menu bar on macOS: the counterpart of the icon by the clock (tray.py), a status item at the top
right whose menu opens the page, pauses or resumes the alerts, quits the program and, once an update is ready,
restarts into it, with the same items in the page's language of the moment, read each time the menu opens.

Standard library only: libobjc and AppKit through ctypes, every message sent through objc_msgSend cast to its own
prototype. The program is an accessory application (no Dock icon, no menu of its own) whose item is made, pumped
and removed on the main thread, the one thread AppKit allows: start() on it, then wait() as the main loop's wait
between two ticks, which pumps AppKit's events until its seconds pass or the stop is set, and close() on it as the
program ends. A quit or a restart chosen in the menu takes the item out at the pump's next turn, as the icon by the
clock goes at once. A failure is said in one fixed stderr line and never raises: the program runs without its item.
"""
import ctypes
import sys
import threading
import time

TIP = "PendiFy"
ICON_POINTS = 18.0  # the picture's side in the menu bar, in points: pendify.ico's frames are scaled to it
PUMP_SECONDS = 0.1  # the longest one wait for an event lasts, so a stop set on another thread ends the pump soon
ACCESSORY = 1  # NSApplicationActivationPolicyAccessory: no Dock icon and no menu bar of its own
VARIABLE_LENGTH = -1.0  # NSVariableStatusItemLength
ANY_EVENT = 0xFFFFFFFFFFFFFFFF  # NSEventMaskAny
TARGET_CLASS = "PendiFyMenuTarget"
OPEN, PAUSE, RESUME, QUIT, RESTART = 1, 2, 3, 4, 5  # the menu's commands, each item's tag; tray.py's numbers

NIL_LINE = " [tray] {call} answered nil"
BROKE_LINE = " [tray] the icon broke: {kind}"
OFF_MAIN_LINE = " [tray] no icon in the menu bar: it starts on the main thread only"

P, BOOL, LONG, DOUBLE = ctypes.c_void_p, ctypes.c_bool, ctypes.c_long, ctypes.c_double
_FRAMEWORK = "/System/Library/Frameworks/{0}.framework/{0}"
# A method's implementation as the runtime calls it: void (id self, SEL _cmd, id argument).
IMP = ctypes.CFUNCTYPE(None, P, P, P)


class NSSize(ctypes.Structure):
    _fields_ = [("width", DOUBLE), ("height", DOUBLE)]


_HANDLERS = {}  # a target's address: its (choose, update) callables


def _method(index):
    def method(target, _selector, argument):
        handlers = _HANDLERS.get(target)
        if handlers is not None:
            handlers[index](argument)
    return method


_CHOOSE, _UPDATE = IMP(_method(0)), IMP(_method(1))  # kept here for the process's life, so neither is collected


class _ObjC:
    """The facade: the runtime's functions the icon calls, a message sent with the prototype its arguments name,
    and the target the menu's items and its delegate call back into."""

    def __init__(self):
        self._lib = ctypes.CDLL("/usr/lib/libobjc.A.dylib")
        ctypes.CDLL(_FRAMEWORK.format("AppKit"))  # loaded for its classes
        foundation = ctypes.CDLL(_FRAMEWORK.format("Foundation"))
        bound = (("objc_getClass", (ctypes.c_char_p,), P), ("sel_registerName", (ctypes.c_char_p,), P),
                 ("objc_allocateClassPair", (P, ctypes.c_char_p, ctypes.c_size_t), P),
                 ("objc_registerClassPair", (P,), None), ("class_addMethod", (P, P, P, ctypes.c_char_p), BOOL),
                 ("objc_autoreleasePoolPush", (), P), ("objc_autoreleasePoolPop", (P,), None))
        for name, arguments, result in bound:
            function = getattr(self._lib, name)
            function.argtypes, function.restype = arguments, result
        self._msg_send = ctypes.cast(self._lib.objc_msgSend, P).value
        self._prototypes = {}
        self.default_mode = P.in_dll(foundation, "NSDefaultRunLoopMode").value

    def cls(self, name):
        return self._lib.objc_getClass(name.encode())

    def sel(self, name):
        return self._lib.sel_registerName(name.encode())

    def send(self, receiver, selector, *typed, res=P):
        """[receiver selector...] with `typed` as (ctypes type, value) pairs; `res` the answer's type."""
        types = tuple(kind for kind, _ in typed)
        function = self._prototypes.get((res, types))
        if function is None:
            function = self._prototypes[(res, types)] = ctypes.CFUNCTYPE(res, P, P, *types)(self._msg_send)
        return function(receiver, self.sel(selector), *(value for _, value in typed))

    def string(self, text):
        return self.send(self.cls("NSString"), "stringWithUTF8String:", (ctypes.c_char_p, text.encode()))

    def push(self):
        return self._lib.objc_autoreleasePoolPush()

    def pop(self, pool):
        self._lib.objc_autoreleasePoolPop(pool)

    def target(self, choose, update):
        """A new TARGET_CLASS object, the class made once per process, whose choose: calls `choose` with the item
        chosen and whose menuNeedsUpdate: calls `update` with the menu about to open."""
        made = self.cls(TARGET_CLASS)
        if not made:
            made = self._lib.objc_allocateClassPair(self.cls("NSObject"), TARGET_CLASS.encode(), 0)
            for name, implementation in (("choose:", _CHOOSE), ("menuNeedsUpdate:", _UPDATE)):
                self._lib.class_addMethod(made, self.sel(name), ctypes.cast(implementation, P), b"v@:@")
            self._lib.objc_registerClassPair(made)
        target = self.send(self.send(made, "alloc"), "init")
        _HANDLERS[target] = (choose, update)
        return target

    def forget(self, target):
        _HANDLERS.pop(target, None)


def _say(line):
    """One fixed line on stderr; from PendiFy.app it lands in pendify.log."""
    print(line, file=sys.stderr)


class MenuBarIcon:
    """The icon in the menu bar, built as tray.Tray is: `paused`, `words` and `update_ready` read each time the menu
    opens, `on_restart` the page's restart. `objc` is the facade of the Objective-C runtime, built from ctypes when
    None; a test passes a fake, and `clock` with it."""

    def __init__(self, url, on_open, on_pause, on_resume, on_quit, paused, words, icon_path, objc=None,
                 update_ready=None, on_restart=None, clock=time.monotonic):
        self.url = url
        self._update_ready, self._on_restart = update_ready, on_restart
        self._on_open, self._on_pause, self._on_resume, self._on_quit = on_open, on_pause, on_resume, on_quit
        self._paused, self._words, self._icon_path = paused, words, str(icon_path)
        self._objc, self._clock = objc, clock
        self._app = self._bar = self._item = self._menu = self._target = None
        self._leaving = False  # a quit or a restart chosen: the item goes at the pump's next turn

    def start(self):
        if threading.current_thread() is not threading.main_thread():
            _say(OFF_MAIN_LINE)
            return
        if self._objc is None:
            try:
                self._objc = _ObjC()
            except (OSError, AttributeError, ValueError) as failure:
                _say(BROKE_LINE.format(kind=type(failure).__name__))
                return
        pool = self._objc.push()
        try:
            self._open()
        except Exception as failure:  # never out of start: the program goes on without its item
            _say(BROKE_LINE.format(kind=type(failure).__name__))
            self._remove()
        finally:
            self._objc.pop(pool)

    def _open(self):
        """The accessory application, then the item: its picture (its name when the picture cannot be read), its
        tip and its menu, filled by its delegate each time it opens."""
        objc = self._objc
        app = objc.send(objc.cls("NSApplication"), "sharedApplication")
        if not app:
            _say(NIL_LINE.format(call="sharedApplication"))
            return
        objc.send(app, "setActivationPolicy:", (LONG, ACCESSORY), res=BOOL)  # before launching: no Dock icon at all
        objc.send(app, "finishLaunching", res=None)
        self._bar = objc.send(objc.cls("NSStatusBar"), "systemStatusBar")
        made = objc.send(self._bar, "statusItemWithLength:", (DOUBLE, VARIABLE_LENGTH))
        if not made:
            _say(NIL_LINE.format(call="statusItemWithLength:"))
            return
        self._item = objc.send(made, "retain")  # an item only autoreleased leaves the menu bar at once
        button = objc.send(self._item, "button")
        image = objc.send(objc.send(objc.cls("NSImage"), "alloc"), "initWithContentsOfFile:",
                          (P, objc.string(self._icon_path)))
        if image:
            objc.send(image, "setSize:", (NSSize, NSSize(ICON_POINTS, ICON_POINTS)), res=None)
            objc.send(button, "setImage:", (P, image), res=None)
            objc.send(image, "release", res=None)  # the button holds it
        else:
            _say(NIL_LINE.format(call="initWithContentsOfFile:"))
            objc.send(button, "setTitle:", (P, objc.string(TIP)), res=None)
        objc.send(button, "setToolTip:", (P, objc.string(TIP)), res=None)
        self._target = objc.target(self._choose, self._update)
        self._menu = objc.send(objc.send(objc.cls("NSMenu"), "alloc"), "initWithTitle:", (P, objc.string("")))
        objc.send(self._menu, "setAutoenablesItems:", (BOOL, False), res=None)
        objc.send(self._menu, "setDelegate:", (P, self._target), res=None)
        objc.send(self._item, "setMenu:", (P, self._menu), res=None)
        self._app = app  # last: the pump runs only once the item is whole

    def wait(self, stop, seconds):
        """The main loop's wait between two ticks: AppKit's events pumped on this, the main thread, each wait for
        one at most PUMP_SECONDS, until `seconds` pass or `stop` is set; True once it is set, as stop.wait answers.
        With no item, or once the pump broke, the stop's own wait."""
        if self._app is None:
            return stop.wait(seconds)
        objc, deadline = self._objc, self._clock() + seconds
        while not stop.is_set():
            left = deadline - self._clock()
            if left <= 0:
                return False
            pool = objc.push()
            try:
                until = objc.send(objc.cls("NSDate"), "dateWithTimeIntervalSinceNow:",
                                  (DOUBLE, min(left, PUMP_SECONDS)))
                event = objc.send(self._app, "nextEventMatchingMask:untilDate:inMode:dequeue:",
                                  (ctypes.c_uint64, ANY_EVENT), (P, until), (P, objc.default_mode), (BOOL, True))
                if event:
                    objc.send(self._app, "sendEvent:", (P, event), res=None)  # a click's action runs in here
                if self._leaving:
                    self._remove()
            except Exception as failure:  # never out of the main loop: the program goes on, its item still
                _say(BROKE_LINE.format(kind=type(failure).__name__))
                self._app = None
                return stop.wait(max(0.0, deadline - self._clock()))
            finally:
                objc.pop(pool)
        return True

    def close(self):
        """The item out of the menu bar and what it holds released, on the main thread as start; once only."""
        if self._objc is None or self._item is None:
            return
        pool = self._objc.push()
        try:
            self._remove()
        finally:
            self._objc.pop(pool)

    def _remove(self):
        objc, self._leaving = self._objc, False
        if self._item:
            objc.send(self._bar, "removeStatusItem:", (P, self._item), res=None)
            objc.send(self._item, "release", res=None)
        if self._menu:
            objc.send(self._menu, "release", res=None)
        if self._target:
            objc.forget(self._target)
            objc.send(self._target, "release", res=None)
        self._item = self._menu = self._target = None

    def _update(self, menu):
        """menuNeedsUpdate:, as the menu opens: its items in the words and the pause of this moment."""
        try:
            objc, words = self._objc, self._words()
            middle = (RESUME, words["resume"]) if self._paused() else (PAUSE, words["pause"])
            ready = self._update_ready() if self._update_ready is not None and self._on_restart is not None else None
            restart = () if ready is None else ((RESTART, words["update_tray_restart"].format(version=ready)),)
            objc.send(menu, "removeAllItems", res=None)
            for entry in ((OPEN, words["tray_open"]), middle, None, *restart, (QUIT, words["quit"])):
                objc.send(menu, "addItem:", (P, self._entry(entry)), res=None)
        except Exception as failure:  # never out of the delegate: the menu opens as it is
            _say(BROKE_LINE.format(kind=type(failure).__name__))

    def _entry(self, entry):
        objc = self._objc
        if entry is None:
            return objc.send(objc.cls("NSMenuItem"), "separatorItem")
        tag, title = entry
        made = objc.send(objc.send(objc.cls("NSMenuItem"), "alloc"), "initWithTitle:action:keyEquivalent:",
                         (P, objc.string(title)), (P, objc.sel("choose:")), (P, objc.string("")))
        objc.send(made, "setTarget:", (P, self._target), res=None)
        objc.send(made, "setTag:", (LONG, tag), res=None)
        return objc.send(made, "autorelease")  # the menu holds it

    def _choose(self, item):
        """choose:, an item's action, run inside the pump's sendEvent:."""
        try:
            chosen = self._objc.send(item, "tag", res=LONG)
            if chosen == OPEN:
                self._on_open()
            elif chosen == PAUSE:
                self._on_pause()
            elif chosen == RESUME:
                self._on_resume()
            elif chosen in (QUIT, RESTART):  # either ends this copy: its item goes at the pump's next turn
                (self._on_quit if chosen == QUIT else self._on_restart)()
                self._leaving = True
        except Exception as failure:  # never out of the action: ctypes would only print it
            _say(BROKE_LINE.format(kind=type(failure).__name__))
