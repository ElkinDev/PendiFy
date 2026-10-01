"""PairingPageTest: the loopback page, its fences and its words (design P2, P5, P6, residuals a and e)."""
import contextlib
import html
import http.client
import io
import json
import re
import socket
import threading
import time
import unittest
import urllib.parse
from html.parser import HTMLParser
from pathlib import Path

import support
from support import LINK_ID, SECRET, FakeClock

try:
    import msvcrt
except ImportError:  # not Windows
    msvcrt = None

codes = support.module("codes")
config = support.module("config")
page = support.module("page")
plate_almena = support.module("plate_almena")
pairing = support.module("pairing")
qr = support.module("qr")
worker = support.module("worker")

GAME_WORDS = re.compile(r"\b(league|legends|riot|lol)\b", re.IGNORECASE)
FENCE = {"cache-control": "no-store", "referrer-policy": "no-referrer", "x-frame-options": "DENY"}
ROUTES = [("GET", "/"), ("GET", "/state"), ("POST", "/check"), ("POST", "/typed"), ("POST", "/forget"),
          ("POST", "/relink")]
# A refused POST's body is read up to 64 KiB before the answer (brief lnk5a-fix1, change 1).
DRAIN_BOUND = 64 * 1024
# Sent in one burst, headers and body share the handler's first read and a missing drain never shows.
BODY_DELAY = 0.01
# The page word for a config file that cannot be read or replaced (brief lnk5a-notes, change 3).
CONFIG_WORDS = {"es": "No se pudo leer ni guardar la configuración de este PC.",
                "en": "This PC's settings could not be read or saved."}
# The watcher line's words (brief pcpg-live, change 3): connected, the phase frame, the ping frame; each phase the
# client names, in Spanish and in English, with one name the map does not hold; each ping result by its name.
WATCH_FRAMES = {"es": ("Conectado al cliente del juego.", "Ahora: {}.", "Aviso al teléfono: {}, a las {}."),
                "en": ("Connected to the game client.", "Now: {}.", "Alert to the phone: {}, at {}.")}
PHASE_TEXTS = {"None": ("Sin partida", "No game"), "Lobby": ("En la sala", "In the lobby"),
               "Matchmaking": ("Buscando partida", "Looking for a match"),
               "ReadyCheck": ("Partida encontrada", "Match found"), "ChampSelect": ("Eligiendo", "Choosing"),
               "InProgress": ("En partida", "In a game"), "EndOfGame": ("Fin de la partida", "Game over"),
               "PreEndOfGame": ("Fin de la partida", "Game over"),
               "WaitingForStats": ("Fin de la partida", "Game over"),
               "Reconnect": ("Otro estado", "Other state")}
PING_TEXTS = {"sent": ("enviado", "sent"),
              "refused": ("rechazado por tu cuenta", "refused by your account"),
              "not_delivered": ("no entregado", "not delivered"), "failed": ("falló el envío", "sending failed")}
WINDOWS_ONLY = "another handle that locks config.json against a read and a replace is a Windows behavior"
# The words of a waiting page with a watcher and a quit button, in the order the page before the design showed
# them (page.py at 8f6b90661), with the key's summary before its label (brief pcpg-hide, change 1; the summary shows the code since brief pcpg-port); None is the key
# as codes.display prints it.
PAGE_ORDER = ("title", "title", "intro", "state_waiting", "watch_waiting", "scan", "show_code", "code_label", None,
              "check", "typed_title", "link_id_label", "secret_label", "save", "forget", "forget_sentence", "forget",
              "quit")


class PageText(HTMLParser):
    """The text a person reads on a page, in document order: every text node outside style and script, stripped,
    the blank ones left out."""

    def __init__(self, document):
        super().__init__()
        self.texts, self.hidden = [], 0
        self.feed(document)
        self.close()

    def handle_starttag(self, tag, attrs):
        self.hidden += tag in ("style", "script")

    def handle_endtag(self, tag):
        self.hidden -= tag in ("style", "script")

    def handle_data(self, data):
        if not self.hidden and data.strip():
            self.texts.append(data.strip())


# The policy as it was before the icon (brief pcico, change 5): the icon is a data URI, which img-src data: allows.
TODAY_POLICY = ("default-src 'none'; img-src data:; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
                "connect-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'")
