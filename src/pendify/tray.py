"""The icon by the clock (lane pftray): an icon in the notification area whose menu opens the page, pauses or
resumes the alerts, quits the program and, once an update is ready, restarts into it, so the program can be stopped with the page and the browser closed.

Standard library only: user32, shell32 and kernel32 by ctypes, bound for 64-bit Windows. The icon lives on a thread
of its own, a daemon, with a hidden top-level tool window (never shown, so no taskbar entry and no Alt+Tab slot,
yet the shell's TaskbarCreated broadcast reaches it) and its message loop; a left or right click on it shows the menu
in the page's language of that moment. A Win32 call that fails is said in one fixed stderr line, as the page says a
failed request, and never raises out of the thread: the program runs without an icon rather than dying under
pythonw. On a platform other than Windows, start() says so in one line and does nothing.
"""
import ctypes
import os
import sys
import threading

TIP = "PendiFy"
CLASS_NAME = "PendiFyTray"
THREAD_NAME = "pendify tray"
CLOSE_SECONDS = 3.0  # how long close() waits for the window to exist, then for the thread to end
ICON_ID = 1

WM_NULL, WM_DESTROY, WM_CLOSE, WM_CONTEXTMENU = 0x0000, 0x0002, 0x0010, 0x007B
WM_LBUTTONUP, WM_RBUTTONUP, WM_APP = 0x0202, 0x0205, 0x8000
CALLBACK_MESSAGE = WM_APP + 1
CLICKS = (WM_LBUTTONUP, WM_RBUTTONUP, WM_CONTEXTMENU)
WS_OVERLAPPED, WS_EX_TOOLWINDOW = 0x0, 0x80  # the window's style; a tool window takes no taskbar entry
NIM_ADD, NIM_DELETE = 0, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP = 0x1, 0x2, 0x4
IMAGE_ICON, LR_LOADFROMFILE, LR_DEFAULTSIZE = 1, 0x10, 0x40
MF_STRING, MF_SEPARATOR = 0x0, 0x800
TPM_RIGHTBUTTON, TPM_NONOTIFY, TPM_RETURNCMD = 0x2, 0x80, 0x100
OPEN, PAUSE, RESUME, QUIT, RESTART = 1, 2, 3, 4, 5  # the menu's commands

FAILED_LINE = " [tray] {call} failed (error {code})"
BROKE_LINE = " [tray] the icon broke: {kind}"
HELD_LINE = " [tray] the icon's thread did not end in {seconds:g} seconds: its icon deleted from close"
EARLY_LINE = " [tray] the icon's thread did not end in {seconds:g} seconds before its window existed"
OTHER_PLATFORM_LINE = " [tray] no icon by the clock: this platform is not Windows"

# The Win32 types for 64-bit Windows: handles as c_void_p, WPARAM as c_size_t, LPARAM and LRESULT as c_ssize_t.
HANDLE, UINT, INT, WSTR = ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_wchar_p
WPARAM, LPARAM, LRESULT = ctypes.c_size_t, ctypes.c_ssize_t, ctypes.c_ssize_t
# The window procedure's type, made once: LRESULT (HWND, UINT, WPARAM, LPARAM). WINFUNCTYPE exists on Windows only.
WNDPROC = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)(LRESULT, HANDLE, UINT, WPARAM, LPARAM)


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class MSG(ctypes.Structure):
    _fields_ = [("hwnd", HANDLE), ("message", UINT), ("wParam", WPARAM), ("lParam", LPARAM), ("time", UINT),
                ("pt", POINT), ("lPrivate", UINT)]


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", INT), ("cbWndExtra", INT),
                ("hInstance", HANDLE), ("hIcon", HANDLE), ("hCursor", HANDLE), ("hbrBackground", HANDLE),
                ("lpszMenuName", WSTR), ("lpszClassName", WSTR)]


