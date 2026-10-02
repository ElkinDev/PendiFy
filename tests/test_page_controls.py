"""PageControlsTest: the page's two controls and the mechanics under them.

The pause and the resume of the watcher and the start with Windows are three POST routes fenced as every other
(host, length, token; 303 to the page); the state line says a linked PC's alerts are paused; /state carries
paused and autostart so the page's poll follows a second tab. The start with Windows runs on a MemoryRegistry.
The controls are drawn as placement A of the controls design, round 2 of 2026-10-01 (frames A1 to A6): one
card «Este PC» right after the watcher line, holding the pause or, while paused, the resume, then the switch of the
start with Windows when one is available; while paused the watcher line is the sheet's paused block.
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
# The watcher line, running or paused (the sheet's paused block carries data-paused, frames A2 and A6).
WATCH_LINE = re.compile(r'<p id="watch" role="status"(?: data-paused="")?>(.*?)</p>', re.S)
# The card's title, the one new word of round 2, as the sheet gives it.
THIS_PC = {"es": "Este PC", "en": "This PC"}
CARD_OPEN = '<div class="panel pc" role="group" aria-labelledby="pc-title">'
FORM_ACTIONS = re.compile(r'<form [^>]*?\baction="([^"]+)"')
# The language switch's two forms open every page's header, ahead of the card's (lane pclang, owner report OR-96).
SWITCH_ACTIONS = ["/lang"]
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

    def card(self, language, paused=False, switch=None, served=None):
        """The card as frames A1 to A6 draw it, the sheet's scope outline left out: the pause form, or the resume
        form while paused, then the switch when `switch` is not None, checked when it is True."""
        words = page.WORDS[language]
        action = "resume" if paused else "pause"
        inner = (f'<h2 id="pc-title">{THIS_PC[language]}</h2><form method="post" action="/{action}"><input '
                 f'type="hidden" name="token" value="{(served or self.page).token}"><button type="submit">'
                 f"{html.escape(words[action])}</button></form>")
        if switch is not None:
            inner += ('<label class="switch"><input type="checkbox" role="switch" name="autostart" '
                      'aria-labelledby="sw-label" aria-describedby="sw-help"' + (" checked" if switch else "") +
                      f'><span class="sw-text"><span class="sw-label" id="sw-label">{html.escape(words["autostart_label"])}'
                      f'</span><span class="sw-help" id="sw-help">{html.escape(words["autostart_help"])}</span></span>'
                      "</label>")
        return f"{CARD_OPEN}{inner}</div>"

    def test_active_the_card_holds_the_pause_form_and_no_resume(self):
        # Mutation: the card left out. Red: no card after the watcher line.
        # Mutation: the resume drawn whatever the pause. Red: action="/resume" on a running page.
        for language in ("es", "en"):
            with self.subTest(language=language):
                shown = self.call("GET", "/", language=language)[2]
                self.assertEqual(shown.count(CARD_OPEN), 1)
                self.assertIn(self.card(language, switch=False), shown)
                self.assertEqual(FORM_ACTIONS.findall(shown), SWITCH_ACTIONS + ["/pause", "/typed", "/forget", "/quit"])
                self.assertNotIn('action="/resume"', shown)
                self.assertIn(THIS_PC[language], PageText(shown).texts)
                self.assertNotIn(page.WORDS[language]["resume"], PageText(shown).texts)
                self.assertNotIn("data-paused=\"\"", shown)

    def test_paused_the_card_holds_the_resume_form_and_the_watcher_line_is_the_paused_block(self):
        # Mutation: the pause form kept while paused. Red: action="/pause" and no resume.
        # Mutation: the watcher line drawn as a running one. Red: no data-paused on #watch.
        self.snapshot["paused"] = True
        linked = [words["state_linked"] for words in page.WORDS.values()]
        for language in ("es", "en"):
            with self.subTest(language=language):
                words = page.WORDS[language]
                shown = self.call("GET", "/", language=language)[2]
                self.assertIn(self.card(language, paused=True, switch=False), shown)
                self.assertEqual(FORM_ACTIONS.findall(shown),
                                 SWITCH_ACTIONS + ["/resume", "/typed", "/forget", "/quit"])
                self.assertNotIn('action="/pause"', shown)
                self.assertIn(f'<p id="watch" role="status" data-paused="">{html.escape(words["watch_paused"])}</p>'
                              f"{CARD_OPEN}", shown)
                answer = json.dumps(self.state_json(language), ensure_ascii=False)
                for sentence in linked:  # neither the page nor its poll says the alerts will arrive
                    self.assertNotIn(html.escape(sentence), shown)
                    self.assertNotIn(sentence, answer)

    def test_the_switch_is_drawn_only_with_an_available_autostart_checked_as_enabled_says(self):
        # Mutation: the switch drawn unchecked whatever the registry holds. Red: no checked on an enabled start.
        # Mutation: the switch drawn with no Autostart. Red: role="switch" on a page given none.
        for language in ("es", "en"):
            with self.subTest(language=language):
                self.assertIn(self.card(language, switch=False), self.call("GET", "/", language=language)[2])
        self.autostart.enable()
        shown = self.call("GET", "/", language="en")[2]
        self.assertIn(self.card("en", switch=True), shown)
        for ident in ("pc-title", "sw-label", "sw-help"):
            self.assertEqual(shown.count(f'id="{ident}"'), 1, ident)
        named = re.findall(r'aria-(?:labelledby|describedby)="([^"]+)"', shown)
        self.assertEqual(sorted(named), ["pc-title", "sw-help", "sw-label"])
        self.assertTrue(all(f'id="{ident}"' in shown for ident in named))
        self.autostart.disable()
        self.assertIn(self.card("en", switch=False), self.call("GET", "/", language="en")[2])
        controls = {"watch": lambda: dict(self.snapshot), "on_pause": lambda: None, "on_resume": lambda: None}
        given_none = self.serve(**controls)
        saved = sys.modules.get("winreg")
        sys.modules["winreg"] = None  # a system with no Run value: the start with Windows is not available
        self.addCleanup(lambda: sys.modules.pop("winreg", None) if saved is None else
                        sys.modules.__setitem__("winreg", saved))
        unavailable = self.serve(autostart=autostart.Autostart(executable=sys.executable), **controls)
        for served in (given_none, unavailable):
            shown = self.call("GET", "/", served=served)[2]
            self.assertIn(self.card("es", served=served), shown)
            for marker in ('role="switch"', 'type="checkbox"', 'id="sw-label"', 'id="sw-help"', 'class="switch"'):
                self.assertNotIn(marker, shown)
        # The switch posts its change as the theme button does, then the page is read again from the registry.
        self.assertIn("var sw=document.querySelector('input[name=autostart]');if(!sw)return;", page._SCRIPT)
        self.assertIn("fetch('/autostart',{method:'POST',body:new URLSearchParams({token:f.value,"
                      "on:sw.checked?'1':'0'})}).then(function(){location.reload();})", page._SCRIPT)

    def test_a_page_built_with_no_watcher_draws_no_card(self):
        # Mutation: the card drawn on every page. Red: «Este PC» on a page with no watcher.
        bare = self.serve(on_quit=lambda: None, on_pause=lambda: None, on_resume=lambda: None,
                          autostart=self.autostart)
        for language in ("es", "en"):
            with self.subTest(language=language):
                shown = self.call("GET", "/", language=language, served=bare)[2]
                for marker in ('class="panel pc"', 'id="pc-title"', 'action="/pause"', 'action="/resume"',
                               'role="switch"', 'id="watch"'):
                    self.assertNotIn(marker, shown)
                texts = PageText(shown).texts
                for text in (THIS_PC[language], page.WORDS[language]["pause"], page.WORDS[language]["autostart_label"]):
                    self.assertNotIn(text, texts)

    def test_the_card_sits_after_the_watcher_line_and_before_the_key_block_in_both_languages(self):
        # Mutation: the card after the key block. Red: the key card before it.
        after_watch = re.compile(r'<p id="watch" role="status">[^<]*</p>' + re.escape(CARD_OPEN))
        for language in ("es", "en"):
            with self.subTest(language=language, linked=True):
                shown = self.call("GET", "/", language=language)[2]
                self.assertIsNotNone(after_watch.search(shown))
                self.assertLess(shown.index(CARD_OPEN), shown.index('</section><details class="fold"'))
        self.call("POST", "/forget")  # the pairing page: the key card in its own column
        for language in ("es", "en"):
            with self.subTest(language=language, linked=False):
                shown = self.call("GET", "/", language=language)[2]
                self.assertIsNotNone(after_watch.search(shown))
                places = [shown.index(marker) for marker in ('<section class="link">', '<p id="watch"', CARD_OPEN,
                                                             '<figure class="key-card"',
                                                             'action="/check"', '</section><details class="fold"')]
                self.assertEqual(places, sorted(places))
                texts = PageText(shown).texts
                key = texts.index(page.WORDS[language]["code_label"])
                # The key card opens with the QR, whose mask carries the instruction as text (lane pfmaskimpl).
                self.assertEqual(texts[texts.index(THIS_PC[language]) + 1:key],
                                 [page.WORDS[language]["pause"], page.WORDS[language]["autostart_label"],
                                  page.WORDS[language]["autostart_help"], page.WORDS[language]["qr_press"]])

    def test_the_poll_reloads_the_page_when_the_pause_differs_from_what_it_rendered(self):
        # Mutation: the poll ignores the pause. Red: a second tab keeps the old line under its old controls.
        self.assertIn("const paused=s.dataset.paused;", page._SCRIPT)
        self.assertIn("(paused!==undefined&&String(j.paused)!==paused)", page._SCRIPT)
        bare = self.serve()
        # No element of a page with no watcher carries the attribute; the style's #watch[data-paused] rule is no
        # attribute (round 2, the sheet's paused block).
        self.assertNotIn("data-paused=", self.call("GET", "/", served=bare)[2])

    def test_the_poll_reloads_the_page_when_the_start_with_windows_differs_from_the_switch(self):
        # Mutation: the poll ignores the start with Windows. Red: a second tab keeps the switch off while the
        # registry holds the value turned on in the first.
        poll = page._SCRIPT[:page._SCRIPT.index("location.reload();")]
        self.assertIn("const sw=document.querySelector('input[name=autostart]');", poll[:poll.index("setInterval(")])
        self.assertTrue(poll.endswith("||(sw&&j.autostart!==undefined&&String(j.autostart)!==String(sw.defaultChecked)))"))
        self.assertEqual(page._SCRIPT.count("j.autostart"), 2)  # both in the guarded compare, none elsewhere
        # A page with no switch: the compare is behind sw, the only switch. With no available Autostart neither the
        # page nor /state carries it; a page with no watcher draws no switch, though its /state answers autostart.
        controls = {"watch": lambda: dict(self.snapshot), "on_pause": lambda: None, "on_resume": lambda: None}
        saved = sys.modules.get("winreg")
        sys.modules["winreg"] = None
        self.addCleanup(lambda: sys.modules.pop("winreg", None) if saved is None else
                        sys.modules.__setitem__("winreg", saved))
        unavailable = self.serve(autostart=autostart.Autostart(executable=sys.executable), **controls)
        self.assertNotIn('name="autostart"', self.call("GET", "/", served=unavailable)[2])
        self.assertNotIn("autostart", self.state_json(served=unavailable))
        bare = self.serve(on_pause=lambda: None, on_resume=lambda: None, autostart=self.autostart)
        self.assertNotIn('name="autostart"', self.call("GET", "/", served=bare)[2])
        self.assertIs(self.state_json(served=bare)["autostart"], False)

    def test_a_switch_post_that_fails_puts_the_switch_back_and_reloads_nothing(self):
        # Mutation: the catch left empty. Red: the switch stays flipped with nothing written.
        self.assertIn("fetch('/autostart',{method:'POST',body:new URLSearchParams({token:f.value,"
                      "on:sw.checked?'1':'0'})}).then(function(){location.reload();})"
                      ".catch(function(){sw.checked=!sw.checked;});});})();", page._SCRIPT)


if __name__ == "__main__":
    unittest.main()
