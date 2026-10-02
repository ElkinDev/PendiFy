"""TrayTest: the icon by the clock on a fake Win32 facade that records every call and plays the window's messages
on the tray's own thread, so no case shows a real icon. The Win32 numbers are written here as literals, so a wrong
constant in the module is red."""
import contextlib
import ctypes
import io
import queue
import threading
import time
import unittest

import support

page = support.module("page")
tray = support.module("tray")

WINDOW, MENU, ICON, INSTANCE, ATOM, REBIRTH = 0x1234, 0x77, 0x99, 0x400000, 0xC001, 0xC0FE
CURSOR = (10, 20)
ERROR = 1008  # what last_error answers after a call the fake fails; any code would do
ICON_PATH = support.package_dir() / "pendify.ico"
URL = "http://127.0.0.1:50000/"
# NOTIFYICONDATAW's size on 64-bit and on 32-bit Windows: a field missing or misaligned changes it.
NOTIFY_SIZE = {8: 976, 4: 956}[ctypes.sizeof(ctypes.c_void_p)]
CALLBACK = 0x8001  # WM_APP + 1
ADD_FLAGS = 0x7  # NIF_MESSAGE, NIF_ICON and NIF_TIP
WM_NULL, WM_CLOSE, WM_CONTEXTMENU, WM_MOUSEMOVE, WM_LBUTTONUP, WM_RBUTTONUP = 0x0, 0x10, 0x7B, 0x200, 0x202, 0x205
MENU_FLAGS = 0x182  # TPM_RETURNCMD, TPM_NONOTIFY and TPM_RIGHTBUTTON
FAILED = " [tray] {} failed (error 1008)\n"


class FakeWin32:
    """The facade's functions as the tray calls them. Every call is kept in `calls` as (name, *what matters); a
    message the tray posts or a case sends waits in a queue that GetMessageW hands out and DispatchMessageW delivers
    to the window procedure RegisterClassW was given. `fail` names the calls that answer 0; TrackPopupMenu answers
    the command of the item whose text is `pick`, or 0, the menu dismissed, when no item has it."""

    def __init__(self, fail=()):
        self.calls, self.menus, self.fail, self.pick = [], [], set(fail), None
        self.messages, self.proc, self.current = queue.Queue(), None, None

    def note(self, name, *what, answer=1):
        self.calls.append((name, *what))
        return 0 if name in self.fail else answer

    def send(self, message, wparam=0, lparam=0):
        """A message to the window, as Windows posts it, and the wait until its procedure has returned."""
        done = threading.Event()
        self.messages.put((WINDOW, message, wparam, lparam, done))
        if not done.wait(5):
            raise AssertionError(f"the message {message:#x} was never dispatched")

    def heard(self):
        return [call[0] for call in self.calls if call[0].startswith("on_")]

    def GetModuleHandleW(self, name):
        return self.note("GetModuleHandleW", name, answer=INSTANCE)

    def RegisterClassW(self, window_class):
        self.proc = window_class.lpfnWndProc
        return self.note("RegisterClassW", window_class.lpszClassName, window_class.hInstance, answer=ATOM)

    def UnregisterClassW(self, name, instance):
        return self.note("UnregisterClassW", name, instance)

    def CreateWindowExW(self, ex_style, class_name, title, style, x, y, width, height, parent, menu, instance, param):
        return self.note("CreateWindowExW", ex_style, class_name, style, parent, instance, answer=WINDOW)

    def DestroyWindow(self, hwnd):
        answer = self.note("DestroyWindow", hwnd)
        self.proc(hwnd, 0x2, 0, 0)  # WM_DESTROY, sent inside the call as Windows does
        return answer

    def DefWindowProcW(self, hwnd, message, wparam, lparam):
        self.calls.append(("DefWindowProcW", message))
        return 0

    def LoadImageW(self, instance, path, kind, width, height, flags):
        return self.note("LoadImageW", instance, path, kind, width, height, flags, answer=ICON)

    def DestroyIcon(self, icon):
        return self.note("DestroyIcon", icon)

    def RegisterWindowMessageW(self, name):
        return self.note("RegisterWindowMessageW", name, answer=REBIRTH)

    def Shell_NotifyIconW(self, action, data):
        return self.note("Shell_NotifyIconW", action, data.cbSize, data.hWnd, data.uID, data.uFlags,
                         data.uCallbackMessage, data.hIcon, data.szTip)

    def PostMessageW(self, hwnd, message, wparam, lparam):
        self.messages.put((hwnd, message, wparam, lparam, None))
        return self.note("PostMessageW", hwnd, message)

    def PostQuitMessage(self, code):
        self.calls.append(("PostQuitMessage", code))
        self.messages.put(None)

    def GetMessageW(self, message, hwnd, low, high):
        try:
            item = self.messages.get(timeout=10)
        except queue.Empty:  # a case that never closes still ends the thread
            return 0
        if item is None:
            return 0
        message.hwnd, message.message, message.wParam, message.lParam, self.current = item
        return 1

    def TranslateMessage(self, message):
        return 0

    def DispatchMessageW(self, message):
        try:
            return self.proc(message.hwnd, message.message, message.wParam, message.lParam)
        finally:
            if self.current is not None:
                self.current.set()

    def CreatePopupMenu(self):
        self.menus.append([])
        return self.note("CreatePopupMenu", answer=MENU)

    def AppendMenuW(self, menu, flags, item, text):
        self.menus[-1].append((flags, item, text))
        return self.note("AppendMenuW", menu, flags, text)

    def GetCursorPos(self, point):
        point.x, point.y = CURSOR
        return self.note("GetCursorPos")

    def SetForegroundWindow(self, hwnd):
        return self.note("SetForegroundWindow", hwnd)

    def TrackPopupMenu(self, menu, flags, x, y, reserved, hwnd, rect):
        self.calls.append(("TrackPopupMenu", menu, flags, x, y, hwnd))
        return next((item for _, item, text in self.menus[-1] if text is not None and text == self.pick), 0)

    def DestroyMenu(self, menu):
        return self.note("DestroyMenu", menu)

    def last_error(self):
        return ERROR


