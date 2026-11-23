"""The poller against what happens around it: a read of the switch that fails, a switch turned off or a copy closed
while a child asks, asks that come together, a restart not taken yet, and what the page reads of the wait (macOS).

Every case runs as on macOS with the child, the wait and config.json faked (test_permission.PollerCase).
"""
import contextlib
import threading
import unittest

import support
from test_permission import JOIN_SECONDS, Child, PollerCase, Store, pollers

autoclick = support.module("autoclick")
permission = support.module("permission")


class PermissionRacesTest(PollerCase):
    """The grant's restart is asked only by a copy that still runs with the switch on, once, by one poller."""

    def test_a_read_of_the_switch_that_fails_neither_ends_the_poller_nor_keeps_it_from_starting(self):
        # Mutation: a failed read taken as off at each round. Red: the poller ends at its first round, no restart.
        # Mutation: taken as off at the start. Red: no poller. Mutation: enabled() answers the failed read. Red: None.
        failures = (OSError(5, "input/output error"), ValueError("not json"), OSError(13, "denied"), OSError(5, "io"))
        switch = self.switch(answers=("untrusted\n",) * 3 + ("trusted\n",), failures=failures)
        self.assertIs(self.ask(switch), False)
        self.assertEqual(self.store.reads, 6)  # the start's, four rounds' and the one after the grant; four failed
        self.assertEqual((len(self.child.calls), self.restarts), (4, [switch.poller.thread.ident]))
        self.assertEqual(self.out.getvalue().splitlines()[-1], permission.GRANTED_LINE)
        # The page reads a failed read as off, as before.
        page = autoclick.Switch(Store(True, failures=(OSError(5, "io"),)), prompt=lambda: False)
        self.assertEqual([page.enabled(), page.enabled()], [False, True])

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
            switch.poller.thread.join(permission.PERMISSION_POLL_SECONDS / 3)  # woken by the close, not at the interval
            self.assertFalse(switch.poller.thread.is_alive())
            switch.ask()
        self.assertEqual((pollers(), self.child.calls, self.restarts), ([], [], []))
        closed = self.switch(answers=("trusted\n",))
        closed.close()  # a copy that stops before any ask: the page's switch turned on as it closes
        self.ask(closed)
        self.assertIsNone(closed.poller.thread)

    def test_a_grant_seen_as_the_copy_closes_asks_no_restart(self):
        # Mutation: the close not read before the restart. Red: a restart asked by a copy that stops.
        switch = self.switch(answers=("trusted\n",), before=lambda: switch.close())
        self.ask(switch)
        self.assertEqual((len(self.child.calls), self.restarts), (1, []))
        self.assertNotIn(permission.GRANTED_LINE, self.out.getvalue())

    def test_the_switch_turned_off_while_the_child_asks_asks_no_restart_and_ends_the_poller(self):
        # Mutation: the switch not read again once the child says trusted. Red: a restart asked with the switch off.
        # Mutation: the poller left counted running at that off. Red: still awaited, and no new one would start.
        switch = self.switch(answers=("trusted\n",), before=lambda: switch.disable())
        self.ask(switch)
        self.assertEqual((len(self.child.calls), self.restarts, self.store.on), (1, [], False))
        self.assertNotIn(permission.GRANTED_LINE, self.out.getvalue())
        self.assertIs(switch.awaiting(), False)

    def test_a_restart_not_taken_yet_is_asked_again_at_the_next_round_and_said_once(self):
        # Mutation: the poller ends whatever the restart answers. Red: one request, never taken.
        taken = iter((False, True))

        def restart():
            self.restarts.append(threading.get_ident())
            return next(taken)

        switch = self.switch(answers=("trusted\n", "trusted\n"), restart=restart)
        self.ask(switch)
        self.assertEqual(len(self.restarts), 2)
        self.assertEqual(self.out.getvalue().splitlines().count(permission.GRANTED_LINE), 1)

    def test_no_restart_to_ask_for_starts_no_poller(self):
        # Mutation: a poller with nothing to call at the grant. Red: a thread.
        switch = autoclick.Switch(Store(True), prompt=lambda: False, granted=lambda: False, run=Child("trusted\n"))
        self.assertIs(self.ask(switch), False)
        self.assertIsNone(switch.poller.thread)
        self.assertIs(switch.awaiting(), False)  # a copy the grant restarted: its page says no restart follows

    def test_the_switch_awaits_the_grant_while_its_poller_runs_until_it_is_turned_off_or_closed(self):
        # Mutation: awaiting() answers True. Red: True before any ask. Mutation: the off not read. Red: True once the
        # poller ended at the off. Mutation: the close not read. Red: True once this copy stops.
        seen = []

        def wait(seconds):
            seen.append(switch.awaiting())  # at each round, while the poller runs
            if len(seen) == 2:
                switch.disable()
            return self.no_wait(seconds)

        switch = self.switch(wait=wait)
        self.assertIs(switch.awaiting(), False)  # no ask yet
        self.ask(switch)
        self.assertEqual(seen, [True, True])
        self.assertIs(switch.awaiting(), False)  # turned off: the poller ended
        # The grant seen and the restart asked: still awaited, as the restart follows, until this copy stops.
        granted = self.switch(answers=("trusted\n",))
        self.ask(granted)
        self.assertEqual(len(self.restarts), 1)
        self.assertIs(granted.awaiting(), True)
        granted.close()
        self.assertIs(granted.awaiting(), False)
        # Closed while the poller waits out its interval (the close's own wait).
        waiting = autoclick.Switch(Store(True), prompt=lambda: False, granted=lambda: False, run=Child(),
                                   restart=self.restart)
        with contextlib.redirect_stdout(self.out):
            waiting.ask()
        self.assertIs(waiting.awaiting(), True)
        waiting.close()
        self.assertIs(waiting.awaiting(), False)


if __name__ == "__main__":
    unittest.main()
