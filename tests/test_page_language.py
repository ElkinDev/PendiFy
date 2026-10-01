"""PageLanguageTest: the page's language switch (lane pclang, owner report OR-96).

The page speaks the language kept in config.json when there is one, else the browser's first Accept-Language tag as
before; one resolver serves the page, /state (the poll's sentences and the log's lines) and the stopped page. The
switch is pendiapp.com's header markup, one form .langsw that is the group (role="group", named «Idioma» or
"Language") with ES then EN, the current one aria-current="true", right before the theme button; each entry is a
submit button of that form posting its value to /lang, fenced as /theme is.
"""
import contextlib
import html
import http.client
import io
import json
import re
import unittest
import urllib.parse
from pathlib import Path

import support
from support import LINK_ID, SECRET, FakeClock

config = support.module("config")
page = support.module("page")
pairing = support.module("pairing")
worker = support.module("worker")

ENGLISH = "en-US,en;q=0.9"
SPANISH = "es-CO,es;q=0.9"
# The group's accessible name, pendiapp.com's own: the one new word of the lane.
GROUP_NAMES = {"es": "Idioma", "en": "Language"}
# One event of each kind the log draws in words, so the log's lines show their language.
EVENTS = [(1, 1_700_000_000.0, "started", None), (2, 1_700_000_001.0, "connected", None),
          (3, 1_700_000_002.0, "phase", "Lobby"), (4, 1_700_000_003.0, "ping", "sent")]


def switch(lang, token, name):
    """The switch as pendiapp.com's header draws it, on this page's form fence: one form that is the group, named in
    the page's language, its token, then ES and EN, the current one marked."""
    current = ' aria-current="true"'
    entries = "".join(f'<button type="submit" name="lang" value="{code}"{current if code == lang else ""}>'
                      f"{code.upper()}</button>" for code in ("es", "en"))
    return (f'<form class="langsw" role="group" aria-label="{name}" method="post" action="/lang">'
            f'<input type="hidden" name="token" value="{token}">{entries}</form>')