class GUID(ctypes.Structure):
    _fields_ = [("Data1", UINT), ("Data2", ctypes.c_ushort), ("Data3", ctypes.c_ushort),
                ("Data4", ctypes.c_ubyte * 8)]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [("cbSize", UINT), ("hWnd", HANDLE), ("uID", UINT), ("uFlags", UINT), ("uCallbackMessage", UINT),
                ("hIcon", HANDLE), ("szTip", ctypes.c_wchar * 128), ("dwState", UINT), ("dwStateMask", UINT),
                ("szInfo", ctypes.c_wchar * 256), ("uVersion", UINT), ("szInfoTitle", ctypes.c_wchar * 64),
                ("dwInfoFlags", UINT), ("guidItem", GUID), ("hBalloonIcon", HANDLE)]


class _Win32:
    """The facade: the Win32 functions the icon calls, each bound with its argtypes and restype, and the last
    error of the calling thread."""

    def __init__(self):
        user32, shell32, kernel32 = (ctypes.WinDLL(name, use_last_error=True)
                                     for name in ("user32", "shell32", "kernel32"))
        bound = ((kernel32, "GetModuleHandleW", (WSTR,), HANDLE),
                 (user32, "RegisterClassW", (ctypes.POINTER(WNDCLASSW),), ctypes.c_ushort),
                 (user32, "UnregisterClassW", (WSTR, HANDLE), INT),
                 (user32, "CreateWindowExW", (UINT, WSTR, WSTR, UINT, INT, INT, INT, INT, HANDLE, HANDLE, HANDLE,
                                              HANDLE), HANDLE),
                 (user32, "DestroyWindow", (HANDLE,), INT),
                 (user32, "DefWindowProcW", (HANDLE, UINT, WPARAM, LPARAM), LRESULT),
                 (user32, "GetMessageW", (ctypes.POINTER(MSG), HANDLE, UINT, UINT), INT),
                 (user32, "TranslateMessage", (ctypes.POINTER(MSG),), INT),
                 (user32, "DispatchMessageW", (ctypes.POINTER(MSG),), LRESULT),
                 (user32, "PostMessageW", (HANDLE, UINT, WPARAM, LPARAM), INT),
                 (user32, "PostQuitMessage", (INT,), None),
                 (user32, "RegisterWindowMessageW", (WSTR,), UINT),
                 (user32, "LoadImageW", (HANDLE, WSTR, UINT, INT, INT, UINT), HANDLE),
                 (user32, "DestroyIcon", (HANDLE,), INT),
                 (user32, "CreatePopupMenu", (), HANDLE),
                 (user32, "AppendMenuW", (HANDLE, UINT, WPARAM, WSTR), INT),
                 (user32, "DestroyMenu", (HANDLE,), INT),
                 (user32, "GetCursorPos", (ctypes.POINTER(POINT),), INT),
                 (user32, "SetForegroundWindow", (HANDLE,), INT),
                 (user32, "TrackPopupMenu", (HANDLE, UINT, INT, INT, INT, HANDLE, HANDLE), INT),
                 (shell32, "Shell_NotifyIconW", (UINT, ctypes.POINTER(NOTIFYICONDATAW)), INT))
        for library, name, arguments, result in bound:
            function = getattr(library, name)
            function.argtypes, function.restype = arguments, result
            setattr(self, name, function)

    @staticmethod
    def last_error():
        return ctypes.get_last_error()


def _windows():
    return os.name == "nt"


def _say(line):
    """One fixed line on stderr, the page's road for a failure; under pythonw there is no stderr and it goes nowhere."""
    print(line, file=sys.stderr)


