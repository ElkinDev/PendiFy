"""MenuBarTest: the icon in the menu bar on a fake Objective-C runtime that records every message and answers from the
objects it made, so no case reaches AppKit and the suite runs the same on every platform. The AppKit numbers are
written here as literals, so a wrong constant in the module is red."""
import contextlib
import ctypes
import io
import threading
import unittest

import support

page = support.module("page")
tray = support.module("tray")
tray_macos = support.module("tray_macos")

ICON_PATH = support.package_dir() / "pendify.ico"
URL = "http://127.0.0.1:50000/"
ACCESSORY, VARIABLE_LENGTH = 1, -1.0
OPENED = [("sharedApplication", "class NSApplication"), ("setActivationPolicy:", "app", ACCESSORY),
          ("finishLaunching", "app"), ("systemStatusBar", "class NSStatusBar"),
          ("statusItemWithLength:", "bar", VARIABLE_LENGTH), ("retain", "made item"), ("button", "item"),
          ("alloc", "class NSImage"), ("initWithContentsOfFile:", "new NSImage", ("string", str(ICON_PATH))),
          ("setSize:", "image", (18.0, 18.0)), ("setImage:", "button", "image"), ("release", "image"),
          ("setToolTip:", "button", ("string", "PendiFy")), ("alloc", "class NSMenu"),
          ("initWithTitle:", "new NSMenu", ("string", "")), ("setAutoenablesItems:", "menu", False),
          ("setDelegate:", "menu", "target"), ("setMenu:", "item", "menu")]
REMOVED = [("removeStatusItem:", "bar", "item"), ("release", "item"), ("release", "menu"), ("forget", "target"),
           ("release", "target")]


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class FakeObjC:
    """The facade's runtime as the icon uses it. Every message is kept in `calls` as (selector, receiver, *arguments),
    a structure as the tuple of its fields; `nil` names the selectors answered nil and `broken` the one that raises.
    nextEventMatchingMask: hands out `events` in order, and with none left lets `clock` run the date it was given;
    sendEvent: runs what `on_send` holds for that event, as AppKit runs a click's action inside it."""

    default_mode = "default mode"

    def __init__(self, clock=None, events=(), nil=(), broken=None):
        self.calls, self.tags, self.events, self.on_send = [], {}, list(events), {}
        self.clock, self.nil, self.broken = clock, set(nil), broken
        self.handlers, self.depth, self.made = None, 0, 0

    def cls(self, name):
        return "class " + name

    def sel(self, name):
        return "sel " + name

    def string(self, text):
        return ("string", text)

    def push(self):
        self.depth += 1
        return self.depth

    def pop(self, pool):
        assert pool == self.depth, (pool, self.depth)
        self.depth -= 1

    def target(self, choose, update):
        self.handlers = {"choose": choose, "update": update}
        return "target"

    def forget(self, target):
        self.calls.append(("forget", target))

    def send(self, receiver, selector, *typed, res=ctypes.c_void_p):
        values = tuple(tuple(getattr(value, name) for name, _ in value._fields_)
                       if isinstance(value, ctypes.Structure) else value for _, value in typed)
        self.calls.append((selector, receiver, *values))
        if selector == self.broken:
            raise OSError("broken")
        if selector in self.nil:
            return None
        if selector == "setTag:":
            self.tags[receiver] = values[0]
        if selector == "initWithTitle:action:keyEquivalent:":
            self.made += 1
            return f"entry {self.made}"
        if selector == "nextEventMatchingMask:untilDate:inMode:dequeue:":
            if self.events:
                return self.events.pop(0)
            self.clock.now += values[1][1]
            return None
        if selector == "sendEvent:":
            self.on_send.get(values[0], lambda: None)()
            return None
        return {"sharedApplication": "app", "systemStatusBar": "bar", "statusItemWithLength:": "made item",
                "retain": "item", "button": "button", "initWithContentsOfFile:": "image", "initWithTitle:": "menu",
                "separatorItem": "separator", "setActivationPolicy:": True, "tag": self.tags.get(receiver),
                "alloc": receiver.replace("class ", "new "), "autorelease": receiver,
                "dateWithTimeIntervalSinceNow:": ("date", values[0] if values else None)}.get(selector)


