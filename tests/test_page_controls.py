"""PageControlsTest: the mechanics under the page's two controls, with no control drawn in this round.

The pause and the resume of the watcher and the start with Windows are three POST routes fenced as every other
(host, length, token; 303 to the page); the state line says a linked PC's alerts are paused; /state carries
paused and autostart so the page's poll follows a second tab. The start with Windows runs on a MemoryRegistry.
"""
import html
import http.client
import json
import re
import sys
import unittest
import urllib.parse
from pathlib import Path

import support
from support import LINK_ID, SECRET, FakeClock, MemoryRegistry
from test_page import GAME_WORDS, PageText

autostart = support.module("autostart")
config = support.module("config")
page = support.module("page")
pairing = support.module("pairing")
worker = support.module("worker")

CONTROL_ROUTES = ("/pause", "/resume", "/autostart")
# The words of the two controls, both languages, as lanes/pcctl-design-2026-10-01.md :9 and its round 2 give them.
NEW_WORDS = {
    "es": {"pause": "Pausar avisos", "resume": "Reanudar avisos",
           "watch_paused": "En pausa: este PC no lee el juego ni avisa a tu teléfono.",
           "autostart_label": "Iniciar con Windows",
           "autostart_help": "Al encender el PC el programa empieza solo, sin abrir esta página.",
           "state_linked_paused": "Este PC está enlazado. Los avisos están en pausa."},
    "en": {"pause": "Pause alerts", "resume": "Resume alerts",
           "watch_paused": "Paused: this PC is not reading the game or alerting your phone.",
           "autostart_label": "Start with Windows",
           "autostart_help": "When the PC turns on, the program starts by itself, without opening this page.",
           "state_linked_paused": "This PC is linked. Alerts are paused."},
}
LINKED_ES = "Los avisos llegarán a tu cuenta."
STATE_LINE = re.compile(r'<p id="state"([^>]*)>(.*?)</p>', re.S)
WATCH_LINE = re.compile(r'<p id="watch" role="status">(.*?)</p>', re.S)
FOREIGN_HOSTS = ("evil.example", "127.0.0.1", "localhost.evil.example:{port}", "127.0.0.2:{port}", "")
WRONG_TOKENS = (False, "wrong")


