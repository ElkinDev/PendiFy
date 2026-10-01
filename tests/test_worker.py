"""LinkWorkerClientTest: check and ping against a fake Worker on 127.0.0.1 (W src/link-check.ts, src/link-ping.ts)."""
import contextlib
import io
import json
import socket
import time
import unittest
from unittest import mock

import support
from support import LINK_ID, SECRET, error

worker = support.module("worker")

SENT = (200, {"result": "sent", "devices": 1, "sent": 1, "reasons": {"sent": 1}})
NO_TARGETS = (200, {"result": "no_targets", "devices": 0, "sent": 0, "reasons": {}})


class LinkWorkerClientTest(unittest.TestCase):
    def fake(self, answer):
        fake = support.FakeWorker(answer)
        self.addCleanup(fake.close)
        return fake

    def test_the_constants(self):
        # Mutation: the timeout raised to 30 s. Red: the constant differs from S:85.
        self.assertEqual(worker.BASE_URL, "https://followapp-ai-proxy.niklerk23.workers.dev")  # the host of S:84
        self.assertEqual(worker.TIMEOUT_SECONDS, 5)
        self.assertEqual(worker.KINDS, ("lol_queue_found", "lol_match_started"))

    def test_check_posts_exactly_the_secret(self):
        # Mutation: check adds a kind to its body. Red: the body has two keys.
        fake = self.fake(lambda path, body: (200, {"linkId": LINK_ID}))
        self.assertEqual(worker.check("abcd-2345-efgh", base=fake.base), worker.Linked(LINK_ID))
        request = fake.requests[0]
        self.assertEqual((request["method"], request["path"]), ("POST", "/v1/link-check"))
        self.assertEqual(json.loads(request["body"]), {"secret": SECRET})
        self.assertEqual(request["headers"]["content-type"], "application/json")

    def test_each_check_answer_maps_to_its_result(self):
        # Mutation: throttled read as a refusal. Red: Refused where Throttled is expected.
        answers = [
            (error("link_refused", 403), worker.Refused()),
            (error("throttled", 429), worker.Throttled()),
            (error("invalid-request", 400), worker.Failed("HTTP 400")),
            ((500, b"not json"), worker.Failed("HTTP 500")),
            ((200, {"linkId": "wxyz-6789-abcd"}), worker.Failed("HTTP 200")),
            ((200, {}), worker.Failed("HTTP 200")),
            ((200, ["WXYZ6789ABCD"]), worker.Failed("HTTP 200")),
        ]
        for reply, expected in answers:
            with self.subTest(reply=reply):
                fake = self.fake(lambda path, body, reply=reply: reply)
                self.assertEqual(worker.check(SECRET, base=fake.base), expected)

    def test_ping_posts_exactly_link_id_secret_and_kind(self):
        # Mutation: ping sends the display form of the secret. Red: the secret carries dashes.
        fake = self.fake(lambda path, body: SENT)
        self.assertEqual(worker.ping(LINK_ID, "abcd-2345-efgh", "lol_queue_found", base=fake.base), worker.Sent())
        request = fake.requests[0]
        self.assertEqual(request["path"], "/v1/link-ping")
        self.assertEqual(json.loads(request["body"]), {"linkId": LINK_ID, "secret": SECRET, "kind": "lol_queue_found"})

    def test_check_and_ping_name_the_program_in_the_user_agent(self):
        # Mutation: the User-Agent header removed. Red: urllib's default Python-urllib/<version> is sent,
        # the signature the edge refuses with error 1010 before the Worker reads the request.
        fake = self.fake(lambda path, body: (200, {"linkId": LINK_ID}) if path == "/v1/link-check" else SENT)
        worker.check(SECRET, base=fake.base)
        worker.ping(LINK_ID, SECRET, "lol_match_started", base=fake.base)
        self.assertEqual([request["path"] for request in fake.requests], ["/v1/link-check", "/v1/link-ping"])
        for request in fake.requests:
            with self.subTest(path=request["path"]):
                agent = request["headers"].get("user-agent", "")
                self.assertTrue(agent.startswith("pendify/"), agent)
                self.assertFalse(agent.startswith("Python-urllib"), agent)

    def test_a_broken_or_empty_version_lookup_falls_back_to_source(self):
        # Mutation: the broad except restored to PackageNotFoundError only. Red: the KeyError subtest raises.
        def raise_key_error(name):
            raise KeyError(name)

        for label, replacement in (("None", lambda name: None), ("KeyError", raise_key_error)):
            with self.subTest(lookup=label):
                with mock.patch.object(worker.importlib.metadata, "version", replacement):
                    self.assertEqual(worker._user_agent(), "pendify/source")

    def test_each_ping_answer_maps_to_its_result_as_s_reads_it(self):
        # Mutation: a 200 with sent 0 read as Sent. Red: Sent where NotDelivered(200) is expected (S:303).
        answers = [
            (SENT, worker.Sent()),
            (NO_TARGETS, worker.NotDelivered(200)),
            (error("link_refused", 403), worker.Refused()),
            (error("throttled", 429), worker.NotDelivered(429)),
            (error("invalid-request", 400), worker.NotDelivered(400)),
            ((503, b"<html>"), worker.NotDelivered(503)),
        ]
        for reply, expected in answers:
            with self.subTest(reply=reply):
                fake = self.fake(lambda path, body, reply=reply: reply)
                self.assertEqual(worker.ping(LINK_ID, SECRET, "lol_match_started", base=fake.base), expected)

    def test_any_other_kind_or_a_malformed_value_is_refused_before_a_call(self):
        # Mutation: the kind check removed. Red: the fake Worker receives a request.
        fake = self.fake(lambda path, body: SENT)
        for link_id, secret, kind in ((LINK_ID, SECRET, "match_found"), (LINK_ID, SECRET, ""),
                                      (LINK_ID, "ABCD2345EFG", "lol_queue_found"), (None, SECRET, "lol_queue_found")):
            with self.subTest(kind=kind):
                with self.assertRaises(ValueError):
                    worker.ping(link_id, secret, kind, base=fake.base)
        with self.assertRaises(ValueError):
            worker.check("ABCD2345EFG", base=fake.base)
        self.assertEqual(fake.requests, [])

    def test_a_redirect_is_never_followed(self):
        # Mutation: the default opener that follows redirects. Red: the fake sees a GET of /followed.
        fake = self.fake(lambda path, body: (302, {}, {"Location": fake.base + "/followed"}))
        self.assertEqual(worker.check(SECRET, base=fake.base), worker.Failed("HTTP 302"))
        self.assertEqual(worker.ping(LINK_ID, SECRET, "lol_queue_found", base=fake.base), worker.NotDelivered(302))
        time.sleep(0.05)
        self.assertEqual([r["method"] for r in fake.requests], ["POST", "POST"])

    def test_a_worker_that_never_answers_gives_failed_within_the_timeout(self):
        # Mutation: the timeout argument not passed to urlopen. Red: the call hangs past the bound.
        fake = self.fake(lambda path, body: None)
        started = time.monotonic()
        self.assertEqual(worker.check(SECRET, base=fake.base, timeout=0.2), worker.Failed("TimeoutError"))
        self.assertEqual(worker.ping(LINK_ID, SECRET, "lol_queue_found", base=fake.base, timeout=0.2),
                         worker.Failed("TimeoutError"))
        self.assertLess(time.monotonic() - started, 1.5)

    def test_a_closed_port_gives_failed(self):
        # Mutation: the connection error re-raised. Red: URLError out of check.
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        result = worker.check(SECRET, base=f"http://127.0.0.1:{port}", timeout=0.5)
        self.assertIsInstance(result, worker.Failed)
        # Windows retries a refused loopback connect for about two seconds, so the bound may fire first.
        self.assertIn(result.reason, ("ConnectionRefusedError", "TimeoutError"))

    def test_nothing_printed_logged_raised_or_shown_carries_the_secret_or_the_link_id(self):
        # Mutation: the exception text carries the body. Red: the secret is in the ValueError text.
        replies = [SENT, NO_TARGETS, error("link_refused", 403), error("throttled", 429), (500, b"x"),
                   (200, {"linkId": LINK_ID}), None]
        texts, out, err = [], io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            for reply in replies:
                fake = self.fake(lambda path, body, reply=reply: reply)
                texts.append(repr(worker.check(SECRET, base=fake.base, timeout=0.2)))
                texts.append(repr(worker.ping(LINK_ID, SECRET, "lol_queue_found", base=fake.base, timeout=0.2)))
            for call in (lambda: worker.ping(LINK_ID, SECRET, "not_a_kind", base=fake.base),
                         lambda: worker.ping("WXYZ6789ABC", SECRET, "lol_queue_found", base=fake.base),
                         lambda: worker.check(SECRET[:11], base=fake.base),
                         lambda: worker.loopback_base(f"http://127.0.0.1:1/{SECRET}")):
                with self.assertRaises(ValueError) as caught:
                    call()
                texts.append(str(caught.exception))
                texts.append(repr(caught.exception))
        texts += [out.getvalue(), err.getvalue()]
        self.assertEqual(out.getvalue() + err.getvalue(), "")
        for text in texts:
            for value in (SECRET, LINK_ID, SECRET[:11], LINK_ID[:11]):
                self.assertNotIn(value, text)

    def test_only_a_loopback_address_replaces_the_worker(self):
        # Mutation: any http address accepted. Red: http://example.com:80 passes.
        self.assertEqual(worker.loopback_base("http://127.0.0.1:8080"), "http://127.0.0.1:8080")
        self.assertEqual(worker.loopback_base("http://localhost:8080/"), "http://localhost:8080")
        for value in ("", "https://127.0.0.1:8080", "http://127.0.0.1", "http://127.0.0.2:8080",
                      "http://example.com:80", "http://127.0.0.1:80@example.com:80", "http://127.0.0.1:8080/v1",
                      "http://[::1]:8080", "http://127.0.0.1:8080?x=1", None):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    worker.loopback_base(value)


if __name__ == "__main__":
    unittest.main()