# The program's icon as the page carries it: the tab icon in the head and, on the pairing page, the image left of the
# title, both the 32 px PNG of icon.py as a data URI.
ICON_PREFIX = "data:image/png;base64,"
ICON_TAGS = re.compile(r'<link rel="icon" type="image/png" href="data:image/png;base64,[A-Za-z0-9+/=]+">'
                       r'|<img alt="" width="32" height="32" src="data:image/png;base64,[A-Za-z0-9+/=]+">')


def icon_uri():
    return ICON_PREFIX + support.module("icon").PNG_32


def without_icon(document):
    """The document with the icon's own link and image taken out, for the pins that allow no other image."""
    return ICON_TAGS.sub("", document)


def outside_references(document):
    """Every href and url() of a page that is neither a fragment of the page itself nor the QR plate's data URI
    nor the icon's."""
    document = without_icon(document)
    found = re.findall(r'\bhref\s*=\s*"([^"]*)"', document) + re.findall(r"url\(([^)]*)\)", document)
    return [ref for ref in found if not ref.startswith("#") and ref != plate_almena.PLATE_DATA_URI]


class KeyPlace(HTMLParser):
    """Where a page prints `key`: for each text node that holds it, the attributes of every <details> around it
    (an empty list when none is), and each <details> start tag's attributes in document order. The same for the
    QR (`qr_places`, each <svg class="qr">) and for every attribute that carries a data URI (`data_places`)."""

    def __init__(self, document, key):
        super().__init__()
        self.key, self.open_details, self.details, self.places = key, [], [], []
        self.qr_places, self.data_places = [], []
        self.feed(document)
        self.close()

    def handle_starttag(self, tag, attrs):
        if tag == "svg" and ("class", "qr") in attrs:
            self.qr_places.append(list(self.open_details))
        self.data_places += [list(self.open_details) for _, value in attrs
                             if (value or "").startswith("data:") and not (value or "").startswith(ICON_PREFIX)]
        if tag == "details":
            self.open_details.append(dict(attrs))
            self.details.append(dict(attrs))

    def handle_endtag(self, tag):
        if tag == "details":
            self.open_details.pop()

    def handle_data(self, data):
        if self.key in data:
            self.places.append(list(self.open_details))


