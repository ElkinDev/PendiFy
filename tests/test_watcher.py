"""WatcherTest: the loop of S:728-800 as a class, driven step by step with no sleep.

The fake client answers on 127.0.0.1 over plain http, reached through the injected address builder. The
three fire sites: the arrival of InProgress after a read of another phase (the loading screen, match_started
with a beep and a ping, once per game), S:776 (ReadyCheck with the accept off) and S:790 (ReadyCheck, the
accept taken).
"""
import ast
import base64
import contextlib
import io
import re
import ssl
import threading
import unittest
from pathlib import Path

import support
from support import LINK_ID, SECRET, FakeClock, error

alert = support.module("alert")
client = support.module("client")
config = support.module("config")
pairing = support.module("pairing")
watcher = support.module("watcher")
worker = support.module("worker")

TOKEN = "fake-Token_0123"  # a test vector, never a client's
PHASE, ACCEPT = support.CLIENT_PHASE_PATH, support.CLIENT_ACCEPT_PATH
QUEUE_FOUND, MATCH_STARTED = "lol_queue_found", "lol_match_started"
WALL = 1_790_000_000.0
PING_WALL = 1_790_000_030.0  # the alerter's wall clock, apart from the watcher's so the snapshot shows whose
GAME_WORDS = re.compile(r"\b(league|legends|riot|lol)\b", re.IGNORECASE)
UNVERIFIED = re.compile(r"CERT_NONE|_create_unverified_context|check_hostname|verify_mode|_create_default_https")


class Credentials:
    """A credentials reader that answers the port the test points it at, and counts its reads."""

    def __init__(self, port):
        self.port, self.reads = port, 0

    def read(self):
        self.reads += 1
        return None if self.port is None else client.Credentials(self.port, TOKEN)