class PageLanguageTest(unittest.TestCase):
    def setUp(self):
        tmp = support.temp_dir()
        self.addCleanup(tmp.cleanup)
        self.store = config.ConfigStore(Path(tmp.name))
        self.state = pairing.PairingState(self.store, lambda secret: worker.Refused(), clock=FakeClock())
        self.quits = []
        self.page = self.serve(self.state)

    def serve(self, state):
        served = page.PairingPage(state, watch=lambda: {"client": "connected", "phase": "Lobby"},
                                  on_quit=lambda: self.quits.append(True), events=lambda: list(EVENTS))
        served.start()
        self.addCleanup(served.close)
        return served

    def call(self, method, path, form=None, language=None, host=None, token=True, served=None, raw=None):
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
        body = raw if raw is not None else urllib.parse.urlencode(fields).encode() if method == "POST" else None
        if body is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        return response.status, {k.lower(): v for k, v in response.getheaders()}, response.read().decode("utf-8")

    def shown(self, language=None, served=None):
        return self.call("GET", "/", language=language, served=served)[2]

    def polled(self, language=None):
        return json.loads(self.call("GET", "/state", language=language)[2])

    def stopped(self, language):
        status, _, body = self.call("POST", "/quit", language=language)
        self.assertEqual(status, 200)
        return body

    def assert_speaks(self, lang, document=None, state=None, stopped=None):
        words = page.WORDS[lang]
        if document is not None:
            self.assertIn(f'<html lang="{lang}"><head>', document)
            self.assertIn(f'<p class="tagline">{html.escape(words["title"])}</p>', document)
            self.assertIn(html.escape(words["log_title"]), document)
            # One entry marked in the body; the style's own rule names the attribute too.
            self.assertEqual(document.split("</head>")[1].count('aria-current="true"'), 1)
            self.assertIn(switch(lang, self.page.token, GROUP_NAMES[lang]), document)
        if state is not None:
            self.assertEqual(state["lang"], lang)
            self.assertEqual(state["text"], words["state_waiting"])
            self.assertEqual(state["watchText"], words["watch_connected"] + " " + words["watch_phase"].format(
                phase=words["phase_lobby"]))
            self.assertEqual([line["text"] for line in state["log"]],
                             [words["log_started"], words["log_connected"], words["phase_lobby"] + ".",
                              words["log_ping"].format(result=words["ping_sent"])])
        if stopped is not None:
            self.assertIn(f'<html lang="{lang}"><head>', stopped)
            self.assertIn(f"<p>{html.escape(words['stopped'])}</p>", stopped)

    def test_with_no_choice_the_page_follows_the_browser_as_before(self):
        # Mutation: the resolver answers "es" with no choice. Red: an English browser gets the Spanish page.
        self.assertIsNone(self.store.read_lang())
        for language, lang in ((ENGLISH, "en"), (SPANISH, "es"), (None, "es"), ("fr-FR,en;q=0.9", "es")):
            with self.subTest(language=language):
                self.assert_speaks(lang, document=self.shown(language), state=self.polled(language),
                                   stopped=self.stopped(language))
        self.assertIsNone(self.store.read_lang())

    def test_a_kept_choice_wins_over_the_browser_on_the_page_its_poll_and_the_stopped_page(self):
        # Mutation: the resolver left out of /state. Red: the poll's sentence comes back in the browser's language.
        # Mutation: the stopped page resolved from Accept-Language alone. Red: an English stopped page after ES.
        for chosen, language in (("es", ENGLISH), ("en", SPANISH)):
            with self.subTest(chosen=chosen):
                status, headers, _ = self.call("POST", "/lang", {"lang": chosen}, language=language)
                self.assertEqual((status, headers["location"]), (303, "/"))
                self.assertEqual(self.store.read_lang(), chosen)
                self.assertEqual(json.loads(self.store.path.read_text(encoding="utf-8"))["lang"], chosen)
                self.assert_speaks(chosen, document=self.shown(language), state=self.polled(language),
                                   stopped=self.stopped(language))

    def test_the_choice_survives_a_restart_and_a_second_browser(self):
        # Mutation: the choice kept in the running state only. Red: a new run serves an English browser in English.
        self.call("POST", "/lang", {"lang": "es"}, language=ENGLISH)
        again = pairing.PairingState(self.store, lambda secret: worker.Refused(), clock=FakeClock())
        self.page = self.serve(again)
        for language in (ENGLISH, "en-GB", None):
            with self.subTest(language=language):
                self.assert_speaks("es", document=self.shown(language), state=self.polled(language))

    def test_the_switch_is_the_sites_markup_right_before_the_theme_button_on_each_page_state(self):
        # Mutation: EN drawn before ES. Red: the markup differs. Mutation: the switch drawn after the theme button.
        # Red: the header does not open with the switch.
        def header(document):
            return re.search(r'<header class="bar">(.*?)<button id="theme-toggle"', document).group(1)

        for state_name in ("waiting", "linked", "refused"):
            if state_name == "linked":
                self.assertEqual(self.call("POST", "/typed", {"linkId": LINK_ID, "secret": SECRET})[0], 303)
            if state_name == "refused":
                for _ in range(3):
                    self.state.record_ping(worker.Refused())
            for language, lang in ((SPANISH, "es"), (ENGLISH, "en")):
                with self.subTest(state=state_name, lang=lang):
                    document = self.shown(language)
                    self.assertEqual('action="/relink"' in document, state_name == "refused")
                    self.assertEqual(header(document), switch(lang, self.page.token, GROUP_NAMES[lang]))
                    self.assertEqual(document.count('class="langsw"'), 1)
                    self.assertEqual(document.count('action="/lang"'), 1)
                    self.assertEqual(document.split("</head>")[1].count('aria-current="true"'), 1)
        self.call("POST", "/lang", {"lang": "en"})
        self.assertEqual(header(self.shown(SPANISH)), switch("en", self.page.token, "Language"))
        self.assertEqual((page.WORDS["es"]["language"], page.WORDS["en"]["language"]), ("Idioma", "Language"))
        # The stopped page has no theme button, so no switch.
        self.assertNotIn('class="langsw"', self.stopped(SPANISH))

    def test_the_lang_route_is_fenced_as_the_theme_route_and_keeps_only_es_or_en(self):
        # Mutation: /lang written before the token check. Red: a post with a wrong token changes config.json.
        # Mutation: the value written unchecked. Red: a post of "fr" is not 400 and changes the file.
        self.call("POST", "/lang", {"lang": "en"})
        before = self.store.path.read_bytes()
        for token in (False, "wrong", self.page.token[:-1]):
            with self.subTest(token=token):
                self.assertEqual(self.call("POST", "/lang", {"lang": "es"}, token=token)[0], 403)
        for host in ("evil.example", f"127.0.0.1:{self.page.port + 1}", f"localhost.evil.example:{self.page.port}"):
            with self.subTest(host=host):
                self.assertEqual(self.call("POST", "/lang", {"lang": "es"}, host=host)[0], 403)
        for value in ("", "fr", "ES", "es-CO", "system", " es"):
            with self.subTest(value=value):
                status, _, body = self.call("POST", "/lang", {"lang": value})
                self.assertEqual((status, body), (400, "bad request"))
        self.assertEqual(self.call("POST", "/lang", {})[0], 400)
        self.assertEqual(self.call("POST", "/lang", raw=b"lang=es&token=" + self.page.token.encode() + b"&pad="
                                   + b"x" * page.MAX_FORM_BYTES)[0], 413)
        # A GET never changes the choice.
        self.assertEqual(self.call("GET", "/lang?lang=es")[0], 404)
        self.assertEqual(self.call("GET", "/?lang=es")[0], 200)
        self.assertEqual(self.store.path.read_bytes(), before)
        self.assertEqual(self.store.read_lang(), "en")
        self.assertEqual(self.quits, [])
        status, headers, body = self.call("POST", "/lang", {"lang": "es"})
        self.assertEqual((status, headers["location"], body), (303, "/", ""))
        self.assertEqual(json.loads(self.store.path.read_text(encoding="utf-8")),
                         {"secret": self.store.read().secret, "linkId": None, "lang": "es"})

    def test_a_lang_post_whose_write_fails_is_answered_as_a_failed_save_and_the_kept_language_stays(self):
        # Mutation: the ConfigError of /lang left to the server. Red: no answer and no page sentence.
        self.call("POST", "/lang", {"lang": "en"})
        before = self.store.path.read_bytes()

        def refuse(choice):  # the way a config.json held by another program refuses the replace
            raise config.ConfigError("held")

        self.store.set_lang = refuse
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            status, headers, _ = self.call("POST", "/lang", {"lang": "es"})
        shown = self.shown(SPANISH)
        self.assertEqual((status, headers.get("location"), err.getvalue()),
                         (303, "/", " [page] a request failed: ConfigError\n"))
        self.assertIn('<html lang="en"><head>', shown)
        self.assertIn(html.escape(page.WORDS["en"]["config_failed"]), shown)
        self.assertEqual(self.store.path.read_bytes(), before)

    def test_the_poll_reloads_the_page_when_the_language_differs_from_what_it_rendered(self):
        # Mutation: the poll ignores the language. Red: a second browser keeps its old language after a switch.
        poll = page._SCRIPT[:page._SCRIPT.index("location.reload();")]
        self.assertIn("const lang=document.documentElement.lang;", poll[:poll.index("setInterval(")])
        self.assertIn("(paused!==undefined&&String(j.paused)!==paused)||j.lang!==lang||", poll)
        self.assertEqual(page._SCRIPT.count("j.lang"), 1)
        self.assertEqual(self.polled(ENGLISH)["lang"], "en")
        self.call("POST", "/lang", {"lang": "es"})
        self.assertEqual(self.polled(ENGLISH)["lang"], "es")

    def test_the_style_holds_the_sites_three_switch_rules_on_the_pages_own_variables(self):
        # Mutation: the current entry left on the muted colour. Red: the third rule is missing.
        for rule in (".langsw{display:inline-flex;border:1px solid var(--hair);border-radius:999px;padding:2px;"
                     "font-size:.8rem;font-weight:600}",
                     ".langsw button{min-height:0;padding:.28rem .62rem;border:0;border-radius:999px;"
                     "background:transparent;color:var(--ink2);font:inherit}",
                     '.langsw button[aria-current="true"]{background:var(--brand);color:var(--on-brand)}',
                     ".bar{display:flex;flex-wrap:wrap;justify-content:flex-end;align-items:center;gap:.45rem;margin:0 0 8px}"):
            with self.subTest(rule=rule):
                self.assertIn(rule, page._STYLE)
        for variable in ("--hair:", "--ink2:", "--brand:", "--on-brand:"):
            self.assertIn(variable, page._LIGHT)
            self.assertIn(variable, page._DARK)


if __name__ == "__main__":
    unittest.main()