class StuckWin32(FakeWin32):
    """GetMessageW never returns while the case runs, as a thread held in TrackPopupMenu's modal loop; it answers
    WM_QUIT once the case lets it go. Each Shell_NotifyIconW keeps its action and the name of the calling thread."""

    def __init__(self):
        super().__init__()
        self.waiting, self.release, self.notified_by = threading.Event(), threading.Event(), []

    def GetMessageW(self, message, hwnd, low, high):
        self.waiting.set()
        self.release.wait(10)
        return 0

    def Shell_NotifyIconW(self, action, data):
        self.notified_by.append((action, threading.current_thread().name))
        return super().Shell_NotifyIconW(action, data)


def tray_threads():
    return [thread for thread in threading.enumerate() if thread.name == tray.THREAD_NAME]


def ended(limit=5):
    """True once no tray thread runs, within `limit` seconds."""
    deadline = time.monotonic() + limit
    while tray_threads() and time.monotonic() < deadline:
        time.sleep(0.01)
    return not tray_threads()


class TrayTest(unittest.TestCase):
    def setUp(self):
        self.fake = FakeWin32()
        self.now = {"paused": False, "words": page.WORDS["es"]}  # what the two callables answer at this moment
        self.err = io.StringIO()  # the tray's stderr lines; every case captures them
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(contextlib.redirect_stderr(self.err))

    def make(self, fake=None):
        fake = self.fake if fake is None else fake

        def heard(name):
            return lambda: fake.calls.append((name,))

        return tray.Tray(URL, on_open=heard("on_open"), on_pause=heard("on_pause"), on_resume=heard("on_resume"),
                         on_quit=heard("on_quit"), paused=lambda: self.now["paused"],
                         words=lambda: self.now["words"], icon_path=ICON_PATH, win32=fake)

    def started(self, fake=None):
        """A tray on the fake with its window open and its loop serving; closed at the end of the case."""
        icon = self.make(fake)
        self.addCleanup(icon.close)
        icon.start()
        (self.fake if fake is None else fake).send(WM_NULL)  # dispatched only once the loop runs
        return icon

    def added(self):
        return ("Shell_NotifyIconW", 0, NOTIFY_SIZE, WINDOW, 1, ADD_FLAGS, CALLBACK, ICON, "PendiFy")

    def test_start_adds_the_icon(self):
        # Mutation: uCallbackMessage left 0. Red: a click would never reach the window.
        # Mutation: the parent HWND_MESSAGE. Red: a message-only window, which never hears TaskbarCreated.
        # Mutation: WS_EX_TOOLWINDOW dropped. Red: the hidden window may take a taskbar entry or an Alt+Tab slot.
        self.started()
        self.assertEqual(self.fake.calls[:6], [
            ("GetModuleHandleW", None),
            ("RegisterClassW", tray.CLASS_NAME, INSTANCE),
            ("CreateWindowExW", 0x80, tray.CLASS_NAME, 0, None, INSTANCE),  # WS_EX_TOOLWINDOW, WS_OVERLAPPED, top-level
            ("LoadImageW", None, str(ICON_PATH), 1, 0, 0, 0x50),  # IMAGE_ICON; LR_LOADFROMFILE, LR_DEFAULTSIZE
            ("RegisterWindowMessageW", "TaskbarCreated"),
            self.added()])
        self.assertTrue(ICON_PATH.is_file())
        self.assertEqual(self.err.getvalue(), "")

    def test_a_click_builds_the_menu_in_the_language_of_the_moment(self):
        # Mutation: the words read once at start. Red: Spanish after the switch to English.
        # Mutation: pause shown whatever paused() says. Red: Pausar while paused.
        self.started()
        first = len(self.fake.calls)
        self.fake.send(CALLBACK, 1, WM_RBUTTONUP)
        self.fake.send(0x401)  # behind the WM_NULL the menu posted, so that one is dispatched too
        one_menu = self.fake.calls[first:]
        self.now["paused"] = True
        self.fake.send(CALLBACK, 1, WM_LBUTTONUP)
        self.now["words"] = page.WORDS["en"]
        self.fake.send(CALLBACK, 1, WM_CONTEXTMENU)
        self.fake.send(CALLBACK, 1, WM_MOUSEMOVE)  # not a click: no menu
        self.assertEqual([[(flags, text) for flags, _, text in menu] for menu in self.fake.menus], [
            [(0, "Abrir la página"), (0, "Pausar avisos"), (0x800, None), (0, "Salir")],
            [(0, "Abrir la página"), (0, "Reanudar avisos"), (0x800, None), (0, "Salir")],
            [(0, "Open the page"), (0, "Resume alerts"), (0x800, None), (0, "Quit")]])
        self.assertEqual(one_menu, [
            ("CreatePopupMenu",),
            ("AppendMenuW", MENU, 0, "Abrir la página"), ("AppendMenuW", MENU, 0, "Pausar avisos"),
            ("AppendMenuW", MENU, 0x800, None), ("AppendMenuW", MENU, 0, "Salir"),
            ("GetCursorPos",), ("SetForegroundWindow", WINDOW),
            ("TrackPopupMenu", MENU, MENU_FLAGS, *CURSOR, WINDOW),
            ("PostMessageW", WINDOW, WM_NULL),
            ("DestroyMenu", MENU),
            ("DefWindowProcW", WM_NULL), ("DefWindowProcW", 0x401)])
        self.assertEqual(self.fake.heard(), [])  # the menu dismissed: no command
        self.assertEqual(self.err.getvalue(), "")

    def test_each_command_calls_its_callback(self):
        # Mutation: quit closes the window without its callback. Red: on_quit never heard.
        # Mutation: pause and resume swapped. Red: on_resume heard for Pausar.
        icon = self.started()
        for pick, paused in (("Abrir la página", False), ("Pausar avisos", False), ("Reanudar avisos", True),
                             (None, False)):
            self.now["paused"] = paused
            self.fake.pick = pick
            self.fake.send(CALLBACK, 1, WM_RBUTTONUP)
        self.assertEqual(self.fake.heard(), ["on_open", "on_pause", "on_resume"])
        self.fake.pick = "Salir"
        self.fake.send(CALLBACK, 1, WM_RBUTTONUP)
        self.assertTrue(ended())  # quit closes the icon's window, and its loop ends
        quit_at = self.fake.calls.index(("on_quit",))
        self.assertEqual(self.fake.calls[quit_at + 1], ("PostMessageW", WINDOW, WM_CLOSE))
        self.assertIn(("Shell_NotifyIconW", 2, NOTIFY_SIZE, WINDOW, 1, 0, 0, None, ""), self.fake.calls[quit_at:])
        icon.close()  # after the quit, close posts nothing more
        self.assertEqual(self.fake.calls.count(("PostMessageW", WINDOW, WM_CLOSE)), 1)
        self.assertEqual(self.fake.heard(), ["on_open", "on_pause", "on_resume", "on_quit"])
        self.assertEqual(self.err.getvalue(), "")

    def test_the_taskbars_rebirth_re_adds_the_icon(self):
        # Mutation: TaskbarCreated not handled. Red: one NIM_ADD only.
        self.started()
        self.fake.send(REBIRTH)
        self.fake.send(0x401)  # any other message goes to DefWindowProcW
        self.assertEqual(self.fake.calls.count(self.added()), 2)
        self.assertIn(("DefWindowProcW", 0x401), self.fake.calls)
        self.assertEqual(self.err.getvalue(), "")

    def test_close_deletes_the_icon(self):
        # Mutation: close joins without posting WM_CLOSE. Red: the thread still runs and the icon stays.
        icon = self.started()
        first = len(self.fake.calls)
        icon.close()
        self.assertEqual(tray_threads(), [])  # joined before close returns
        self.assertEqual(self.fake.calls[first:], [
            ("PostMessageW", WINDOW, WM_CLOSE),
            ("Shell_NotifyIconW", 2, NOTIFY_SIZE, WINDOW, 1, 0, 0, None, ""),  # NIM_DELETE
            ("DestroyWindow", WINDOW),
            ("PostQuitMessage", 0),
            ("DestroyIcon", ICON),
            ("UnregisterClassW", tray.CLASS_NAME, INSTANCE)])
        self.assertEqual(self.err.getvalue(), "")

    def test_close_deletes_the_icon_itself_when_the_thread_does_not_end(self):
        # Mutation: close only joins. Red: no NIM_DELETE from close, a dead icon stays by the clock.
        # Mutation: the thread not a daemon. Red: a held thread keeps the process alive after main returns.
        stuck = StuckWin32()
        icon = self.make(stuck)
        self.addCleanup(lambda: self.assertTrue(ended()))  # last: the thread let go ends by itself
        self.addCleanup(stuck.release.set)
        self.addCleanup(setattr, tray, "CLOSE_SECONDS", tray.CLOSE_SECONDS)
        tray.CLOSE_SECONDS = 0.2
        icon.start()
        self.assertTrue(stuck.waiting.wait(5))
        icon.close()
        self.assertEqual(len(tray_threads()), 1)  # still held when close returns
        self.assertEqual(stuck.notified_by, [(0, tray.THREAD_NAME), (2, threading.current_thread().name)])
        self.assertEqual(stuck.calls[-1], ("Shell_NotifyIconW", 2, NOTIFY_SIZE, WINDOW, 1, 0, 0, None, ""))
        self.assertEqual(self.err.getvalue(),
                         " [tray] the icon's thread did not end in 0.2 seconds: its icon deleted from close\n")
        self.assertTrue(all(thread.daemon for thread in tray_threads()))

    def test_a_failed_shell_call_is_logged_and_does_not_raise(self):
        # Mutation: a zero return raised. Red: the thread dies with a traceback on stderr and serves no menu.
        failing = FakeWin32(fail={"Shell_NotifyIconW"})
        icon = self.started(failing)
        failing.send(CALLBACK, 1, WM_RBUTTONUP)  # the program goes on without its icon; the window still serves
        icon.close()
        self.assertEqual(len(failing.menus), 1)
        self.assertEqual(tray_threads(), [])
        self.assertEqual(self.err.getvalue(), FAILED.format("Shell_NotifyIconW") * 2)  # the add, then the delete
        # A window that cannot be made: said, no icon, and the thread ends by itself.
        self.err.truncate(0)
        self.err.seek(0)
        no_window = FakeWin32(fail={"CreateWindowExW"})
        icon = self.make(no_window)
        icon.start()
        self.assertTrue(ended())
        icon.close()
        self.assertEqual(self.err.getvalue(), FAILED.format("CreateWindowExW"))
        self.assertNotIn("Shell_NotifyIconW", [call[0] for call in no_window.calls])
        self.assertEqual(no_window.calls[-1], ("UnregisterClassW", tray.CLASS_NAME, INSTANCE))

    def test_another_platform_starts_nothing(self):
        # Mutation: the platform not asked. Red: a thread starts and loads user32.
        self.addCleanup(setattr, tray, "_windows", tray._windows)
        tray._windows = lambda: False
        icon = tray.Tray(URL, on_open=print, on_pause=print, on_resume=print, on_quit=print, paused=lambda: False,
                         words=lambda: page.WORDS["en"], icon_path=ICON_PATH)
        icon.start()
        icon.close()
        self.assertEqual(tray_threads(), [])
        self.assertEqual(self.err.getvalue(), " [tray] no icon by the clock: this platform is not Windows\n")


if __name__ == "__main__":
    unittest.main()