class PageControlsTest(unittest.TestCase):
    def setUp(self):
        tmp = support.temp_dir()
        self.addCleanup(tmp.cleanup)
        self.store = config.ConfigStore(Path(tmp.name))
        self.store.set_typed(LINK_ID, SECRET)
        self.state = pairing.PairingState(self.store, lambda secret: worker.Refused(), clock=FakeClock())
        self.snapshot = {"client": "connected", "phase": "Lobby", "alert": None, "at": None, "pingResult": None,
                         "pingAt": None, "paused": False}
        self.calls, self.registry = [], MemoryRegistry()
        self.autostart = autostart.Autostart(registry=self.registry, executable=sys.executable)
        self.page = self.serve(watch=lambda: dict(self.snapshot), on_quit=lambda: None,
                               on_pause=lambda: self.calls.append("pause"),
                               on_resume=lambda: self.calls.append("resume"), autostart=self.autostart)

    def serve(self, **kwargs):
        served = page.PairingPage(self.state, **kwargs)
        served.start()
        self.addCleanup(served.close)
        return served

    def call(self, method, path, form=None, host=None, token=True, language=None, served=None):
        served = served or self.page
        connection = http.client.HTTPConnection("127.0.0.1", served.port, timeout=5)
        self.addCleanup(connection.close)
        fields = dict(form or {})
        if token is True:
            fields["token"] = served.token
        elif token:
            fields["token"] = token
        headers = {"Host": f"127.0.0.1:{served.port}" if host is None else host}
        if language:
            headers["Accept-Language"] = language
        body = urllib.parse.urlencode(fields).encode() if method == "POST" else None
        if body is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        connection.request(method, path, body=body, headers=headers)
        answer = connection.getresponse()
        return answer.status, answer.getheader("Location"), answer.read().decode("utf-8")

    def post_with_no_length(self, path):
        connection = http.client.HTTPConnection("127.0.0.1", self.page.port, timeout=5)
        self.addCleanup(connection.close)
        connection.putrequest("POST", path, skip_host=True, skip_accept_encoding=True)
        connection.putheader("Host", f"127.0.0.1:{self.page.port}")
        connection.endheaders()
        return connection.getresponse().status

    def state_json(self, language="es", served=None):
        return json.loads(self.call("GET", "/state", language=language, served=served)[2])

    def test_linked_and_paused_the_page_and_the_state_say_the_alerts_are_paused_in_both_languages(self):
        # Mutation: render keeps the linked sentence while paused. Red: «Los avisos llegarán a tu cuenta.» on /.
        # Mutation: /state keeps it. Red: the poll writes it back over the paused sentence.
        self.assertEqual(self.state.snapshot()["state"], "linked")
        self.snapshot["paused"] = True
        for language in ("es", "en"):
            with self.subTest(language=language):
                words = page.WORDS[language]
                shown = self.call("GET", "/", language=language)[2]
                attributes, sentence = STATE_LINE.search(shown).groups()
                self.assertEqual(sentence, html.escape(words["state_linked_paused"]))
                self.assertIn('data-paused="true"', attributes)
                self.assertEqual(WATCH_LINE.search(shown).group(1), html.escape(words["watch_paused"]))
                self.assertNotIn(html.escape(words["state_linked"]), shown)
                answer = self.state_json(language)
                self.assertEqual((answer["text"], answer["watchText"], answer["paused"]),
                                 (words["state_linked_paused"], words["watch_paused"], True))
        self.assertNotIn(LINKED_ES, self.call("GET", "/", language="es")[2])
        self.assertNotIn(LINKED_ES, json.dumps(self.state_json("es"), ensure_ascii=False))

    def test_not_paused_the_page_and_the_state_read_as_today(self):
        # Mutation: the paused sentence whatever the snapshot says. Red: a running watcher reads paused.
        for language in ("es", "en"):
            with self.subTest(language=language):
                words = page.WORDS[language]
                shown = self.call("GET", "/", language=language)[2]
                attributes, sentence = STATE_LINE.search(shown).groups()
                self.assertEqual(sentence, html.escape(words["state_linked"]))
                self.assertIn('data-paused="false"', attributes)
                watching = " ".join((words["watch_connected"], words["watch_phase"].format(phase=words["phase_lobby"])))
                self.assertEqual(WATCH_LINE.search(shown).group(1), html.escape(watching))
                answer = self.state_json(language)
                self.assertEqual((answer["text"], answer["watchText"], answer["paused"]),
                                 (words["state_linked"], watching, False))
        # A pairing page that is not linked keeps its own sentence whatever the watcher says.
        self.snapshot["paused"] = True
        self.call("POST", "/forget")
        self.assertEqual(self.state_json("en")["text"], page.WORDS["en"]["state_waiting"])

    def test_each_control_route_is_fenced_as_every_post_route_and_calls_its_callback_once(self):
        # Mutation: the three routes answered before the token check. Red: a POST with no token pauses.
        forms = {"/pause": {}, "/resume": {}, "/autostart": {"on": "1"}}
        for path in CONTROL_ROUTES:
            with self.subTest(path=path):
                refused = [self.call("POST", path, form=forms[path], host=host.format(port=self.page.port))[0]
                           for host in FOREIGN_HOSTS]
                refused += [self.call("POST", path, form=forms[path], token=token)[0] for token in WRONG_TOKENS]
                refused.append(self.post_with_no_length(path))
                self.assertEqual(refused, [403] * (len(FOREIGN_HOSTS) + len(WRONG_TOKENS)) + [411])
                self.assertEqual((self.calls, [c for c in self.registry.calls if c != "read"]), ([], []))
        self.assertEqual(self.call("POST", "/pause")[:2], (303, "/"))
        self.assertEqual(self.calls, ["pause"])
        self.assertEqual(self.call("POST", "/resume")[:2], (303, "/"))
        self.assertEqual(self.calls, ["pause", "resume"])
        self.assertEqual(self.call("POST", "/autostart", form={"on": "1"})[:2], (303, "/"))
        self.assertEqual((self.registry.value, self.state_json()["autostart"]), (self.autostart.command(), True))
        self.assertEqual(self.call("POST", "/autostart", form={"on": "0"})[:2], (303, "/"))
        self.assertEqual((self.registry.value, self.state_json()["autostart"]), (None, False))
        self.assertEqual([c for c in self.registry.calls if c != "read"], ["write", "delete"])

    def test_autostart_with_on_neither_1_nor_0_is_400_and_changes_nothing(self):
        # Mutation: any value but 1 read as off. Red: a 303 and a delete.
        for value in ("2", "", "true", "01", " 1", None):
            with self.subTest(value=value):
                form = {} if value is None else {"on": value}
                self.assertEqual(self.call("POST", "/autostart", form=form)[0], 400)
        self.assertEqual([c for c in self.registry.calls if c != "read"], [])
        self.assertIsNone(self.registry.value)

    def test_a_page_given_no_controls_answers_404_on_the_three_and_its_state_says_nothing_of_them(self):
        # Mutation: the three routes always listed. Red: a page with nothing to pause answers 303.
        bare = self.serve()
        for path in CONTROL_ROUTES:
            with self.subTest(path=path):
                self.assertEqual(self.call("POST", path, form={"on": "1"}, served=bare)[0], 404)
        answer = self.state_json(served=bare)
        self.assertNotIn("paused", answer)
        self.assertNotIn("autostart", answer)
        saved = sys.modules.get("winreg")
        sys.modules["winreg"] = None  # a system with no Run value: the start with Windows is not available

        def restore():
            if saved is None:
                sys.modules.pop("winreg", None)
            else:
                sys.modules["winreg"] = saved

        self.addCleanup(restore)
        missing = autostart.Autostart(executable=sys.executable)
        self.assertFalse(missing.available)
        watched = self.serve(watch=lambda: dict(self.snapshot), autostart=missing)
        self.assertEqual(self.call("POST", "/autostart", form={"on": "1"}, served=watched)[0], 404)
        self.assertNotIn("autostart", self.state_json(served=watched))
        self.assertEqual(self.state_json(served=watched)["paused"], False)

    def test_the_six_new_words_exist_in_both_languages_as_the_design_gives_them(self):
        # Mutation: a word with an em-dash or a game's name. Red: the scan below finds it.
        for language, words in NEW_WORDS.items():
            for key, text in words.items():
                with self.subTest(language=language, key=key):
                    self.assertEqual(page.WORDS[language][key], text)
                    self.assertNotIn("—", text)
                    self.assertIsNone(GAME_WORDS.search(text), text)
        self.assertEqual(set(page.WORDS["es"]), set(page.WORDS["en"]))

    def test_the_page_draws_no_new_control_in_this_round(self):
        # Mutation: a pause button drawn. Red: a form action and a word more than today's page.
        today = self.serve(watch=lambda: dict(self.snapshot), on_quit=lambda: None)
        for paused in (False, True):
            self.snapshot["paused"] = paused
            for language in ("es", "en"):
                with self.subTest(paused=paused, language=language):
                    shown = self.call("GET", "/", language=language)[2]
                    before = self.call("GET", "/", language=language, served=today)[2]
                    actions = re.findall(r'<form method="post" action="([^"]+)"', shown)
                    self.assertEqual(actions, re.findall(r'<form method="post" action="([^"]+)"', before))
                    self.assertEqual(sorted(actions), ["/forget", "/quit", "/typed"])
                    self.assertEqual(shown.count("<button"), before.count("<button"))
                    self.assertEqual(shown.count("<input"), before.count("<input"))
                    self.assertNotIn("checkbox", shown)
                    self.assertNotIn('role="switch"', shown)
                    texts = PageText(shown).texts
                    for key in ("pause", "resume", "autostart_label", "autostart_help"):
                        self.assertNotIn(page.WORDS[language][key], texts)

    def test_the_poll_reloads_the_page_when_the_pause_differs_from_what_it_rendered(self):
        # Mutation: the poll ignores the pause. Red: a second tab keeps the old line under its old controls.
        self.assertIn("const paused=s.dataset.paused;", page._SCRIPT)
        self.assertIn("(paused!==undefined&&String(j.paused)!==paused)", page._SCRIPT)
        bare = self.serve()
        self.assertNotIn("data-paused", self.call("GET", "/", served=bare)[2])


if __name__ == "__main__":
    unittest.main()
