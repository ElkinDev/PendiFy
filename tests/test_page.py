"""PairingPageTest: the loopback page, its fences and its words (design P2, P5, P6, residuals a and e)."""
import contextlib
import http.client
import io
import json
import re
import socket
import time
import unittest
import urllib.parse
from pathlib import Path

import support
from support import LINK_ID, SECRET, FakeClock

codes = support.module("codes")
config = support.module("config")
page = support.module("page")
pairing = support.module("pairing")
worker = support.module("worker")

GAME_WORDS = re.compile(r"\b(league|legends|riot|lol)\b", re.IGNORECASE)
FENCE = {"cache-control": "no-store", "referrer-policy": "no-referrer", "x-frame-options": "DENY"}
ROUTES = [("GET", "/"), ("GET", "/state"), ("POST", "/check"), ("POST", "/typed"), ("POST", "/forget"),
          ("POST", "/relink")]
# A refused POST's body is read up to 64 KiB before the answer (brief lnk5a-fix1, change 1).
DRAIN_BOUND = 64 * 1024
# Sent in one burst, headers and body share the handler's first read and a missing drain never shows.
BODY_DELAY = 0.01


class PairingPageTest(unittest.TestCase):
    def setUp(self):
        tmp = support.temp_dir()
        self.addCleanup(tmp.cleanup)
        self.store = config.ConfigStore(Path(tmp.name))
        self.checks = []
        self.state = pairing.PairingState(self.store, lambda secret: self.checks.append(secret) or worker.Refused(),
                                          clock=FakeClock())
        self.page = page.PairingPage(self.state)
        self.url = self.page.start()
        self.addCleanup(self.page.close)
        self.port = self.page.port
        self.host = f"127.0.0.1:{self.port}"

    def call(self, method, path, form=None, host=None, headers=None, token=True):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        self.addCleanup(connection.close)
        fields = dict(form or {})
        if token is True:
            fields["token"] = self.page.token
        elif token:
            fields["token"] = token
        body = urllib.parse.urlencode(fields).encode() if method == "POST" else None
        sent = {"Host": self.host if host is None else host, **(headers or {})}
        if body is not None:
            sent["Content-Type"] = "application/x-www-form-urlencoded"
        connection.request(method, path, body=body, headers=sent)
        response = connection.getresponse()
        return response.status, {k.lower(): v for k, v in response.getheaders()}, response.read().decode("utf-8")

    def html(self, language=None):
        return self.call("GET", "/", headers={"Accept-Language": language} if language else None)[2]

    def secret(self):
        return self.store.read().secret

    def test_the_server_listens_on_127_0_0_1_on_a_system_port(self):
        # Mutation: the server bound to 0.0.0.0. Red: the address is not 127.0.0.1.
        self.assertEqual(self.page.server.server_address[0], "127.0.0.1")
        self.assertNotEqual(self.port, 0)
        self.assertEqual(self.url, f"http://127.0.0.1:{self.port}/")

    def test_a_foreign_host_answers_403_on_every_route_and_changes_nothing(self):
        # Mutation: the Host check removed. Red: GET / answers 200 with the secret to a rebinding page.
        secret = self.secret()
        hosts = ["evil.example", f"evil.example:{self.port}", f"127.0.0.1:{self.port + 1}", "127.0.0.1",
                 f"localhost.evil.example:{self.port}", f"[::1]:{self.port}", f"127.0.0.2:{self.port}", ""]
        for method, path in ROUTES:
            for host in hosts:
                with self.subTest(method=method, path=path, host=host):
                    status, headers, body = self.call(method, path, form={"linkId": LINK_ID, "secret": SECRET},
                                                      host=host)
                    self.assertEqual(status, 403)
                    self.assertNotIn(secret, body)
                    self.assertNotIn(codes.display(secret), body)
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        self.addCleanup(connection.close)
        connection.putrequest("GET", "/", skip_host=True)
        connection.endheaders()
        self.assertEqual(connection.getresponse().status, 403)
        self.assertEqual(self.store.read(), config.Pairing(secret, None))
        self.assertEqual(self.checks, [])
        self.assertEqual(self.call("GET", "/", host=f"localhost:{self.port}")[0], 200)
        self.assertEqual(self.call("GET", "/", host=f"LOCALHOST:{self.port}")[0], 200)

    def test_a_post_without_the_run_token_answers_403_and_changes_nothing(self):
        # Mutation: the token compared only when present. Red: a POST with no token forgets the secret.
        secret = self.secret()
        for path in ("/check", "/typed", "/forget", "/relink"):
            for token in (False, "wrong", self.page.token[:-1], self.page.token + "x"):
                with self.subTest(path=path, token=token):
                    status, _, _ = self.call("POST", path, form={"linkId": LINK_ID, "secret": SECRET}, token=token)
                    self.assertEqual(status, 403)
        self.assertEqual(self.store.read(), config.Pairing(secret, None))
        self.assertEqual(self.checks, [])
        status, headers, _ = self.call("POST", "/check")
        self.assertEqual((status, headers["location"]), (303, "/"))
        self.assertEqual(self.checks, [secret])

    def test_every_answer_carries_the_four_fence_headers(self):
        # Mutation: the headers sent only on the page. Red: a 403 without Cache-Control.
        answers = [self.call("GET", "/"), self.call("GET", "/state"), self.call("GET", "/nothing"),
                   self.call("GET", "/", host="evil.example"), self.call("POST", "/check", token=False),
                   self.call("POST", "/check"), self.call("PUT", "/"), self.call("POST", "/nothing")]
        for status, headers, _ in answers:
            with self.subTest(status=status):
                for name, value in FENCE.items():
                    self.assertEqual(headers.get(name), value)
                policy = dict(part.strip().split(" ", 1) for part in headers["content-security-policy"].split(";"))
                self.assertEqual(policy.pop("default-src"), "'none'")
                self.assertEqual(policy.pop("style-src"), "'unsafe-inline'")
                self.assertEqual(policy.pop("script-src"), "'unsafe-inline'")
                self.assertEqual(policy.pop("connect-src"), "'self'")
                self.assertEqual({value for value in policy.values()} - {"'none'", "'self'"}, set())

    def test_the_state_route_never_holds_the_secret_or_the_link_id(self):
        # Mutation: /state carries the pair for the script. Red: the secret is in the JSON.
        for step in ("unlinked", "linked"):
            if step == "linked":
                self.call("POST", "/typed", form={"linkId": LINK_ID, "secret": self.secret()})
            status, headers, body = self.call("GET", "/state")
            self.assertEqual((status, headers["content-type"]), (200, "application/json"))
            state = json.loads(body)
            self.assertEqual(state["linked"], step == "linked")
            for value in (self.secret(), codes.display(self.secret()), LINK_ID, codes.display(LINK_ID)):
                self.assertNotIn(value, body)

    def test_the_qr_and_the_4_4_4_form_show_only_without_a_link_id_or_after_a_relink(self):
        # Mutation: the QR drawn whatever the state. Red: an svg on the linked page.
        secret = self.secret()
        shown = self.html()
        self.assertIn("<svg", shown)
        self.assertIn(codes.display(secret), shown)
        self.call("POST", "/typed", form={"linkId": LINK_ID, "secret": secret})
        linked = self.html()
        for value in ("<svg", secret, codes.display(secret), LINK_ID, codes.display(LINK_ID), 'action="/relink"'):
            self.assertNotIn(value, linked)
        self.call("POST", "/relink")  # nothing offered: a no-op
        self.assertEqual(self.store.read().link_id, LINK_ID)
        for _ in range(3):
            self.state.record_ping(worker.Refused())
        offered = self.html()
        self.assertIn('action="/relink"', offered)
        self.assertNotIn("<svg", offered)
        self.assertEqual(self.call("POST", "/relink")[0], 303)
        again = self.html()
        self.assertIn("<svg", again)
        self.assertIn(codes.display(secret), again)
        self.assertEqual(self.store.read(), config.Pairing(secret, None))

    def test_the_typed_road_stores_a_normalized_pair_and_refuses_a_malformed_one(self):
        # Mutation: the typed fields stored without normalizing. Red: the dashed form lands in the file.
        before = self.store.read()
        self.assertEqual(self.call("POST", "/typed", form={"linkId": "WXYZ6789ABC", "secret": SECRET})[0], 303)
        self.assertEqual(self.store.read(), before)
        self.assertIn(page.WORDS["es"]["typed_refused"], self.html())
        self.call("POST", "/typed", form={"linkId": "wxyz-6789-abcd", "secret": "abcd-2345-efgh"})
        self.assertEqual(self.store.read(), config.Pairing(SECRET, LINK_ID))

    def test_forget_changes_the_secret(self):
        # Mutation: /forget clears only the link id. Red: the secret is unchanged.
        old = self.secret()
        self.assertEqual(self.call("POST", "/forget")[0], 303)
        new = self.secret()
        self.assertNotEqual(new, old)
        shown = self.html()
        self.assertIn(codes.display(new), shown)
        self.assertNotIn(codes.display(old), shown)
        self.assertIn(page.WORDS["es"]["forget_sentence"], shown)

    def test_nothing_reaches_stdout_or_stderr_during_any_request(self):
        # Mutation: log_message left to the base class. Red: a request line on stderr.
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            for method, path in ROUTES + [("GET", "/nothing"), ("PUT", "/")]:
                self.call(method, path)
                self.call(method, path, host="evil.example")
                self.call(method, path, token="wrong")
            with socket.create_connection(("127.0.0.1", self.port), timeout=5) as raw:
                raw.sendall(b"NOT A REQUEST\r\n\r\n")
                self.assertIn(b"400", raw.recv(4096))
        self.assertEqual((out.getvalue(), err.getvalue()), ("", ""))

    def test_the_words_follow_the_first_language_tag_and_name_no_game(self):
        # Mutation: the English table names the game in its intro. Red: a game word in the page.
        self.assertEqual(set(page.WORDS), {"es", "en"})
        self.assertEqual(set(page.WORDS["es"]), set(page.WORDS["en"]))
        for table in page.WORDS.values():
            for text in table.values():
                self.assertIsNone(GAME_WORDS.search(text), text)
        for language, expected in ((None, "es"), ("es-CO,es;q=0.9", "es"), ("en-US,en;q=0.9", "en"),
                                   ("fr-FR,en;q=0.8", "es"), ("EN", "en")):
            with self.subTest(language=language):
                shown = self.html(language)
                self.assertIn(f'<html lang="{expected}">', shown)
                self.assertIn(page.WORDS[expected]["forget"], shown)
                self.assertIsNone(GAME_WORDS.search(shown))
                self.assertNotIn("http", shown)
                self.assertIsNone(re.search(r"\b(src|href)\s*=|<img|<link|@import|url\(", shown))
                state = json.loads(self.call("GET", "/state", headers={"Accept-Language": language} if language
                                             else None)[2])
                self.assertEqual(state["text"], page.WORDS[expected]["state_waiting"])

    def post_in_two_sends(self, path, body, host=None, declared=None):
        """A POST whose body follows its headers in a second send, as a browser may send a form: the status
        and the Connection header, or the name of the socket error met in their place."""
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            connection.putrequest("POST", path, skip_host=True, skip_accept_encoding=True)
            connection.putheader("Host", self.host if host is None else host)
            connection.putheader("Content-Type", "application/x-www-form-urlencoded")
            connection.putheader("Content-Length", str(len(body) if declared is None else declared))
            connection.endheaders()
            # The body a moment after the headers, as over a real network: a handler that answers without
            # reading it has closed by then, and Windows resets the socket (150 of 150 without the drain).
            time.sleep(BODY_DELAY)
            connection.send(body)
            response = connection.getresponse()
            response.read()
            return response.status, response.getheader("Connection")
        except OSError as failure:
            return type(failure).__name__, None
        finally:
            connection.close()

    def test_a_refused_post_is_answered_after_its_body_is_read_and_never_reset(self):
        # Mutation: the drain removed from do_POST. Red: Windows resets some of the 150 connections.
        secret = self.secret()
        cases = [("foreign host", "/typed", b"x" * 3000, "evil.example", 403),
                 ("no such route", "/nothing", b"x" * 3000, None, 404),
                 ("too large", "/typed", b"x" * (3 * page.MAX_FORM_BYTES), None, 413)]
        for name, path, body, host, status in cases:
            with self.subTest(name=name):
                answers = [self.post_in_two_sends(path, body, host) for _ in range(50)]
                self.assertEqual([answer for answer in answers if answer != (status, "close")], [])
        self.assertEqual(self.store.read(), config.Pairing(secret, None))
        self.assertEqual(self.checks, [])

    def test_a_declared_body_past_the_bound_is_read_up_to_it_and_answered(self):
        # Mutation: the drain reads the whole declaration. Red: the handler waits for a megabyte never sent.
        self.assertEqual(self.post_in_two_sends("/typed", b"x" * DRAIN_BOUND, declared=1024 * 1024),
                         (413, "close"))

    def test_every_answer_says_it_closes_its_connection(self):
        # Mutation: the Connection header dropped from _send. Red: the page's 200 carries none.
        answers = [self.call("GET", "/"), self.call("GET", "/state"), self.call("GET", "/nothing"),
                   self.call("GET", "/", host="evil.example"), self.call("POST", "/check", token=False),
                   self.call("POST", "/check"), self.call("POST", "/nothing"), self.call("PUT", "/"),
                   self.call("HEAD", "/")]
        self.assertEqual([(status, headers.get("connection")) for status, headers, _ in answers],
                         [(200, "close"), (200, "close"), (404, "close"), (403, "close"), (403, "close"),
                          (303, "close"), (404, "close"), (501, "close"), (200, "close")])

    def test_head_runs_the_fences_and_answers_the_headers_of_get_with_no_body(self):
        # Mutation: do_HEAD removed. Red: a foreign-Host HEAD answers 501 from the base class.
        for path in ("/", "/state", "/nothing"):
            with self.subTest(path=path):
                status, _, body = self.call("HEAD", path, host="evil.example")
                self.assertEqual((status, body), (403, ""))
        with socket.create_connection(("127.0.0.1", self.port), timeout=5) as raw:
            raw.sendall(f"HEAD / HTTP/1.1\r\nHost: {self.host}\r\n\r\n".encode("ascii"))
            received = b""
            while chunk := raw.recv(65536):
                received += chunk
        head, _, rest = received.partition(b"\r\n\r\n")
        lines = head.decode("latin-1").split("\r\n")
        headers = {name.lower(): value for name, value in (line.split(": ", 1) for line in lines[1:])}
        self.assertEqual((lines[0].split(" ")[1], rest), ("200", b""))
        for name, value in FENCE.items():
            self.assertEqual(headers.get(name), value)
        self.assertEqual(headers["content-type"], "text/html; charset=utf-8")
        # A HEAD is not a person looking at the page: no automatic check falls due.
        self.assertIsNone(self.state.tick())
        self.assertEqual(self.checks, [])
        self.assertEqual(int(headers["content-length"]), len(self.html().encode("utf-8")))


if __name__ == "__main__":
    unittest.main()