@contextlib.contextmanager
def held(path):
    """path open in another handle with its bytes locked, as another program holds config.json."""
    size = path.stat().st_size
    with open(path, "rb") as handle:
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, size)
        try:
            yield
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, size)


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
                self.assertEqual(headers["content-security-policy"],
                                 "default-src 'none'; img-src data:; style-src 'unsafe-inline'; script-src "
                                 "'unsafe-inline'; connect-src 'self'; form-action 'self'; frame-ancestors 'none'; "
                                 "base-uri 'none'")
                policy = dict(part.strip().split(" ", 1) for part in headers["content-security-policy"].split(";"))
                self.assertEqual(policy.pop("default-src"), "'none'")
                self.assertEqual(policy.pop("img-src"), "data:")
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
        self.assertIn('<svg class="qr"', shown)
        self.assertIn(codes.display(secret), shown)
        self.call("POST", "/typed", form={"linkId": LINK_ID, "secret": secret})
        linked = self.html()
        for value in ('<svg class="qr"', "data:", secret, codes.display(secret), LINK_ID, codes.display(LINK_ID),
                      'action="/relink"'):
            self.assertNotIn(value, linked)
        self.call("POST", "/relink")  # nothing offered: a no-op
        self.assertEqual(self.store.read().link_id, LINK_ID)
        for _ in range(3):
            self.state.record_ping(worker.Refused())
        offered = self.html()
        self.assertIn('action="/relink"', offered)
        self.assertNotIn('<svg class="qr"', offered)
        self.assertEqual(self.call("POST", "/relink")[0], 303)
        again = self.html()
        self.assertIn('<svg class="qr"', again)
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
                self.assertIsNone(re.search(r"\bsrc\s*=|<img|<link|@import", without_icon(shown)))
                self.assertEqual(outside_references(shown), [])
                state = json.loads(self.call("GET", "/state", headers={"Accept-Language": language} if language
                                             else None)[2])
                self.assertEqual(state["text"], page.WORDS[expected]["state_waiting"])

    def test_the_page_loads_nothing_from_outside_and_every_word_keeps_its_one_place(self):
        # Mutation: the scan sentence left out of the QR's column. Red: the words miss WORDS["es"]["scan"].
        # Mutation: a web font imported by the style. Red: "@import" and an http address on the page.
        secret = self.secret()
        self.page = page.PairingPage(self.state, watch=lambda: {"client": "waiting"}, on_quit=lambda: None)
        self.page.start()
        self.addCleanup(self.page.close)
        self.port, self.host = self.page.port, f"127.0.0.1:{self.page.port}"
        symbol = qr.encode(qr.pairing_address(secret).encode("ascii")).modules
        for accept, lang in (("es-CO,es;q=0.9", "es"), ("en-US,en;q=0.9", "en")):
            with self.subTest(lang=lang):
                shown, words = self.html(accept), page.WORDS[lang]
                self.assertEqual(PageText(shown).texts,
                                 [codes.display(secret) if key is None else words[key] for key in PAGE_ORDER])
                self.assertEqual(shown.count(f'data-closed="{html.escape(words["state_closed"])}"'), 1)
                for outside in ("<link", "src=", "@import", "@font-face", "http"):
                    self.assertNotIn(outside, without_icon(shown))
                self.assertEqual(outside_references(shown), [])
                # The pairing address is only in the QR's own modules: the page draws the scene of the encoder's
                # symbol over the one plate (SceneDecodeTest reads it with ZXing).
                drawn = re.findall(r'<svg class="qr".*?</svg>', shown, re.S)
                self.assertEqual(drawn, [qr.scene_svg(symbol, plate_almena.PLATE_DATA_URI, labelledby="scan")])
        self.assertEqual(self.call("POST", "/typed", form={"linkId": "WXYZ6789ABC", "secret": SECRET})[0], 303)
        refused = list(PAGE_ORDER)
        refused.insert(refused.index("save") + 1, "typed_refused")
        self.assertEqual(PageText(self.html()).texts,
                         [codes.display(secret) if key is None else page.WORDS["es"][key] for key in refused])

    def test_each_document_carries_one_tab_icon_from_a_data_uri_in_its_head(self):
        # Mutation: the icon link left out of render_stopped. Red: the stopped page holds no rel="icon".
        uri = icon_uri()
        documents = {"served": self.html(), "es": self.page.render("es"), "en": self.page.render("en"),
                     "stopped es": self.page.render_stopped("es"), "stopped en": self.page.render_stopped("en")}
        for name, document in documents.items():
            with self.subTest(document=name):
                links = re.findall(r"<link\b[^>]*>", document)
                self.assertEqual(links, [f'<link rel="icon" type="image/png" href="{uri}">'])
                self.assertIn(links[0], document.split("</head>")[0])
                self.assertEqual(document.count('rel="icon"'), 1)

    def test_the_pairing_page_title_row_shows_the_icon_left_of_the_title(self):
        # Mutation: the img placed after the h1. Red: the row does not open with the img. Mutation: the alt text
        # set to the title. Red: the row's img carries a non-empty alt.
        uri = icon_uri()
        for accept, lang in (("es-CO,es;q=0.9", "es"), ("en-US,en;q=0.9", "en")):
            with self.subTest(lang=lang):
                shown = self.html(accept)
                title = html.escape(page.WORDS[lang]["title"])
                self.assertEqual(shown.count("<img"), 1)
                self.assertEqual(shown.count("<h1>"), 1)
                self.assertIn(f'<div class="title-row"><img alt="" width="32" height="32" src="{uri}"><h1>{title}'
                              f'</h1></div>', shown)
        self.assertIn(".title-row{display:flex;align-items:center;gap:12px;margin:0 0 8px}", page._STYLE)
        self.assertIn(".title-row img{flex:none;image-rendering:pixelated}", page._STYLE)
        self.assertNotIn("<img", self.page.render_stopped("es"))

    def test_the_icon_adds_no_origin_to_the_policy_and_no_outside_source_to_the_page(self):
        # Mutation: img-src widened with 'self' for the icon. Red: the policy differs from today's. Mutation: the
        # tab icon served as href="/favicon.ico". Red: a source that is not a data URI or a fragment.
        self.assertEqual(page.POLICY, TODAY_POLICY)
        _, headers, shown = self.call("GET", "/")
        self.assertEqual(headers["content-security-policy"], TODAY_POLICY)
        for document in (shown, self.page.render("en"), self.page.render_stopped("es")):
            sources = re.findall(r'\b(?:src|href)\s*=\s*"([^"]*)"', document)
            self.assertIn(icon_uri(), sources)
            self.assertEqual([source for source in sources if not source.startswith(("data:", "#"))], [])
            self.assertEqual([source for source in sources if source.startswith(("http", "//"))], [])
            self.assertNotIn("http", document)

    def test_the_qr_and_the_key_are_served_only_inside_one_closed_details_whose_summary_shows_the_code(self):
        # Mutation: the QR drawn before the details, as on main. Red: the svg and its data URI have no details
        # around them. Mutation: the details rendered open. Red: its attributes hold "open". Mutation: the timer's
        # 60000 turned to 600000. Red: the script pin misses it.
        secret = self.secret()
        for accept, lang in (("es-CO,es;q=0.9", "es"), ("en-US,en;q=0.9", "en")):
            with self.subTest(lang=lang):
                shown = self.html(accept)
                self.assertEqual(shown.count(codes.display(secret)), 1)
                self.assertNotIn(secret, shown)
                place = KeyPlace(shown, codes.display(secret))
                self.assertEqual(place.places, [[{}]])
                self.assertEqual(place.qr_places, [[{}]])
                self.assertEqual(place.data_places, [[{}]])
                self.assertEqual(place.details, [{}])
                self.assertEqual(shown.count("data:image/webp;base64,"), 1)
                self.assertIn(f'<figure class="key-card"><details><summary>'
                              f"{html.escape(page.WORDS[lang]['show_code'])}</summary>", shown)
                self.assertIn(".key-card summary::-webkit-details-marker{display:none}", shown)
        self.assertEqual((page.WORDS["es"]["show_code"], page.WORDS["en"]["show_code"]),
                         ("Mostrar el código", "Show the code"))
        self.assertNotIn("show_key", page.WORDS["es"])
        self.assertNotIn("show_key", page.WORDS["en"])
        # The one timer: opened, the details closes 60 s later; any toggle clears the timer first.
        self.assertIn("const d=document.querySelector('.key-card details');let hide;"
                      "if(d)d.addEventListener('toggle',()=>{clearTimeout(hide);"
                      "if(d.open)hide=setTimeout(()=>{d.open=false;},60000);});", page._SCRIPT)
        self.assertEqual(page._SCRIPT.count("setTimeout("), 1)

    def test_the_theme_button_of_the_site_is_served_with_its_words_its_head_read_and_its_rules(self):
        # Mutation: the head read left out. Red: the head holds no pendi-theme read. Mutation: the dark variables
        # left out of the data-theme="dark" block. Red: the block is not the dark list.
        for accept, lang in (("es-CO,es;q=0.9", "es"), ("en-US,en;q=0.9", "en")):
            with self.subTest(lang=lang):
                shown = self.html(accept)
                self.assertEqual(shown.count('<button id="theme-toggle"'), 1)
                self.assertIn(f'<main class="page"><header class="bar"><button id="theme-toggle" class="icon-btn" '
                              f'type="button" aria-pressed="false" aria-label="'
                              f'{html.escape(page.WORDS[lang]["theme_toggle"])}"><svg class="moon"', shown)
                self.assertIn('<svg class="sun"', shown)
                head = shown.split("</head>")[0]
                self.assertIn("<script>(function(){try{var e=document.documentElement;"
                              "if(e.hasAttribute('data-theme'))return;var t=localStorage.getItem('pendi-theme');"
                              "if(t==='light'||t==='dark')e.setAttribute('data-theme',t);}catch(x){}})();</script>",
                              head)
        self.assertEqual((page.WORDS["es"]["theme_toggle"], page.WORDS["en"]["theme_toggle"]),
                         ("Cambiar entre tema claro y oscuro", "Switch between light and dark theme"))
        for handler in ("btn.addEventListener('click',function(){var next=current()==='dark'?'light':'dark';",
                        "document.documentElement.setAttribute('data-theme',next);",
                        "try{localStorage.setItem('pendi-theme',next);}catch(e){}reflect();",
                        "btn.setAttribute('aria-pressed',String(current()==='dark'));",
                        "var f=document.querySelector('input[name=token]');"
                        "if(f)fetch('/theme',{method:'POST',body:new URLSearchParams({token:f.value,theme:next})})"):
            self.assertIn(handler, page._SCRIPT)
        self.assertIn(':root[data-theme="light"]{color-scheme:light;' + page._LIGHT + "}", page._STYLE)
        self.assertIn(':root[data-theme="dark"]{color-scheme:dark;' + page._DARK + "}", page._STYLE)
        self.assertIn("@media (prefers-color-scheme:dark){:root{" + page._DARK + "}}", page._STYLE)
        self.assertIn("--bg:#131022;", page._DARK)
        self.assertIn("--bg:#FAF8FE;", page._LIGHT)
        # The button is the page's own touch minimum, 44 px square, never the site's 38 px.
        self.assertIn(".icon-btn{display:inline-grid;place-items:center;width:44px;height:44px;min-height:0;",
                      page._STYLE)
        # The stopped page keeps a stored choice too.
        self.assertIn("localStorage.getItem('pendi-theme')", self.page.render_stopped("es").split("</head>")[0])

    def test_the_theme_choice_is_kept_in_the_config_file_and_served_as_data_theme(self):
        # Mutation: /theme answered without a write. Red: the GET after a POST of dark carries no data-theme.
        # Mutation: the value written unchecked. Red: a POST of a fourth value is not 400 and changes config.json.
        self.assertNotIn("data-theme=", self.html().split("<head>")[0])
        self.assertEqual(self.call("POST", "/theme", {"theme": "dark"})[0], 204)
        self.assertIn('<html lang="es" data-theme="dark"><head>', self.html("es"))
        self.assertEqual(json.loads(self.store.path.read_text(encoding="utf-8"))["theme"], "dark")
        self.assertEqual(self.store.read().secret, self.secret())
        self.assertEqual(self.call("POST", "/theme", {"theme": "light"})[0], 204)
        self.assertIn('<html lang="en" data-theme="light"><head>', self.html("en"))
        # A new run reads the choice back from the file.
        again = page.PairingPage(pairing.PairingState(self.store, lambda secret: worker.Refused(), clock=FakeClock()))
        self.assertIn('data-theme="light"', again.render("en").split("<head>")[0])
        self.assertEqual(self.call("POST", "/theme", {"theme": "system"})[0], 204)
        self.assertNotIn("data-theme=", self.html().split("<head>")[0])
        self.assertNotIn("theme", json.loads(self.store.path.read_text(encoding="utf-8")))
        self.call("POST", "/theme", {"theme": "dark"})
        before = self.store.path.read_bytes()
        for value in ("", "blue", "Dark", "system "):
            with self.subTest(value=value):
                self.assertEqual(self.call("POST", "/theme", {"theme": value})[0], 400)
                self.assertEqual(self.store.path.read_bytes(), before)
        self.assertEqual(self.call("POST", "/theme", {})[0], 400)
        self.assertEqual(self.call("POST", "/theme", {"theme": "light"}, token="wrong")[0], 403)
        self.assertEqual(self.store.path.read_bytes(), before)
        self.assertIn(' data-theme="dark"><head>', self.html())

    def test_a_theme_post_whose_write_fails_is_answered_as_a_failed_save_and_the_kept_theme_stays(self):
        # Mutation: the ConfigError of /theme answered 204. Red: the answer is 204, not 303 to the page.
        self.assertEqual(self.call("POST", "/theme", {"theme": "light"})[0], 204)
        before = self.store.path.read_bytes()

        def refuse(choice):  # the way a config.json held by another program refuses the replace
            raise config.ConfigError("held")

        self.store.set_theme = refuse
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            status, headers, _ = self.call("POST", "/theme", {"theme": "dark"})
        shown = self.html("es")
        self.assertEqual((status, headers.get("location"), err.getvalue()),
                         (303, "/", " [page] a request failed: ConfigError\n"))
        self.assertIn('<html lang="es" data-theme="light"><head>', shown)
        self.assertIn(CONFIG_WORDS["es"], shown)
        self.assertEqual(self.store.path.read_bytes(), before)

    def test_the_stopped_page_carries_the_kept_theme_as_the_pairing_page_does(self):
        # Mutation: render_stopped without the config's theme. Red: its <html> carries no data-theme="dark".
        self.assertNotIn("data-theme=", self.page.render_stopped("es").split("<head>")[0])
        self.assertEqual(self.call("POST", "/theme", {"theme": "dark"})[0], 204)
        self.assertIn('<html lang="es" data-theme="dark"><head>', self.page.render_stopped("es"))
        self.assertIn('<html lang="en" data-theme="dark"><head>', self.page.render_stopped("en"))

    def test_the_watcher_line_shows_in_both_languages_follows_the_watcher_and_names_no_game(self):
        # Mutation: the watcher's line left out of the state answer. Red: no watchText in /state.
        snapshot = {"client": "waiting", "alert": None, "at": None}
        watched = page.PairingPage(self.state, watch=lambda: dict(snapshot))
        watched.start()
        self.addCleanup(watched.close)

        def get(path, language):
            connection = http.client.HTTPConnection("127.0.0.1", watched.port, timeout=5)
            self.addCleanup(connection.close)
            connection.request("GET", path, headers={"Host": f"127.0.0.1:{watched.port}", "Accept-Language": language})
            return connection.getresponse().read().decode("utf-8")

        shown = get("/", "es")
        self.assertIn('id="watch"', shown)
        self.assertIn(page.WORDS["es"]["watch_waiting"], shown)
        self.assertEqual(json.loads(get("/state", "es")).get("watchText"), page.WORDS["es"]["watch_waiting"])
        at = 1_790_000_000.0
        for alert, language in (("loading", "es"), ("queue", "en"), ("started", "es")):
            snapshot.update(client="connected", phase="Lobby", alert=alert, at=at)
            words = page.WORDS[language]
            expected = " ".join((words["watch_connected"], words["watch_phase"].format(phase=words["phase_lobby"]),
                                 words["watch_last"].format(what=words["alert_" + alert],
                                                            time=time.strftime("%H:%M", time.localtime(at)))))
            self.assertEqual(json.loads(get("/state", language)).get("watchText"), expected)
            self.assertIn(html.escape(expected), get("/", language))
            self.assertIsNone(GAME_WORDS.search(expected), expected)
        self.assertNotIn('id="watch"', self.html())
        self.assertNotIn("watchText", json.loads(self.call("GET", "/state")[2]))

    def test_the_watcher_line_says_the_phase_while_connected_and_the_last_ping_in_both_languages(self):
        # Mutation: the ping line left out of watch_text. Red: /state ends at the phase, with no ping line.
        # Mutation: PreEndOfGame left out of the phase map. Red: it reads «Otro estado».
        at, ping_at = 1_790_000_000.0, 1_790_000_600.0
        snapshot = {"client": "connected", "phase": None, "alert": None, "at": None, "pingResult": None, "pingAt": None}
        self.page = page.PairingPage(self.state, watch=lambda: dict(snapshot))
        self.page.start()
        self.addCleanup(self.page.close)
        self.port, self.host = self.page.port, f"127.0.0.1:{self.page.port}"

        def served(accept):
            headers = {"Accept-Language": accept}
            return json.loads(self.call("GET", "/state", headers=headers)[2]).get("watchText"), self.html(accept)

        for index, (lang, accept) in enumerate((("es", "es-CO,es;q=0.9"), ("en", "en-US,en;q=0.9"))):
            connected, now, ping = WATCH_FRAMES[lang]
            words = page.WORDS[lang]
            # Connected with nothing read yet: no phase clause (pcpg-live Open item 5); the client's "None" is a read.
            snapshot.update(client="connected", phase=None, alert=None, at=None, pingResult=None, pingAt=None)
            state, shown = served(accept)
            self.assertEqual(state, connected)
            self.assertIn(f'<p id="watch" role="status">{html.escape(connected)}</p>', shown)
            for phase, names in PHASE_TEXTS.items():
                with self.subTest(lang=lang, phase=phase):
                    snapshot.update(client="connected", phase=phase, pingResult=None, pingAt=None)
                    expected = f"{connected} {now.format(names[index])}"
                    state, shown = served(accept)
                    self.assertEqual(state, expected)
                    self.assertIn(f'<p id="watch" role="status">{html.escape(expected)}</p>', shown)
            last = words["watch_last"].format(what=words["alert_started"],
                                              time=time.strftime("%H:%M", time.localtime(at)))
            for result, names in PING_TEXTS.items():
                with self.subTest(lang=lang, result=result):
                    snapshot.update(client="connected", phase="InProgress", alert="started", at=at, pingResult=result,
                                    pingAt=ping_at)
                    line = ping.format(names[index], time.strftime("%H:%M", time.localtime(ping_at)))
                    expected = f"{connected} {now.format(PHASE_TEXTS['InProgress'][index])} {last} {line}"
                    state, shown = served(accept)
                    self.assertEqual(state, expected)
                    self.assertIn(html.escape(expected), shown)
                    self.assertIsNone(GAME_WORDS.search(state), state)
            # Waiting: no phase, whatever the last one read was; the ping line still shows.
            snapshot.update(client="waiting", phase="Lobby", alert=None, at=None, pingResult="sent", pingAt=ping_at)
            line = ping.format(PING_TEXTS["sent"][index], time.strftime("%H:%M", time.localtime(ping_at)))
            self.assertEqual(served(accept)[0], f"{words['watch_waiting']} {line}")
        # The note under a panel's form keeps its own margin (pcpg-apply Open item 6).
        self.assertIn(".panel p{margin:0 0 16px;font-size:14px;line-height:20px;color:var(--ink2)}\n"
                      ".panel .note{margin:12px 0 0}\n", self.html())

    def post_in_two_sends(self, path, body, host=None, declared=None, length_headers=None):
        """A POST whose body follows its headers in a second send, as a browser may send a form: the status
        and the Connection header, or the name of the socket error met in their place."""
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            connection.putrequest("POST", path, skip_host=True, skip_accept_encoding=True)
            connection.putheader("Host", self.host if host is None else host)
            connection.putheader("Content-Type", "application/x-www-form-urlencoded")
            if length_headers is None:
                length_headers = [("Content-Length", str(len(body) if declared is None else declared))]
            for name, value in length_headers:
                connection.putheader(name, value)
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

    def serve(self, answer):
        """A new state and page over the same store, whose check answers answer()."""
        self.state = pairing.PairingState(self.store, lambda secret: self.checks.append(secret) or answer(),
                                          clock=FakeClock())
        self.page = page.PairingPage(self.state)
        self.page.start()
        self.addCleanup(self.page.close)
        self.port = self.page.port
        self.host = f"127.0.0.1:{self.port}"

    def status_of(self, method, path, form=None):
        """The status of the answer, or the name of the socket error met in its place."""
        try:
            return self.call(method, path, form=form)[0]
        except OSError as failure:
            return type(failure).__name__

    def test_a_post_with_no_usable_length_is_drained_then_answered_411_or_400_and_never_reset(self):
        # Mutation: the length refusal answered before the drain. Red: Windows resets the connections.
        secret = self.secret()
        body = b"x" * 3000
        chunked = b"%x\r\n" % len(body) + body + b"\r\n0\r\n\r\n"
        cases = [("Content-Length abc", [("Content-Length", "abc")], body, 400),
                 ("chunked, no length", [("Transfer-Encoding", "chunked")], chunked, 411),
                 ("Content-Length -5", [("Content-Length", "-5")], body, 400),
                 ("no length", [], body, 411)]
        for name, length_headers, sent, status in cases:
            with self.subTest(name=name):
                answers = [self.post_in_two_sends("/typed", sent, length_headers=length_headers) for _ in range(20)]
                self.assertEqual([answer for answer in answers if answer != (status, "close")], [])
        self.assertEqual(self.store.read(), config.Pairing(secret, None))
        self.assertEqual(self.checks, [])

    def test_a_declared_body_never_sent_is_dropped_at_the_timeout_and_its_thread_ends(self):
        # Mutation: the timeout removed from the Handler. Red: the socket is still open and unanswered at 12 s.
        baseline = threading.active_count()
        with socket.create_connection(("127.0.0.1", self.port), timeout=12) as raw:
            raw.sendall(f"POST /typed HTTP/1.1\r\nHost: {self.host}\r\nContent-Length: 65536\r\n\r\n".encode("ascii"))
            started = time.monotonic()
            try:
                received = raw.recv(4096)
            except OSError as failure:  # TimeoutError when the page never lets go, or a reset
                received = type(failure).__name__
            waited = time.monotonic() - started
            deadline = time.monotonic() + 2
            while threading.active_count() > baseline and time.monotonic() < deadline:
                time.sleep(0.01)
            threads = threading.active_count()
        self.assertEqual((received, 9 <= waited < 12, threads <= baseline), (b"", True, True))

    @unittest.skipIf(msvcrt is None, WINDOWS_ONLY)
    def test_a_post_that_meets_a_held_config_file_answers_303_and_the_page_says_so_in_both_languages(self):
        # Mutation: the ConfigError catch removed from the page's act. Red: RemoteDisconnected in place of 303.
        roads = [("/check", self.store.forget, lambda: worker.Linked(LINK_ID)),
                 ("/typed", self.store.forget, worker.Refused),
                 ("/forget", self.store.forget, worker.Refused),
                 ("/relink", lambda: self.store.set_typed(LINK_ID, SECRET), worker.Refused)]
        answers = []
        for path, arrange, answer in roads:
            arrange()
            self.serve(answer)
            for _ in range(3 if path == "/relink" else 0):
                self.state.record_ping(worker.Refused())
            before, shown_before = self.store.read(), CONFIG_WORDS["es"] in self.html()
            err = io.StringIO()
            with held(self.store.path), contextlib.redirect_stderr(err):
                status = self.status_of("POST", path, form={"linkId": LINK_ID, "secret": SECRET})
                shown = (CONFIG_WORDS["es"] in self.html(), html.escape(CONFIG_WORDS["en"]) in self.html("en"))
            answers.append((path, shown_before, status, *shown, err.getvalue(), self.store.read() == before))
        self.assertEqual(answers, [(path, False, 303, True, True, " [page] a request failed: ConfigError\n", True)
                                   for path, _, _ in roads])

    def test_quit_is_fenced_like_every_post_route_and_answers_the_stopped_page_in_both_languages(self):
        # Mutation: /quit answered before the token check. Red: a POST with no token stops the program.
        self.assertNotIn('action="/quit"', self.html())  # a page with nothing to stop has no quit
        self.assertEqual(self.call("POST", "/quit")[0], 404)
        quits = []
        self.page = page.PairingPage(self.state, on_quit=lambda: quits.append(True))
        self.page.start()
        self.addCleanup(self.page.close)
        self.port, self.host = self.page.port, f"127.0.0.1:{self.page.port}"
        hosts = ["evil.example", f"evil.example:{self.port}", f"127.0.0.1:{self.port + 1}", "127.0.0.1",
                 f"localhost.evil.example:{self.port}", f"[::1]:{self.port}", f"127.0.0.2:{self.port}", ""]
        refused = [self.call("POST", "/quit", host=host)[0] for host in hosts]
        refused += [self.call("POST", "/quit", token=token)[0]
                    for token in (False, "wrong", self.page.token[:-1], self.page.token + "x")]
        self.assertEqual((refused, quits), ([403] * 12, []))
        self.assertEqual((page.WORDS["es"]["quit"], page.WORDS["en"]["quit"]), ("Salir", "Quit"))
        for accept, lang in (("es-CO,es;q=0.9", "es"), ("en-US,en;q=0.9", "en")):
            with self.subTest(lang=lang):
                words = page.WORDS[lang]
                self.assertIn(f'<button type="submit">{words["quit"]}</button></form>', self.html(accept))
                status, headers, body = self.call("POST", "/quit", headers={"Accept-Language": accept})
                self.assertEqual((status, headers["content-type"], headers["connection"]),
                                 (200, "text/html; charset=utf-8", "close"))
                for name, value in FENCE.items():
                    self.assertEqual(headers.get(name), value)
                self.assertIn(f'<html lang="{lang}">', body)
                for key in ("stopped", "start_again"):
                    self.assertIn(html.escape(words[key]), body)
                self.assertIsNone(GAME_WORDS.search(body), body)
                self.assertNotIn(self.page.token, body)
        self.assertEqual(quits, [True, True])

    def test_quit_stops_the_program_even_when_its_answer_cannot_be_written(self):
        # Mutation: the finally removed, on_quit reached only after a written answer. Red: quits stays [].
        quits = []
        self.page = page.PairingPage(self.state, on_quit=lambda: quits.append(True))
        self.page.start()
        self.addCleanup(self.page.close)
        self.port, self.host = self.page.port, f"127.0.0.1:{self.page.port}"
        failures = [OSError("the answer cannot be written")]

        def render_stopped(lang):  # fails once, the way a write to a socket the browser closed fails
            raise failures.pop()

        self.page.render_stopped = render_stopped
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(ConnectionError):
            self.call("POST", "/quit")  # the handler closes the socket with no answer
        self.assertEqual((quits, failures), ([True], []))
        self.assertEqual(err.getvalue(), " [page] a request failed: OSError\n")  # its type, no traceback


if __name__ == "__main__":
    unittest.main()