class Tray:
    """The icon by the clock. `paused` answers the watcher's state and `words` the page's table for the language set
    at that moment; both are read at each click. `win32` is the facade of the Win32 functions, built from ctypes on
    Windows when None; a test passes a fake. `update_ready` answers the version an install made ready, or None, read
    at each click as well, and `on_restart` is the page's restart: with both, a ready version adds its item."""

    def __init__(self, url, on_open, on_pause, on_resume, on_quit, paused, words, icon_path, win32=None,
                 update_ready=None, on_restart=None):
        self.url = url
        self._update_ready, self._on_restart = update_ready, on_restart
        self._on_open, self._on_pause, self._on_resume, self._on_quit = on_open, on_pause, on_resume, on_quit
        self._paused, self._words, self._icon_path = paused, words, str(icon_path)
        self._win32 = win32
        self._wndproc = WNDPROC(self._proc)  # made once and kept here, so the window's procedure is never collected
        self._thread = None
        self._ready = threading.Event()  # set once the window exists, or once it cannot
        self._instance = self._hwnd = self._icon = None
        self._registered, self._taskbar_created = False, 0
        self._closing = False  # set by close(): an _open still under way then adds no icon

    def start(self):
        """The icon on its own thread, a daemon, so a thread held past close() never keeps the process alive."""
        if self._win32 is None:
            if not _windows():
                _say(OTHER_PLATFORM_LINE)
                return
            try:
                self._win32 = _Win32()
            except (OSError, AttributeError) as failure:
                _say(BROKE_LINE.format(kind=type(failure).__name__))
                return
        self._thread = threading.Thread(target=self._run, name=THREAD_NAME, daemon=True)
        self._thread.start()

    def close(self):
        """Posts WM_CLOSE to the window when it exists, then waits a few seconds for the thread. A thread still held
        then (in TrackPopupMenu's modal loop, say) leaves no dead icon: close deletes it from the calling thread.
        A thread held before its window existed adds no icon once it goes on, so there is none to delete."""
        self._closing = True
        if self._thread is None:
            return
        self._ready.wait(CLOSE_SECONDS)
        hwnd = self._hwnd
        if hwnd is not None and self._thread.is_alive() and not self._win32.PostMessageW(hwnd, WM_CLOSE, 0, 0):
            self._failed("PostMessageW")
        self._thread.join(CLOSE_SECONDS)
        if self._thread.is_alive():
            if hwnd is not None:
                self._notify(NIM_DELETE, hwnd)
                _say(HELD_LINE.format(seconds=CLOSE_SECONDS))
            else:
                _say(EARLY_LINE.format(seconds=CLOSE_SECONDS))

    def _failed(self, call):
        _say(FAILED_LINE.format(call=call, code=self._win32.last_error()))

    def _run(self):
        try:
            if self._open():
                self._loop()
        except Exception as failure:  # never out of the thread: the program goes on without its icon
            _say(BROKE_LINE.format(kind=type(failure).__name__))
        finally:
            self._ready.set()
            self._release()

    def _open(self):
        """The window class, the hidden top-level tool window, the icon's picture and the TaskbarCreated message;
        then the icon. False when there is no window to serve."""
        win32 = self._win32
        self._instance = win32.GetModuleHandleW(None)
        if not self._instance:
            self._failed("GetModuleHandleW")
        window_class = WNDCLASSW(lpfnWndProc=self._wndproc, hInstance=self._instance, lpszClassName=CLASS_NAME)
        self._registered = bool(win32.RegisterClassW(window_class))
        if not self._registered:
            self._failed("RegisterClassW")
            return False
        hwnd = win32.CreateWindowExW(WS_EX_TOOLWINDOW, CLASS_NAME, TIP, WS_OVERLAPPED, 0, 0, 0, 0, None, None,
                                     self._instance, None)  # top-level, never shown: TaskbarCreated reaches it
        if not hwnd:
            self._failed("CreateWindowExW")
            return False
        self._hwnd = hwnd
        self._icon = win32.LoadImageW(None, self._icon_path, IMAGE_ICON, 0, 0, LR_LOADFROMFILE | LR_DEFAULTSIZE)
        if not self._icon:
            self._failed("LoadImageW")
        self._taskbar_created = win32.RegisterWindowMessageW("TaskbarCreated")
        if not self._taskbar_created:
            self._failed("RegisterWindowMessageW")
        if self._closing:  # close() has run: no icon, and _release destroys the window and unregisters the class
            return False
        self._notify(NIM_ADD)
        self._ready.set()
        return True

    def _loop(self):
        win32, message = self._win32, MSG()
        while True:
            got = win32.GetMessageW(message, None, 0, 0)
            if got == 0:  # WM_QUIT, posted at WM_DESTROY
                return
            if got == -1:
                self._failed("GetMessageW")
                return
            win32.TranslateMessage(message)
            win32.DispatchMessageW(message)

    def _release(self):
        """What the thread made, undone as it ends; the window only when its loop ended without WM_CLOSE."""
        win32 = self._win32
        if self._hwnd is not None:
            self._notify(NIM_DELETE)
            if not win32.DestroyWindow(self._hwnd):
                self._failed("DestroyWindow")
            self._hwnd = None
        if self._icon and not win32.DestroyIcon(self._icon):
            self._failed("DestroyIcon")
        self._icon = None
        if self._registered and not win32.UnregisterClassW(CLASS_NAME, self._instance):
            self._failed("UnregisterClassW")
        self._registered = False

    def _notify(self, action, hwnd=None):
        """The icon added or deleted; `hwnd` names the window when the caller holds it (close, off the thread)."""
        data = NOTIFYICONDATAW(cbSize=ctypes.sizeof(NOTIFYICONDATAW), hWnd=self._hwnd if hwnd is None else hwnd,
                               uID=ICON_ID)
        if action == NIM_ADD:
            data.uFlags, data.uCallbackMessage = NIF_MESSAGE | NIF_ICON | NIF_TIP, CALLBACK_MESSAGE
            data.hIcon, data.szTip = self._icon, TIP
        if not self._win32.Shell_NotifyIconW(action, data):
            self._failed("Shell_NotifyIconW")

    def _proc(self, hwnd, message, wparam, lparam):
        try:
            if message == CALLBACK_MESSAGE:
                if (lparam & 0xFFFF) in CLICKS:
                    self._menu(hwnd)
                return 0
            if message == WM_CLOSE:
                self._notify(NIM_DELETE)
                if not self._win32.DestroyWindow(hwnd):
                    self._failed("DestroyWindow")
                return 0
            if message == WM_DESTROY:
                self._hwnd = None
                self._win32.PostQuitMessage(0)
                return 0
            if self._taskbar_created and message == self._taskbar_created:  # Explorer restarted: the icon again
                self._notify(NIM_ADD)
                return 0
        except Exception as failure:  # never out of the procedure: the window goes on serving
            _say(BROKE_LINE.format(kind=type(failure).__name__))
            return 0
        return self._win32.DefWindowProcW(hwnd, message, wparam, lparam)

    def _menu(self, hwnd):
        """The menu at the cursor, in the words and the pause of this moment, then its command. TrackPopupMenu
        answers 0 when the menu is dismissed, which is no failure."""
        win32, words = self._win32, self._words()
        middle = (RESUME, words["resume"]) if self._paused() else (PAUSE, words["pause"])
        ready = self._update_ready() if self._update_ready is not None and self._on_restart is not None else None
        restart = () if ready is None else ((RESTART, words["update_tray_restart"].format(version=ready)),)
        menu = win32.CreatePopupMenu()
        if not menu:
            self._failed("CreatePopupMenu")
            return
        try:
            for item in ((OPEN, words["tray_open"]), middle, None, *restart, (QUIT, words["quit"])):
                flags, command, text = (MF_SEPARATOR, 0, None) if item is None else (MF_STRING, *item)
                if not win32.AppendMenuW(menu, flags, command, text):
                    self._failed("AppendMenuW")
            cursor = POINT()
            if not win32.GetCursorPos(cursor):
                self._failed("GetCursorPos")
            if not win32.SetForegroundWindow(hwnd):  # without it the menu stays open on a click elsewhere
                self._failed("SetForegroundWindow")
            chosen = win32.TrackPopupMenu(menu, TPM_RETURNCMD | TPM_NONOTIFY | TPM_RIGHTBUTTON, cursor.x, cursor.y,
                                          0, hwnd, None)
            if not win32.PostMessageW(hwnd, WM_NULL, 0, 0):  # lets the menu close on a click elsewhere next time
                self._failed("PostMessageW")
        finally:
            if not win32.DestroyMenu(menu):
                self._failed("DestroyMenu")
        if chosen == OPEN:
            self._on_open()
        elif chosen == PAUSE:
            self._on_pause()
        elif chosen == RESUME:
            self._on_resume()
        elif chosen in (QUIT, RESTART):  # either ends this copy: its icon goes at once
            (self._on_quit if chosen == QUIT else self._on_restart)()
            if not win32.PostMessageW(hwnd, WM_CLOSE, 0, 0):
                self._failed("PostMessageW")
