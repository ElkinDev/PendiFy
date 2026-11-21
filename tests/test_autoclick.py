"""The Accessibility permission read again by a new process, and the copy's restart once it is granted (macOS).

A process keeps the answer macOS gave it at its start: AXIsProcessTrusted() in it never asks tccd again, so a grant
made while the copy runs is read by a child, and the copy then asks for its restart. No test starts a child or asks
macOS: the child is a fake runner, and the wait between two rounds a fake that takes no time.
"""
import contextlib
import io
import subprocess
import sys
import threading
import unittest
from unittest import mock

import support

autoclick = support.module("autoclick")

JOIN_SECONDS = 5  # the bound on a poller's end in a test, never a wait the poller itself makes
POLLER = "autoaccept-permission"


class Child:
    """The runner in place of subprocess.run: each start of the fresh check answers the next of `answers`, a line
    the child prints with exit `code`, or an exception it raises (a child that cannot start, or that does not answer
    in time); "untrusted" once they run out. `before` runs at each start. The commands and their keywords are kept."""

    def __init__(self, *answers, code=0, before=None):
        self.answers, self.code, self.before, self.calls = list(answers), code, before, []

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        if self.before is not None:
            self.before()
        answer = self.answers.pop(0) if self.answers else "untrusted\n"
        if isinstance(answer, BaseException):
            raise answer
        return subprocess.CompletedProcess(command, self.code, stdout=answer, stderr="")


class Store:
    """config.json's autoaccept held in memory, never a file."""

    def __init__(self, on):
        self.on = on

    def read_autoaccept(self):
        return self.on

    def set_autoaccept(self, on):
        self.on = on


def pollers():
    return [thread for thread in threading.enumerate() if thread.name == POLLER]


class OnMac(unittest.TestCase):
    """Every case runs as on macOS, on any platform the suite runs on: the check is reached, its child faked."""

    def setUp(self):
        on_mac = mock.patch.object(autoclick, "available", return_value=True)
        on_mac.start()
        self.addCleanup(on_mac.stop)


class FreshCheckTest(OnMac):
    """trusted_now(): a new process asks macOS, and only its "trusted" is a yes."""

    def test_the_child_saying_trusted_is_a_yes_asked_by_this_interpreter_through_the_public_call(self):
        # Mutation: the answer read in this process. Red: no child started. Mutation: no timeout. Red: none passed.
        run = Child("trusted\n")
        self.assertIs(autoclick.trusted_now(run), True)
        [(command, kwargs)] = run.calls
        self.assertEqual(command[:3], [sys.executable, "-I", "-c"])  # isolated: no PYTHON* variable or user site
        self.assertIn("AXIsProcessTrusted()", command[-1])
        compile(command[-1], "the fresh check", "exec")  # the child's program is Python that compiles
        self.assertEqual(kwargs["timeout"], autoclick.TIMEOUT)

    def test_untrusted_or_a_child_that_fails_is_a_no(self):
        # Mutation: any output read as a yes. Red: "untrusted" answers True. Mutation: the exit code ignored. Red: a
        # failing child's "trusted" answers True.
        for run in (Child("untrusted\n"), Child("trusted\n", code=1), Child("")):
            with self.subTest(answer=run.answers, code=run.code):
                self.assertIs(autoclick.trusted_now(run), False)

    def test_a_child_that_cannot_start_or_does_not_answer_in_time_is_a_no(self):
        # Mutation: the runner's exceptions let through. Red: the check raises.
        for failure in (FileNotFoundError(2, "no such file"), subprocess.TimeoutExpired("python", autoclick.TIMEOUT)):
            with self.subTest(failure=type(failure).__name__):
                self.assertIs(autoclick.trusted_now(Child(failure)), False)

    def test_off_macos_no_child_starts_and_the_answer_is_no(self):
        # Mutation: the platform not checked. Red: a child starts and its yes is taken.
        run = Child("trusted\n")
        with mock.patch.object(autoclick, "available", return_value=False):
            self.assertIs(autoclick.trusted_now(run), False)
        self.assertEqual(run.calls, [])