class WatcherTest(unittest.TestCase):
    def setUp(self):
        tmp = support.temp_dir()
        self.addCleanup(tmp.cleanup)
        self.store = config.ConfigStore(Path(tmp.name))
        self.store.set_typed(LINK_ID, SECRET)
        self.clock = FakeClock()
        self.state = pairing.PairingState(self.store, lambda secret: worker.Refused(), clock=self.clock)
        self.beeps, self.pings, self.lines, self.sleeps = [], [], [], []
        self.fake = self.client_fake()

    def client_fake(self, **kwargs):
        fake = support.FakeClient(**kwargs)
        self.addCleanup(fake.close)
        return fake

    def ping(self, link_id, secret, kind):
        self.pings.append((link_id, secret, kind))
        return worker.Sent()

    def sleep(self, seconds):
        self.sleeps.append((seconds, self.fake.count("POST", ACCEPT)))
        self.clock.advance(seconds)

    def alerter(self, ping=None, **kwargs):
        kwargs.setdefault("start", lambda target: target())
        kwargs.setdefault("beep", lambda: self.beeps.append(self.clock()))
        kwargs.setdefault("wall", lambda: PING_WALL)
        return alert.Alerter(self.store, self.state, ping or self.ping, log=self.lines.append, **kwargs)

    def watcher(self, credentials=None, accept=True, alerter=None, delay=1.7, **kwargs):
        return watcher.Watcher(credentials or Credentials(self.fake.port), alerter or self.alerter(), accept=accept,
                               addresses=lambda port: f"http://127.0.0.1:{port}", clock=self.clock,
                               wall=lambda: WALL, sleep=kwargs.pop("sleep", self.sleep), delay=lambda: delay,
                               log=self.lines.append, **kwargs)

    def steps_until(self, subject, deadline, every=0.3):
        while self.clock() + every < deadline:
            self.clock.advance(every)
            subject.step()

    def read(self, subject, *phases):
        """One step per phase, 0.3 s apart, the fake client answering each phase in turn."""
        for phase in phases:
            self.fake.phase = phase
            self.clock.advance(0.3)
            subject.step()

    @staticmethod
    def shown(phase, alert=None, ping=None, client_state="connected"):
        """The snapshot a watcher answers: the phase as the client names it, the last alert at the watcher's
        wall clock and the last ping's result at the alerter's."""
        return {"client": client_state, "phase": phase, "alert": alert, "at": None if alert is None else WALL,
                "pingResult": ping, "pingAt": None if ping is None else PING_WALL}

    def test_the_arrival_of_in_progress_pings_the_match_start_once_and_beeps_once(self):
        # Mutation: the arrival alerting with no kind, as before. Red: a beep and no ping.
        # Mutation: the latch set after the alert. Red: an alert that broke is made again at the next read.
        for name in ("ALERT_PHASES", "CLOCK_READ_INTERVAL"):
            self.assertFalse(hasattr(watcher, name), name)
        for name in ("game_really_started", "LIVE_CLOCK_URL", "LIVE_CLOCK_PATH"):
            self.assertFalse(hasattr(client, name), name)
        subject = self.watcher()
        self.read(subject, "Lobby", "ChampSelect")
        self.assertEqual((self.beeps, self.pings), ([], []))
        self.assertEqual(subject.snapshot(), self.shown("ChampSelect"))
        self.read(subject, "InProgress")
        self.assertEqual((len(self.beeps), self.pings), (1, [(LINK_ID, SECRET, MATCH_STARTED)]))
        self.assertEqual(subject.snapshot(), self.shown("InProgress", "started", "sent"))
        self.read(subject, *["InProgress"] * 5)
        self.assertEqual((len(self.beeps), len(self.pings)), (1, 1))
        # The phase is the one call: no other address and no other route is read.
        self.assertEqual({(call["method"], call["path"]) for call in self.fake.requests}, {("GET", PHASE)})
        failures = [RuntimeError("the beep could not start")]

        def beep():
            if failures:
                raise failures.pop()
            self.beeps.append(self.clock())

        self.beeps.clear()
        self.pings.clear()
        breaking = self.watcher(alerter=self.alerter(beep=beep))
        self.read(breaking, "Lobby")
        self.fake.phase = "InProgress"
        with self.assertRaises(RuntimeError):
            breaking.step()
        self.read(breaking, "InProgress", "InProgress")
        self.assertEqual((self.beeps, self.pings, failures), ([], [], []))

    def test_a_client_already_in_progress_at_the_first_read_alerts_nothing(self):
        # Mutation: the first-read guard removed (S:755-757). Red: the first read of InProgress beeps and pings.
        subject = self.watcher()
        self.read(subject, "InProgress", "InProgress", "InProgress")
        self.assertEqual((self.beeps, self.pings), ([], []))
        self.assertEqual(subject.snapshot(), self.shown("InProgress"))

    def test_ready_check_with_the_accept_on_posts_once_after_the_delay_then_alerts_and_holds_15_s(self):
        # Mutation: the alert made before the accept's answer is read. Red: the refused accept beeps and pings.
        self.assertEqual(watcher.ACCEPT_DELAY, (1.0, 2.5))
        draws = [watcher.accept_delay() for _ in range(300)]
        self.assertTrue(all(1.0 <= draw <= 2.5 for draw in draws))
        self.assertGreater(len(set(draws)), 1)
        self.fake.phase = "ReadyCheck"
        subject = self.watcher(delay=1.7)
        subject.step()
        self.assertEqual(self.sleeps, [(1.7, 0)])  # the delay is slept before any accept is posted
        posts = self.fake.calls("POST", ACCEPT)
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0]["headers"]["authorization"],
                         "Basic " + base64.b64encode(f"riot:{TOKEN}".encode()).decode())
        self.assertEqual(posts[0]["body"], b"{}")
        self.assertEqual(len(self.beeps), 1)
        self.assertEqual(self.pings, [(LINK_ID, SECRET, QUEUE_FOUND)])
        held_from, reads = self.clock(), self.fake.count("GET", PHASE)
        self.steps_until(subject, held_from + 15.0)
        self.assertEqual((self.fake.count("POST", ACCEPT), self.fake.count("GET", PHASE)), (1, reads))
        self.clock.now = held_from + 15.0
        subject.step()
        self.assertEqual(self.fake.count("POST", ACCEPT), 2)
        refused = self.client_fake(phase="ReadyCheck", accept_status=500)
        self.beeps.clear()
        self.pings.clear()
        self.watcher(credentials=Credentials(refused.port)).step()
        self.assertEqual(refused.count("POST", ACCEPT), 1)
        self.assertEqual((self.beeps, self.pings), ([], []))

    def test_ready_check_with_the_accept_off_posts_nothing_alerts_once_and_holds_15_s(self):
        # Mutation: the accept posted in dry mode. Red: one POST on the fake client.
        self.fake.phase = "ReadyCheck"
        subject = self.watcher(accept=False)
        subject.step()
        self.assertEqual(self.fake.count("POST", ACCEPT), 0)
        self.assertEqual((len(self.beeps), self.pings, self.sleeps), (1, [(LINK_ID, SECRET, QUEUE_FOUND)], []))
        self.assertEqual(subject.snapshot(), self.shown("ReadyCheck", "queue", "sent"))
        held_from, reads = self.clock(), self.fake.count("GET", PHASE)
        self.steps_until(subject, held_from + 15.0)
        self.assertEqual((len(self.beeps), len(self.pings), self.fake.count("GET", PHASE)), (1, 1, reads))
        self.clock.now = held_from + 15.0
        subject.step()
        self.assertEqual((len(self.pings), self.fake.count("POST", ACCEPT)), (2, 0))

    def test_lobby_then_in_progress_again_pings_the_match_start_again(self):
        # Mutation: the latch never reset when the phase leaves InProgress. Red: the second game sends no ping.
        subject = self.watcher()
        self.read(subject, "InProgress", "PreEndOfGame", "WaitingForStats", "EndOfGame", "Lobby", "Matchmaking",
                  "InProgress", "InProgress", "EndOfGame", "Lobby", "InProgress", "InProgress")
        self.assertEqual([kind for *_, kind in self.pings], [MATCH_STARTED, MATCH_STARTED])
        self.assertEqual(len(self.beeps), 2)
        self.assertEqual(subject.snapshot(), self.shown("InProgress", "started", "sent"))

    def test_a_client_that_stops_answering_is_looked_for_again_and_a_fresh_one_is_followed(self):
        # Mutation: the credentials kept after a lost connection. Red: the fresh client on another port is never read.
        self.fake.phase = "Lobby"
        credentials = Credentials(self.fake.port)
        subject = self.watcher(credentials=credentials)
        self.assertEqual(subject.snapshot()["client"], "waiting")
        self.assertEqual(subject.step(), watcher.STEP_PAUSE)
        self.clock.advance(0.3)
        subject.step()
        self.assertEqual((credentials.reads, self.fake.count("GET", PHASE)), (1, 2))
        self.fake.dropping = True
        self.clock.advance(0.3)
        self.assertEqual(subject.step(), watcher.STEP_PAUSE)
        self.assertEqual((self.lines.count(watcher.LOST_LINE), subject.snapshot()["client"]), (1, "waiting"))
        credentials.port = None
        self.clock.advance(0.3)
        self.assertEqual(subject.step(), watcher.NO_CLIENT_PAUSE)
        self.assertEqual(credentials.reads, 2)
        fresh = self.client_fake(phase="Lobby")
        credentials.port = fresh.port
        self.clock.advance(3.0)
        self.assertEqual(subject.step(), watcher.STEP_PAUSE)
        self.assertEqual((credentials.reads, fresh.count("GET", PHASE)), (3, 1))
        self.assertEqual(subject.snapshot()["client"], "connected")
        self.assertEqual(self.lines.count(watcher.CONNECTED_LINE), 2)

    def test_a_fresh_client_already_in_progress_after_a_lost_one_alerts_nothing_until_lobby_then_in_progress(self):
        # Mutation: _forget keeps the last phase read. Red: the fresh client's first InProgress beeps and pings.
        credentials = Credentials(self.fake.port)
        subject = self.watcher(credentials=credentials)
        self.read(subject, "Lobby")
        self.fake.dropping = True
        self.clock.advance(0.3)
        subject.step()
        self.assertEqual(subject.snapshot(), self.shown(None, client_state="waiting"))
        self.fake = self.client_fake(phase="InProgress")
        credentials.port = self.fake.port
        self.read(subject, "InProgress", "InProgress")
        self.assertEqual((credentials.reads, self.lines.count(watcher.CONNECTED_LINE)), (2, 2))
        self.assertEqual((self.beeps, self.pings), ([], []))
        self.assertEqual(subject.snapshot(), self.shown("InProgress"))
        self.read(subject, "Lobby", "InProgress", "InProgress")
        self.assertEqual([kind for *_, kind in self.pings], [MATCH_STARTED])
        self.assertEqual(len(self.beeps), 1)
        self.assertEqual(subject.snapshot(), self.shown("InProgress", "started", "sent"))

    def test_run_pauses_3_s_while_there_is_no_client_and_0_3_s_between_reads(self):
        # Mutation: the no-client pause made 0.3 s (S:739). Red: the pauses read 0.3 where 3 is due.
        self.assertEqual((watcher.NO_CLIENT_PAUSE, watcher.STEP_PAUSE), (3.0, 0.3))
        credentials, stop, pauses = Credentials(None), threading.Event(), []

        def sleep(seconds):
            pauses.append(seconds)
            if len(pauses) == 2:
                credentials.port = self.fake.port
            if len(pauses) == 4:
                stop.set()

        self.watcher(credentials=credentials, sleep=sleep, stop=stop).run()
        self.assertEqual(pauses, [3.0, 3.0, 0.3, 0.3])
        self.assertEqual(self.lines, [watcher.CONNECTED_LINE])
        stopped = Credentials(self.fake.port)
        self.watcher(credentials=stopped, sleep=sleep, stop=stop).run()
        self.assertEqual(stopped.reads, 0)

    def test_an_alert_pings_only_with_a_link_id_off_the_watcher_s_thread_and_three_refusals_offer_a_relink(self):
        # Mutation: the ping made on the watcher's thread. Red: the step returns only after the held ping.
        self.store.forget()  # a new secret and no link id
        started = []
        unlinked = self.alerter(start=started.append)
        unlinked(QUEUE_FOUND)
        self.assertEqual((len(self.beeps), self.pings, started), (1, [], []))
        self.assertEqual(self.watcher(alerter=unlinked).snapshot(), self.shown(None, client_state="waiting"))
        self.store.set_typed(LINK_ID, SECRET)
        release, finished = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def slow(link_id, secret, kind):
            release.wait(2.0)
            finished.set()
            return worker.Sent()

        held = alert.Alerter(self.store, self.state, slow, beep=lambda: None, log=self.lines.append)
        self.fake.phase = "ReadyCheck"
        subject = self.watcher(accept=False, alerter=held)
        subject.step()
        self.assertFalse(finished.is_set())
        self.clock.advance(15.0)
        subject.step()
        self.assertEqual(self.fake.count("GET", PHASE), 2)
        self.assertFalse(finished.is_set())
        release.set()
        self.assertTrue(held.flush(2.0))
        self.assertTrue(finished.is_set())
        fake_worker = support.FakeWorker(lambda path, body: error("link_refused", 403))
        self.addCleanup(fake_worker.close)
        refusing = self.alerter(lambda link_id, secret, kind: worker.ping(link_id, secret, kind, base=fake_worker.base,
                                                                           timeout=2), start=None)
        for count in (1, 2, 3):
            refusing(QUEUE_FOUND)
            self.assertTrue(refusing.flush(2.0))
            self.assertEqual(self.state.snapshot()["relinkOffered"], count == 3)
        self.assertEqual(self.state.snapshot()["state"], "refused")
        self.assertEqual(fake_worker.bodies(), [{"linkId": LINK_ID, "secret": SECRET, "kind": QUEUE_FOUND}] * 3)
        # The last ping's result reaches the watcher's snapshot by its name, at the alerter's wall clock.
        self.assertEqual(self.watcher(alerter=refusing).snapshot(),
                         self.shown(None, ping="refused", client_state="waiting"))
        answers = {"not_delivered": lambda *_: worker.NotDelivered(500), "failed": lambda *_: 1 / 0,
                   "sent": lambda *_: worker.Sent()}
        for name, answer in answers.items():
            answering = self.alerter(answer)
            answering(MATCH_STARTED)
            self.assertEqual(self.watcher(alerter=answering).snapshot()["pingResult"], name)

    def test_the_beep_plays_the_script_s_six_notes_on_its_own_thread_and_is_silent_without_the_sound_module(self):
        # Mutation: the notes played on the caller's thread. Red: a note played on the test's thread.
        notes = []

        class Sound:
            @staticmethod
            def Beep(frequency, duration):
                notes.append((frequency, duration, threading.current_thread() is threading.main_thread()))

        thread = alert.beep(Sound)
        self.assertIsInstance(thread, threading.Thread)
        thread.join(2.0)
        self.assertEqual([(frequency, duration) for frequency, duration, _ in notes], [(988, 160), (1319, 160)] * 3)
        self.assertEqual({on_main for *_, on_main in notes}, {False})
        self.assertIsNone(alert.beep(None))

    def test_the_unverified_context_is_built_in_one_function_that_refuses_any_host_but_127_0_0_1(self):
        # Mutation: a second unverified call site in the package. Red: the search finds it outside the function.
        for host in ("localhost", "127.0.0.2", "127.1", "0x7f000001", "::1", "192.168.1.10", "example.com", "", None):
            with self.subTest(host=host), self.assertRaises(ValueError):
                client.loopback_tls_context(host)
        context = client.loopback_tls_context("127.0.0.1")
        self.assertEqual((context.verify_mode, context.check_hostname), (ssl.CERT_NONE, False))
        inside, outside = [], []
        for path in sorted(support.package_dir().glob("*.py")):
            text = path.read_text(encoding="utf-8")
            spans = [(node.lineno, node.end_lineno) for node in ast.walk(ast.parse(text))
                     if isinstance(node, ast.FunctionDef) and node.name == "loopback_tls_context"]
            for number, line in enumerate(text.splitlines(), 1):
                if UNVERIFIED.search(line):
                    (inside if any(a <= number <= b for a, b in spans) else outside).append((path.name, number))
        self.assertEqual(outside, [])
        self.assertEqual({name for name, _ in inside}, {"client.py"})
        self.assertGreaterEqual(len(inside), 2)
        self.assertEqual(client.real_addresses(51234), "https://127.0.0.1:51234")
        with self.assertRaises(ValueError):
            client.get(f"https://localhost:{self.fake.port}{PHASE}", TOKEN, 1.0)
        with self.assertRaises(client.ClientUnreachable):  # the https road, against a fake that speaks http
            client.get(client.real_addresses(self.fake.port) + PHASE, TOKEN, 0.3)
        self.assertEqual(self.fake.count("GET", PHASE), 0)

    def test_nothing_printed_logged_or_raised_holds_the_token_the_secret_or_the_link_id(self):
        # Mutation: the token in the connection line. Red: the token in a console line.
        out, err, raised = io.StringIO(), io.StringIO(), []
        fake_worker = support.FakeWorker(lambda path, body: error("link_refused", 403))
        self.addCleanup(fake_worker.close)
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            refusing = alert.Alerter(self.store, self.state, lambda link_id, secret, kind: worker.ping(
                link_id, secret, kind, base=fake_worker.base, timeout=2), beep=lambda: None, log=self.lines.append)
            self.fake.phase = "ReadyCheck"
            subject = self.watcher(alerter=refusing)
            subject.step()
            refusing.flush(2.0)
            self.fake.dropping = True
            self.clock.advance(15.1)
            subject.step()
            for call in (lambda: client.get(f"http://127.0.0.1:{self.fake.port}{PHASE}", TOKEN, 1.0),
                         lambda: client.post(f"http://127.0.0.1:{self.fake.port}{ACCEPT}", TOKEN, 1.0),
                         lambda: client.loopback_tls_context("localhost")):
                try:
                    call()
                except (client.ClientUnreachable, ValueError) as failure:
                    raised.append(f"{failure!r} {failure}")
        shown = [*self.lines, out.getvalue(), err.getvalue(), *raised, repr(subject.snapshot()),
                 repr(client.Credentials(1, TOKEN))]
        for value in (TOKEN, SECRET, LINK_ID):
            for text in shown:
                self.assertNotIn(value, text)
        for line in self.lines:
            self.assertIsNone(GAME_WORDS.search(line), line)
        self.assertEqual(len(raised), 3)
        for line in (watcher.CONNECTED_LINE, watcher.ACCEPTED_LINE, watcher.LOST_LINE, alert.PING_LINES["refused"]):
            self.assertIn(line, self.lines)


if __name__ == "__main__":
    unittest.main()
