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
        self.assertEqual((command[0], command[-2]), (sys.executable, "-c"))
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
        self.restarts, self.waits, self.out = [], [], io.StringIO()
        self.addCleanup(self.join_pollers)

    def join_pollers(self):
        for thread in pollers():
            thread.join(JOIN_SECONDS)

    def restart(self):
        self.restarts.append(threading.get_ident())
        return True

    def no_wait(self, seconds):
        self.waits.append(seconds)
        return False

    def switch(self, granted=False, on=True, answers=(), restart=None, wait=None, before=None):
        self.store, self.child = Store(on), Child(*answers, before=before)
        return autoclick.Switch(self.store, prompt=lambda: granted, granted=lambda: granted, run=self.child,
                                restart=restart or self.restart, wait=wait or self.no_wait)

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
        # the restart. Red: the thread never ends. Mutation: the line left out. Red: one line only.
        switch = self.switch(answers=("untrusted\n", "untrusted\n", "trusted\n"))
        self.assertIs(self.ask(switch), False)
        self.assertTrue(switch.thread.daemon)  # a stop of the program never waits for it
        self.assertEqual(len(self.child.calls), 3)
        self.assertEqual(self.waits, [autoclick.PERMISSION_POLL_SECONDS] * 3)
        self.assertEqual(self.restarts, [switch.thread.ident])
        self.assertEqual(self.out.getvalue().splitlines(),
                         [autoclick.PERMISSION_LINES[False], autoclick.GRANTED_LINE])

    def test_the_switch_off_starts_no_poller_and_a_poller_ends_once_it_is_turned_off(self):
        # Mutation: the poller started with the switch off. Red: a thread. Mutation: the switch not read at each round.
        # Red: a child asks, and the restart follows.
        off = self.switch(on=False, answers=("trusted\n",))
        self.assertIs(self.ask(off), False)
        self.assertIsNone(off.thread)

        def turned_off(seconds):
            switch.disable()  # the page's switch turned off while the poller waits
            return self.no_wait(seconds)

        switch = self.switch(answers=("trusted\n",), wait=turned_off)
        self.ask(switch)
        self.assertEqual((self.store.on, self.child.calls, self.restarts), (False, [], []))

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
        # Mutation: an ask after the close starts a poller. Red: one more thread.
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