class PermissionPollerTest(OnMac):
    """While the switch is on and the permission was not granted at the ask, a child asks macOS every
    PERMISSION_POLL_SECONDS; once it says trusted, the copy's restart is asked once and the poller ends."""

    def setUp(self):
        super().setUp()
        self.restarts, self.waits, self.out, self.now = [], [], io.StringIO(), 0.0
        self.addCleanup(self.join_pollers)

    def join_pollers(self):
        for thread in pollers():
            thread.join(JOIN_SECONDS)

    def restart(self):
        self.restarts.append(threading.get_ident())
        return True

    def no_wait(self, seconds):
        self.waits.append(seconds)
        self.now += seconds  # the poller's clock moves by the wait, which takes no time
        return False

    def switch(self, granted=False, on=True, answers=(), restart=None, wait=None, before=None):
        self.store, self.child = Store(on), Child(*answers, before=before)
        return autoclick.Switch(self.store, prompt=lambda: granted, granted=lambda: granted, run=self.child,
                                restart=restart or self.restart, wait=wait or self.no_wait, clock=lambda: self.now)

    def ask(self, switch):
        with contextlib.redirect_stdout(self.out):
            answer = switch.ask()
            if switch.thread is not None:
                switch.thread.join(JOIN_SECONDS)
                self.assertFalse(switch.thread.is_alive())
        return answer

    def test_granted_at_the_ask_starts_no_poller(self):
        # Mutation: the poller started whatever the answer. Red: a thread, and children asked.
        switch = self.switch(granted=True, answers=("trusted\n",))
        self.assertIs(self.ask(switch), True)
        self.assertIsNone(switch.thread)
        self.assertEqual((self.child.calls, self.restarts), ([], []))
        self.assertEqual(self.out.getvalue(), autoclick.PERMISSION_LINES[True] + "\n")

    def test_polls_until_the_child_says_trusted_then_asks_the_restart_once_says_it_and_ends(self):
        # Mutation: the restart asked at the first round. Red: one child, not three. Mutation: the loop goes on after
        # the restart. Red: the thread never ends. Mutation: the line left out. Red: one line only. Mutation: the
        # poller counted ended once the restart is asked. Red: a second poller and a second restart.
        switch = self.switch(answers=("untrusted\n", "untrusted\n", "trusted\n"))
        self.assertIs(self.ask(switch), False)
        self.assertTrue(switch.thread.daemon)  # a stop of the program never waits for it
        self.assertEqual(len(self.child.calls), 3)
        self.assertEqual(self.waits, [autoclick.PERMISSION_POLL_SECONDS] * 3)
        self.assertEqual(self.restarts, [switch.thread.ident])
        self.assertEqual(self.out.getvalue().splitlines(),
                         [autoclick.PERMISSION_LINES[False], autoclick.GRANTED_LINE])
        ended = switch.thread
        self.ask(switch)  # an ask in a copy that stops for its restart: no poller starts again
        self.assertIs(switch.thread, ended)
        self.assertEqual(len(self.restarts), 1)

    def test_every_3_seconds_for_10_minutes_after_the_ask_then_every_30(self):
        # Mutation: no slow phase. Red: 203 waits of 3 s. Mutation: the slow phase from the start. Red: 30 s at first.
        self.assertEqual((autoclick.PERMISSION_POLL_SECONDS, autoclick.PERMISSION_FAST_SECONDS,
                          autoclick.PERMISSION_SLOW_POLL_SECONDS), (3, 600, 30))
        self.now = 1000.0  # the ask is at any time of the clock
        switch = self.switch(answers=("untrusted\n",) * 202 + ("trusted\n",))
        self.ask(switch)
        self.assertEqual(self.waits, [3] * 200 + [30] * 3)
        self.assertEqual(len(self.restarts), 1)

    def test_an_ask_while_the_poll_is_slow_brings_back_the_fast_one(self):
        # Mutation: the fast phase timed from the poller's start only. Red: 30 s after the second ask.
        def wait(seconds):
            if seconds == 30 and self.waits.count(30) == 1:
                switch.ask()  # the page's switch turned on again while the poller is slow
            return self.no_wait(seconds)

        switch = self.switch(answers=("untrusted\n",) * 202 + ("trusted\n",), wait=wait)
        self.ask(switch)
        self.assertEqual(self.waits, [3] * 200 + [30, 30] + [3])

    def test_the_switch_off_starts_no_poller_and_a_poller_ends_once_it_is_turned_off_and_starts_again_on(self):
        # Mutation: the poller started with the switch off. Red: a thread. Mutation: the switch not read at each round.
        # Red: a child asks, and the restart follows. Mutation: an ended poller still counted. Red: none starts again.
        off = self.switch(on=False, answers=("trusted\n",))
        self.assertIs(self.ask(off), False)
        self.assertIsNone(off.thread)

        def turned_off(seconds):
            if not self.waits:
                switch.disable()  # the page's switch turned off while the first poller waits
            return self.no_wait(seconds)

        switch = self.switch(answers=("trusted\n",), wait=turned_off)
        self.ask(switch)
        first = switch.thread
        self.assertEqual((self.store.on, self.child.calls, self.restarts), (False, [], []))
        with contextlib.redirect_stdout(self.out):
            switch.enable()  # turned on again: a new poller, which sees the grant
            switch.thread.join(JOIN_SECONDS)
        self.assertIsNot(switch.thread, first)
        self.assertEqual((len(self.child.calls), self.restarts), (1, [switch.thread.ident]))

    def test_the_switch_turned_on_from_the_page_asks_and_polls(self):
        # Mutation: enable() asks without the poller. Red: no restart once the child says trusted.
        prompts = []
        self.store, self.child = Store(False), Child("untrusted\n", "trusted\n")
        switch = autoclick.Switch(self.store, prompt=lambda: prompts.append(1), granted=lambda: False, run=self.child,
                                  restart=self.restart, wait=self.no_wait)
        with contextlib.redirect_stdout(self.out):
            self.assertIs(switch.enable(), True)
            switch.thread.join(JOIN_SECONDS)
        self.assertEqual((self.store.on, prompts, len(self.child.calls)), (True, [1], 2))
        self.assertEqual(self.restarts, [switch.thread.ident])

    def test_never_two_pollers(self):
        # Mutation: the running poller not looked for. Red: three threads, and the restart asked by more than one.
        gate = threading.Event()
        self.addCleanup(gate.set)  # a red never leaves a poller waiting

        def held(seconds):
            gate.wait(JOIN_SECONDS)
            return self.no_wait(seconds)

        switch = self.switch(answers=("trusted\n", "trusted\n", "trusted\n"), wait=held)
        with contextlib.redirect_stdout(self.out):
            switch.ask()
            switch.ask()  # a second ask while the first poller waits
            switch.enable()  # and the page's switch turned on again
            self.assertEqual(len(pollers()), 1)
            gate.set()
            self.join_pollers()
        self.assertEqual((len(self.restarts), len(self.child.calls)), (1, 1))

    def test_the_close_ends_the_wait_and_nothing_polls_after_it(self):
        # Mutation: the wait not the close's own. Red: the poller sleeps out its interval and asks a child.
        # Mutation: an ask after the close starts a poller. Red: a thread on the switch closed before any ask.
        self.store, self.child = Store(True), Child("trusted\n")
        switch = autoclick.Switch(self.store, prompt=lambda: False, granted=lambda: False, run=self.child,
                                  restart=self.restart)
        with contextlib.redirect_stdout(self.out):
            switch.ask()
            switch.close()
            switch.thread.join(autoclick.PERMISSION_POLL_SECONDS / 3)  # woken by the close, not at the interval
            self.assertFalse(switch.thread.is_alive())
            switch.ask()
        self.assertEqual((pollers(), self.child.calls, self.restarts), ([], [], []))
        closed = self.switch(answers=("trusted\n",))
        closed.close()  # a copy that stops before any ask: the page's switch turned on as it closes
        self.ask(closed)
        self.assertIsNone(closed.thread)

    def test_a_grant_seen_as_the_copy_closes_asks_no_restart(self):
        # Mutation: the close not read before the restart. Red: a restart asked by a copy that stops.
        switch = self.switch(answers=("trusted\n",), before=lambda: switch.close())
        self.ask(switch)
        self.assertEqual((len(self.child.calls), self.restarts), (1, []))
        self.assertNotIn(autoclick.GRANTED_LINE, self.out.getvalue())

    def test_a_restart_not_taken_yet_is_asked_again_at_the_next_round_and_said_once(self):
        # Mutation: the poller ends whatever the restart answers. Red: one request, never taken.
        taken = iter((False, True))

        def restart():
            self.restarts.append(threading.get_ident())
            return next(taken)

        switch = self.switch(answers=("trusted\n", "trusted\n"), restart=restart)
        self.ask(switch)
        self.assertEqual(len(self.restarts), 2)
        self.assertEqual(self.out.getvalue().splitlines().count(autoclick.GRANTED_LINE), 1)

    def test_no_restart_to_ask_for_starts_no_poller(self):
        # Mutation: a poller with nothing to call at the grant. Red: a thread.
        switch = autoclick.Switch(Store(True), prompt=lambda: False, granted=lambda: False, run=Child("trusted\n"))
        self.assertIs(self.ask(switch), False)
        self.assertIsNone(switch.thread)


if __name__ == "__main__":
    unittest.main()
