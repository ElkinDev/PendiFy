"""PumpTest: the menu bar's icon pumps AppKit's events on the main thread between two ticks of the main loop, on the
fake Objective-C runtime of test_tray_macos; PlatformTest: which icon each platform takes."""
import threading
import time
import unittest

import support
from test_tray_macos import ICON_PATH, REMOVED, URL, FakeObjC, MenuBarCase

entry = support.module("__main__")
page = support.module("page")
tray = support.module("tray")
tray_macos = support.module("tray_macos")
ANY_EVENT = 0xFFFFFFFFFFFFFFFF


class PumpTest(MenuBarCase):
    def test_quit_takes_the_item_out_of_the_menu_bar_at_the_pumps_next_turn(self):
        # Mutation: the item left until close(). Red: it stays for the seconds the program takes to stop.
        stop = threading.Event()
        icon = self.make(on_quit=stop.set)
        icon.start()
        self.opened_menu()
        quit_item = self.tagged(4)
        self.fake.events = ["click"]
        self.fake.on_send["click"] = lambda: self.fake.handlers["choose"](quit_item)
        start = len(self.fake.calls)
        self.assertTrue(icon.wait(stop, 1.0))
        self.assertEqual(self.fake.calls[start + 2:], [("sendEvent:", "app", "click"), ("tag", quit_item)] + REMOVED)
        icon.close()  # nothing is left to remove
        self.assertEqual(self.fake.calls[-len(REMOVED):], REMOVED)
        self.assertEqual(self.fake.depth, 0)


    def test_the_pump_waits_out_its_seconds_in_slices_and_delivers_each_event(self):
        # Mutation: one wait for the whole tick. Red: a stop set meanwhile waits the full second.
        icon = self.make()
        icon.start()
        self.fake.events = ["key", "mouse"]
        start = len(self.fake.calls)
        self.assertFalse(icon.wait(threading.Event(), 0.25))
        pumped = self.fake.calls[start:]
        asks = [call for call in pumped if call[0] == "nextEventMatchingMask:untilDate:inMode:dequeue:"]
        self.assertEqual([call[2:] for call in asks], [(ANY_EVENT, call[3], "default mode", True) for call in asks])
        dates = [call[2] for call in pumped if call[0] == "dateWithTimeIntervalSinceNow:"]
        self.assertEqual(len(dates), 5)  # two events at once, then 0.1, 0.1 and what is left
        for date, expected in zip(dates, (0.1, 0.1, 0.1, 0.1, 0.05)):
            self.assertAlmostEqual(date, expected)
        self.assertEqual([call[2] for call in pumped if call[0] == "sendEvent:"], ["key", "mouse"])
        self.assertAlmostEqual(self.clock.now, 0.25)
        self.assertEqual(self.fake.depth, 0)


    def test_the_pump_ends_once_the_stop_is_set(self):
        # Mutation: the stop read only at the deadline. Red: the wait goes on after a quit set it.
        icon = self.make()
        icon.start()
        stop = threading.Event()
        self.fake.events, self.fake.on_send["click"] = ["click"], stop.set
        start = len(self.fake.calls)
        self.assertTrue(icon.wait(stop, 1.0))
        self.assertEqual([call[0] for call in self.fake.calls[start:]],
                         ["dateWithTimeIntervalSinceNow:", "nextEventMatchingMask:untilDate:inMode:dequeue:",
                          "sendEvent:"])
        start = len(self.fake.calls)
        self.assertTrue(icon.wait(stop, 1.0))  # set already: no message at all
        self.assertEqual((self.fake.calls[start:], self.clock.now), ([], 0.0))


    def test_without_an_item_the_wait_is_the_stops_own(self):
        # Mutation: the pump run with no application. Red: a message to nil, or no wait at all.
        stop = threading.Event()
        icon = self.make()
        self.assertFalse(icon.wait(stop, 0.01))
        stop.set()
        self.assertTrue(icon.wait(stop, 5.0))
        self.assertEqual(self.fake.calls, [])


    def test_a_pump_that_breaks_falls_back_to_the_stops_wait(self):
        # Mutation: the failure raised out of wait. Red: the main loop ends and the program with it.
        fake = FakeObjC(self.clock, broken="nextEventMatchingMask:untilDate:inMode:dequeue:")
        icon = self.make(fake)
        icon.start()
        stop = threading.Event()
        self.assertFalse(icon.wait(stop, 0.01))
        self.assertEqual(self.err.getvalue(), " [tray] the icon broke: OSError\n")
        asked = len(fake.calls)
        self.assertFalse(icon.wait(stop, 0.01))  # the stop's own wait from now on: AppKit is not asked again
        stop.set()
        self.assertTrue(icon.wait(stop, 5.0))
        self.assertEqual((len(fake.calls), fake.depth, self.err.getvalue().count("broke")), (asked, 0, 1))



class PlatformTest(unittest.TestCase):
    def test_macos_takes_the_menu_bar_and_every_other_platform_the_clocks_icon(self):
        # Mutation: the choice by os.name. Red: macOS is "posix" as Linux is, and gets the icon by the clock.
        self.assertIs(entry._icon_kind("darwin"), tray_macos.MenuBarIcon)
        self.assertIs(entry._icon_kind("win32"), tray.Tray)
        self.assertIs(entry._icon_kind("linux"), tray.Tray)

    def test_the_icon_by_the_clock_waits_as_the_stop_does(self):
        # Mutation: Tray.wait pumps or sleeps. Red: Windows' main loop ends late, or never on a stop.
        icon = tray.Tray(URL, on_open=print, on_pause=print, on_resume=print, on_quit=print, paused=lambda: False,
                         words=lambda: page.WORDS["en"], icon_path=ICON_PATH)
        stop, began = threading.Event(), time.monotonic()
        self.assertFalse(icon.wait(stop, 0.05))
        self.assertGreaterEqual(time.monotonic() - began, 0.05)  # its seconds waited out
        setting = threading.Timer(0.05, stop.set)
        setting.start()
        self.addCleanup(setting.cancel)
        began = time.monotonic()
        self.assertTrue(icon.wait(stop, 30.0))
        self.assertLess(time.monotonic() - began, 10.0)  # ended by the stop, long before its seconds



if __name__ == "__main__":
    unittest.main()