class MenuBarCase(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.fake = FakeObjC(self.clock)
        self.now = {"paused": False, "words": page.WORDS["es"], "ready": None}
        self.heard = []
        self.err = io.StringIO()
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(contextlib.redirect_stderr(self.err))

    def make(self, fake=None, ready=False, **callbacks):
        def heard(name):
            return callbacks.get(name, lambda: self.heard.append(name))

        more = {"update_ready": lambda: self.now["ready"], "on_restart": heard("on_restart")} if ready else {}
        return tray_macos.MenuBarIcon(URL, on_open=heard("on_open"), on_pause=heard("on_pause"),
                                      on_resume=heard("on_resume"), on_quit=heard("on_quit"),
                                      paused=lambda: self.now["paused"], words=lambda: self.now["words"],
                                      icon_path=ICON_PATH, objc=self.fake if fake is None else fake,
                                      clock=self.clock, **more)

    def opened_menu(self):
        """The menu as AppKit opens it, through its delegate's menuNeedsUpdate:, as [(tag, title) or None]."""
        start, base = len(self.fake.calls), self.fake.made
        self.fake.handlers["update"]("menu")
        calls = self.fake.calls[start:]
        self.assertEqual(calls[0], ("removeAllItems", "menu"))
        built = {f"entry {base + number}": call for number, call in
                 enumerate((call for call in calls if call[0] == "initWithTitle:action:keyEquivalent:"), 1)}
        items = []
        for added in (call[2] for call in calls if call[0] == "addItem:"):
            if added == "separator":
                items.append(None)
                continue
            self.assertEqual((built[added][1], *built[added][3:]), ("new NSMenuItem", "sel choose:", ("string", "")))
            self.assertIn(("setTarget:", added, "target"), calls)
            self.assertIn(("autorelease", added), calls)
            items.append((self.fake.tags[added], built[added][2][1]))
        return items

    def tagged(self, tag):
        """The item of the last menu opened whose tag is `tag`."""
        return [item for item, value in self.fake.tags.items() if value == tag][-1]


class MenuBarTest(MenuBarCase):
    def test_start_puts_the_item_in_the_menu_bar_with_no_dock_icon(self):
        # Mutation: the activation policy left Regular (0). Red: a Dock icon. Mutation: the item not retained. Red: an
        # autoreleased item leaves the menu bar at once. Mutation: no delegate. Red: the menu is never filled.
        self.make().start()
        self.assertEqual(self.fake.calls, OPENED)
        self.assertEqual((self.fake.depth, self.err.getvalue()), (0, ""))

    def test_the_menu_holds_the_items_of_the_icon_by_the_clock_in_the_language_of_the_moment(self):
        # Mutation: the words read once at start. Red: the second opening is still in Spanish.
        icon = self.make()
        icon.start()
        es, en = page.WORDS["es"], page.WORDS["en"]
        self.assertEqual(self.opened_menu(), [(1, es["tray_open"]), (2, es["pause"]), None, (4, es["quit"])])
        self.now.update(words=en, paused=True)
        self.assertEqual(self.opened_menu(), [(1, en["tray_open"]), (3, en["resume"]), None, (4, en["quit"])])
        self.assertEqual([es["tray_open"], es["pause"], es["quit"]], ["Abrir la página", "Pausar avisos", "Salir"])

    def test_each_command_calls_its_callback(self):
        # Mutation: pause and resume swapped. Red: the wrong callback is heard.
        icon = self.make()
        icon.start()
        for paused, tag, name in ((False, 1, "on_open"), (False, 2, "on_pause"), (True, 3, "on_resume")):
            self.now["paused"] = paused
            self.opened_menu()
            chosen = self.tagged(tag)
            self.fake.handlers["choose"](chosen)
            self.assertEqual(self.heard[-1], name)
        self.assertEqual(self.heard, ["on_open", "on_pause", "on_resume"])

    def test_a_ready_update_adds_the_restart_item_before_salir_and_its_callback_restarts(self):
        # Mutation: the item added without on_restart. Red: no item, or one that calls nothing.
        icon = self.make(ready=True)
        icon.start()
        self.assertEqual(self.opened_menu()[3:], [(4, "Salir")])  # nothing is ready yet
        self.now["ready"] = "0.2.0"
        self.assertEqual(self.opened_menu()[3:], [(5, "Reiniciar para actualizar a 0.2.0"), (4, "Salir")])
        self.fake.events, stop = ["click"], threading.Event()
        restart = self.tagged(5)
        self.fake.on_send["click"] = lambda: self.fake.handlers["choose"](restart)
        self.assertFalse(icon.wait(stop, 0.25))
        self.assertEqual(self.heard, ["on_restart"])
        self.assertEqual([call for call in self.fake.calls if call[0] in ("removeStatusItem:", "release", "forget")
                          and call[1] != "image"], REMOVED)

    def test_no_restart_item_without_the_restart_itself(self):
        # Mutation: the item added on a ready version alone. Red: an item whose choice calls nothing.
        icon = tray_macos.MenuBarIcon(URL, on_open=print, on_pause=print, on_resume=print, on_quit=print,
                                      paused=lambda: False, words=lambda: page.WORDS["es"], icon_path=ICON_PATH,
                                      objc=self.fake, update_ready=lambda: "0.2.0")
        icon.start()
        self.assertEqual([item and item[0] for item in self.opened_menu()], [1, 2, None, 4])

    def test_close_takes_the_item_out_once(self):
        # Mutation: the item released and not removed. Red: a stale item until the process ends.
        icon = self.make()
        icon.close()  # before start: nothing to remove
        self.assertEqual(self.fake.calls, [])
        icon.start()
        icon.close()
        icon.close()
        self.assertEqual(self.fake.calls[len(OPENED):], REMOVED)
        self.assertEqual(self.fake.depth, 0)

    def test_off_the_main_thread_start_shows_nothing(self):
        # Mutation: the thread not asked. Red: AppKit is messaged off the main thread, which it never allows.
        icon = self.make()
        starting = threading.Thread(target=icon.start)
        starting.start()
        starting.join(5)
        icon.close()
        self.assertEqual(self.fake.calls, [])
        self.assertEqual(self.err.getvalue(), " [tray] no icon in the menu bar: it starts on the main thread only\n")

    def test_a_missing_picture_shows_the_name_instead(self):
        # Mutation: the title not set. Red: an item with no picture and no text, which takes no room to be seen.
        fake = FakeObjC(self.clock, nil={"initWithContentsOfFile:"})
        self.make(fake).start()
        shown = [call for call in fake.calls if call[0] in ("setImage:", "setTitle:", "setSize:")]
        self.assertEqual(shown, [("setTitle:", "button", ("string", "PendiFy"))])
        self.assertEqual(self.err.getvalue(), " [tray] initWithContentsOfFile: answered nil\n")
        self.assertEqual(fake.calls[-1], ("setMenu:", "item", "menu"))  # the menu all the same

    def test_no_application_means_no_item_and_a_line(self):
        fake = FakeObjC(self.clock, nil={"sharedApplication"})
        icon = self.make(fake)
        icon.start()
        self.assertEqual(fake.calls, [("sharedApplication", "class NSApplication")])
        self.assertEqual(self.err.getvalue(), " [tray] sharedApplication answered nil\n")
        self.assertFalse(icon.wait(threading.Event(), 0.01))
        icon.close()
        self.assertEqual(len(fake.calls), 1)

    def test_a_callback_that_raises_is_said_and_never_leaves_the_handler(self):
        # Mutation: no guard in the handler. Red: the exception reaches ctypes, which prints a traceback.
        icon = self.make(on_open=lambda: [][0])
        icon.start()
        self.opened_menu()
        self.fake.handlers["choose"](self.tagged(1))
        self.assertEqual(self.err.getvalue(), " [tray] the icon broke: IndexError\n")


if __name__ == "__main__":
    unittest.main()
