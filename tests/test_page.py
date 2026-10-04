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
import types
import unittest
import urllib.parse
from html.parser import HTMLParser
from pathlib import Path
from unittest import mock

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
# The update's words (lane pfupd), in the order of the brief.
UPDATE_KEYS = ("update_available", "update_install", "update_installing", "update_ready", "update_restart",
               "update_failed")
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
# The program's name (owner 2026-10-01): the title and the h1 of both documents; the sentence they read before is the
# tagline under the title row.
NAME = "PendiFy"
# The credit: the creator and the repository first in the top bar of every pairing page, left of the language switch
# and the theme button; the foot holds only «Salir», and the page draws it only beside a quit. The link is
# the page's one address out; it loads nothing, and the pins that allow no outside address take out exactly its start
# tag, so any other one still shows.
REPO = "https://github.com/ElkinDev/PendiFy"
CREDIT = {"es": "Creado por", "en": "Created by"}
CREDIT_LINK = f'<a href="{REPO}" target="_blank" rel="noopener noreferrer">'
# The words of a waiting page with a watcher and a quit button, in the order the page shows them; an entry that is not
# a key of WORDS is printed as it is, and None is the key as codes.display prints it. The card «Este PC» sits right
# after the watcher line (brief pcctl-r2, placement A); the page given no start with Windows draws no switch in it.
# The code is hidden until asked, its key under the mask, then the label of what the QR is for and the reveal; the
# lower part is one fold; the top bar names the creator and the repository before the language switch, and «Salir»
# closes the page. The QR's mask carries the instruction (qr_press) as text, and the label is two texts, its leading
# capitals and the rest of the same string (qr_lead, qr_rest; expect()).
PAGE_ORDER = (NAME, "credit", "Niklerk", "·", "github.com/ElkinDev/PendiFy", NAME, "title", "intro", "state_waiting",
              "watch_waiting", "this_pc", "pause", "qr_press", "code_label", None, "qr_lead", "qr_rest", "show_code",
              "check", "log_title",
              "log_help", "fold", "typed_title", "link_id_label", "secret_label", "save", "forget", "forget_sentence",
              "forget", "quit")
# The texts of PAGE_ORDER before the language switch: the title tag and the credit.
BEFORE_SWITCH = 5
# The language switch's two labels, the same in both languages, sit in the header between the title tag and the
# title row (lane pclang, owner report OR-96).
SWITCH_LABELS = ["ES", "EN"]
# The reveal's two labels, the fold's summary and the words of the sponsored aside.
SHOW = {"es": "Mostrar el código", "en": "Show the code"}
HIDE = {"es": "Ocultar el código", "en": "Hide the code"}
# The label over the reveal (owner 2026-10-02 09:5x, his words): what the pairing QR is for, no game and no maker.
QR_FOR = {"es": "ESCANEA ESTE CÓDIGO QR con tu teléfono para recibir notificaciones cuando empiece la partida y "
                "se acepte la cola.",
          "en": "SCAN THIS QR CODE with your phone to get notifications when the match starts and the queue is "
                "accepted."}
FOLD = {"es": "Más opciones: escribir los valores del enlace u olvidar este PC",
        "en": "More options: type the link values, or forget this PC"}
# The scan sentence the card held before the label became the QR's one sentence (owner 2026-10-02 10:3x, his B):
# gone from both tables and from the page.
SCAN = {"es": "Escanea este código con la cámara del teléfono donde tienes tu cuenta y confirma el enlace.",
        "en": "Scan this code with the camera of the phone that holds your account and confirm the link."}
# The name of the pixelated mask over the QR's modules, for a screen reader.
QR_MASK = {"es": "Código oculto", "en": "Hidden code"}
# The instruction on both masks of the pairing QR (owner 2026-10-02 12:4x, his correction: both carry option C's
# words), and the label's leading capitals, its lead line (OR-105); the label's words are QR_FOR, unchanged.
QR_PRESS = {"es": "Pulsa Mostrar el código", "en": "Press Show the code"}
# The reveal's binding, its toggle, and the masks' after it: a press on either mask only shows (lane pfpress);
# the key mask prevents the default of Enter and Space alike, so Enter's keypress never reaches the reveal.
# The reveal also prevents the default of a repeat Enter or Space only, so a key held after a mask press moved the
# focus to it never toggles the code back, while a fresh press of the reveal keeps its toggle (review fix2 MINOR 1).
REVEAL_BINDING = ("if(r)r.addEventListener('click',()=>showKey(k.dataset.shown!=='true'));"
                  "if(r)r.addEventListener('keydown',e=>{if(e.repeat&&(e.key==='Enter'||e.key===' '))e.preventDefault();});")
MASK_BINDING = ("document.querySelectorAll('.qr-mask').forEach(m=>m.addEventListener('click',()=>showKey(true)));"
                "document.querySelectorAll('.mask').forEach(m=>{const press=()=>{showKey(true);r.focus();};"
                "m.addEventListener('click',press);m.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){"
                "e.preventDefault();press();}});});")
QR_LEAD = {"es": "ESCANEA ESTE CÓDIGO QR", "en": "SCAN THIS QR CODE"}


def expect(key, words, secret):
    """A PAGE_ORDER entry as the page prints it: the key's words, the key as codes.display prints it for None, and
    the label's lead line and its rest for "qr_lead" and "qr_rest" (one string in the tables, two texts)."""
    if key is None:
        return codes.display(secret)
    if key in ("qr_lead", "qr_rest"):
        lead = next(lead for lead in QR_LEAD.values() if words["qr_for"].startswith(lead + " "))
        return lead if key == "qr_lead" else words["qr_for"][len(lead) + 1:]
    return words.get(key, key)


def label_markup(lang):
    """The label as the page draws it: its leading capitals as the lead line, then the rest of the same string."""
    lead, rest = QR_LEAD[lang], QR_FOR[lang][len(QR_LEAD[lang]) + 1:]
    return f'<p class="qr-for" id="qr-for"><b>{html.escape(lead)}</b> {html.escape(rest)}</p>'
SPONSOR = {"es": {"by": "Patrocinado por Pendiapp.com", "cap": "Escanéalo para abrir pendiapp.com",
                  "qr": "Código QR de pendiapp.com",
                  "note": "Este PC ya está enlazado, por eso ya no se muestra su código. «Olvidar este PC», en «Más "
                          "opciones», crea un código nuevo para enlazar."},
           "en": {"by": "Sponsored by Pendiapp.com", "cap": "Scan it to open pendiapp.com",
                  "qr": "QR code of pendiapp.com",
                  "note": "This PC is already linked, so its code is no longer shown. «Forget this PC», under «More "
                          "options», makes a new code to link."}}
# The mask over the key: three groups of the same four 8 by 8 heads, whatever the key, named for a screen reader.
MASK = re.compile(r'<span class="mask" role="button" tabindex="0" aria-label="([^"]*)">(<span class="grp">((?:<svg class="mx" '
                  r'viewBox="0 0 8 8" width="16" height="16" shape-rendering="crispEdges" aria-hidden="true">'
                  r'(?:(?!</?svg).)*</svg>){4})</span>)\2\2</span>')
# Each figure's own move, one after another, one second apart, once every 15 s, only while the system asks for no
# less motion; the hop is gone.
MOVES = ("@media (prefers-reduced-motion:no-preference){\n"
         ".px{transform-origin:50% 100%}\n"
         ".px:nth-child(1){animation:px-archer 15s steps(1,end) infinite}\n"
         ".px:nth-child(2){animation:px-knight 15s steps(1,end) infinite}\n"
         ".px:nth-child(3){animation:px-mage 15s steps(1,end) infinite}\n"
         ".px:nth-child(4){animation:px-creature 15s steps(1,end) infinite}\n"
         "@keyframes px-archer{0%{transform:none}4%{transform:scaleX(-1)}10%,100%{transform:none}}\n"
         "@keyframes px-knight{0%{transform:none}16.67%{transform:translateX(3px)}19.67%{transform:translateX(6px)}"
         "22.67%{transform:translateX(3px)}25.67%,100%{transform:none}}\n"
         "@keyframes px-mage{0%{transform:none}32.33%{transform:translateY(-3px)}35.33%{transform:translateY(-6px)}"
         "44.33%{transform:translateY(-3px)}47.33%,100%{transform:none}}\n"
         "@keyframes px-creature{0%{transform:none}54%{transform:translate(3px,-6px)}"
         "56%{transform:translate(3px,-3px)}58%{transform:translate(0,-6px)}60%{transform:translate(0,-3px)}"
         "62%{transform:translate(-3px,-6px)}64%{transform:translate(-3px,-3px)}66%,100%{transform:none}}}\n")


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


def icon_uri():
    return ICON_PREFIX + support.module("icon").PNG_32


def icon_tags():
    """The icon's two tags, each only with the icon's own data URI; the same shape with another payload is not it."""
    uri = re.escape(icon_uri())
    return re.compile(f'<link rel="icon" type="image/png" href="{uri}">'
                      f'|<img alt="" width="32" height="32" src="{uri}">')


def without_icon(document):
    """The document with the icon's own link and image taken out, for the pins that allow no other image."""
    return icon_tags().sub("", document)


def without_credit(document):
    """The document with the top bar's one link out taken out, its start tag only, once."""
    return document.replace(CREDIT_LINK, "", 1)


def outside_references(document):
    """Every href and url() of a page that is neither a fragment of the page itself nor the QR plate's data URI
    nor the icon's nor the top bar's one link to the repository."""
    document = without_credit(without_icon(document))
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
        self.icon = icon_uri()
        self.feed(document)
        self.close()

    def handle_starttag(self, tag, attrs):
        if tag == "svg" and ("class", "qr") in attrs:
            self.qr_places.append(list(self.open_details))
        self.data_places += [list(self.open_details) for _, value in attrs
                             if (value or "").strip().lower().startswith("data:") and value != self.icon]
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
RUN_SECRET = "0123456789abcdef" * 2  # a test vector, never a run file's


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
        for value in ('<svg class="qr"', secret, codes.display(secret), LINK_ID, codes.display(LINK_ID),
                      'action="/relink"'):
            self.assertNotIn(value, without_icon(linked))
        # The icon's own data URI stays on every page (brief pcico); no other data: may, in any case of the scheme.
        self.assertNotIn("data:", without_icon(linked).lower())
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

    def test_only_the_icon_itself_is_taken_out_of_the_linked_page(self):
        # A second 32 px image in the icon's tag shape but with another payload is not the icon: the linked page's
        # "no data: outside the icon" pin must still see it.
        self.call("POST", "/typed", form={"linkId": LINK_ID, "secret": self.secret()})
        linked = self.html()
        extra = '<img alt="" width="32" height="32" src="data:image/png;base64,AAAA">'
        added = linked.replace("</body>", extra + "</body>", 1)
        self.assertEqual(added.count(extra), 1)
        self.assertIn("data:", without_icon(added))
        self.assertNotIn("data:", without_icon(linked))

    def test_only_the_icon_itself_is_exempt_from_the_key_places(self):
        # A PNG data URI outside every details is not the icon: KeyPlace must report it as a place outside them.
        secret = self.secret()
        shown = self.html()
        extra = '<object data="data:image/png;base64,AAAA"></object>'
        added = shown.replace("</body>", extra + "</body>", 1)
        self.assertEqual(added.count(extra), 1)
        self.assertEqual(KeyPlace(added, codes.display(secret)).data_places, [[], []])
        self.assertEqual(KeyPlace(shown, codes.display(secret)).data_places, [[]])

    def data_places_with(self, extra):
        """KeyPlace's data_places on the unlinked page with `extra` added outside every details."""
        shown = self.html()
        added = shown.replace("</body>", extra + "</body>", 1)
        self.assertEqual(added.count(extra), 1)
        return KeyPlace(added, codes.display(self.secret())).data_places

    def test_a_data_uri_with_a_leading_space_in_a_poster_is_a_place_outside_the_details(self):
        # A browser strips the space before the scheme, so the poster loads the PNG.
        self.assertEqual(self.data_places_with('<video poster=" data:image/png;base64,AAAA"></video>'), [[], []])

    def test_a_data_uri_with_a_leading_space_in_a_table_background_is_a_place_outside_the_details(self):
        self.assertEqual(self.data_places_with('<table background=" data:image/png;base64,AAAA"></table>'),
                         [[], []])

    def test_a_data_uri_with_an_upper_case_scheme_is_a_place_outside_the_details(self):
        # A browser folds the scheme's case, so DATA: is data:.
        self.assertEqual(self.data_places_with('<video poster="DATA:image/png;base64,AAAA"></video>'), [[], []])

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
                self.assertNotIn("http", without_credit(shown))
                self.assertIsNone(re.search(r"\bsrc\s*=|<img|<link|@import", without_icon(shown)))
                self.assertEqual(outside_references(shown), [])
                state = json.loads(self.call("GET", "/state", headers={"Accept-Language": language} if language
                                             else None)[2])
                self.assertEqual(state["text"], page.WORDS[expected]["state_waiting"])

    def test_the_page_loads_nothing_from_outside_and_every_word_keeps_its_one_place(self):
        # Mutation: the label left out of the QR's card. Red: the words miss WORDS["es"]["qr_for"].
        # Mutation: a web font imported by the style. Red: "@import" and an http address on the page.
        secret = self.secret()
        self.page = page.PairingPage(self.state, watch=lambda: {"client": "waiting"}, on_quit=lambda: None,
                                     events=lambda: [])
        self.page.start()
        self.addCleanup(self.page.close)
        self.port, self.host = self.page.port, f"127.0.0.1:{self.page.port}"
        symbol = qr.encode(qr.pairing_address(secret).encode("ascii")).modules
        for accept, lang in (("es-CO,es;q=0.9", "es"), ("en-US,en;q=0.9", "en")):
            with self.subTest(lang=lang):
                shown, words = self.html(accept), page.WORDS[lang]
                expected = [expect(key, words, secret) for key in PAGE_ORDER]
                self.assertEqual(PageText(shown).texts,
                                 expected[:BEFORE_SWITCH] + SWITCH_LABELS + expected[BEFORE_SWITCH:])
                self.assertEqual(shown.count(f'data-closed="{html.escape(words["state_closed"])}"'), 1)
                for outside in ("<link", "src=", "@import", "@font-face", "http"):
                    self.assertNotIn(outside, without_credit(without_icon(shown)))
                self.assertEqual(outside_references(shown), [])
                # The pairing address is only in the QR's own modules: the page draws the scene of the encoder's
                # symbol over the one plate (SceneDecodeTest reads it with ZXing).
                drawn = re.findall(r'<svg class="qr".*?</svg>', shown, re.S)
                self.assertEqual(drawn, [qr.scene_svg(symbol, plate_almena.PLATE_DATA_URI, labelledby="qr-for",
                                                      mask=self.page.mask,
                                                      mask_words=html.escape(words["qr_press"]))])
        self.assertEqual(self.call("POST", "/typed", form={"linkId": "WXYZ6789ABC", "secret": SECRET})[0], 303)
        refused = list(PAGE_ORDER)
        refused.insert(refused.index("save") + 1, "typed_refused")
        expected = [expect(key, page.WORDS["es"], secret) for key in refused]
        self.assertEqual(PageText(self.html()).texts,
                         expected[:BEFORE_SWITCH] + SWITCH_LABELS + expected[BEFORE_SWITCH:])

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

    def test_the_title_and_the_h1_read_the_name_on_every_page_and_the_old_sentence_is_the_tagline(self):
        # Mutation: the h1 left as the sentence. Red: the row's h1 is not the name. Mutation: the img placed after the
        # h1. Red: the row does not open with the img. Mutation: the stopped page's title left as the sentence. Red:
        # its <title> is not the name.
        uri = icon_uri()
        self.assertEqual((page.WORDS["es"]["title"], page.WORDS["en"]["title"]),
                         ("Avisos de este PC", "Alerts from this PC"))
        langs = (("es-CO,es;q=0.9", "es"), ("en-US,en;q=0.9", "en"))
        pages = {"waiting": {lang: self.html(accept) for accept, lang in langs}}
        self.call("POST", "/typed", form={"linkId": LINK_ID, "secret": self.secret()})
        pages["linked"] = {lang: self.html(accept) for accept, lang in langs}
        for _ in range(3):
            self.state.record_ping(worker.Refused())
        pages["relink"] = {lang: self.html(accept) for accept, lang in langs}
        for name, documents in pages.items():
            for lang, shown in documents.items():
                with self.subTest(page=name, lang=lang):
                    tagline = html.escape(page.WORDS[lang]["title"])
                    self.assertEqual((shown.count("<img"), shown.count("<h1>"), shown.count("<title>")), (1, 1, 1))
                    self.assertIn(f"<title>{NAME}</title>", shown)
                    self.assertIn(f'<div class="title-row"><img alt="" width="32" height="32" src="{uri}"><h1>{NAME}'
                                  f'</h1></div><p class="tagline">{tagline}</p><p class="intro">', shown)
        self.assertIn('action="/relink"', pages["relink"]["es"])
        for lang in ("es", "en"):
            with self.subTest(page="stopped", lang=lang):
                stopped = self.page.render_stopped(lang)
                self.assertEqual((stopped.count("<title>"), stopped.count("<h1>")), (1, 1))
                self.assertIn(f"<title>{NAME}</title>", stopped)
                self.assertIn(f"<h1>{NAME}</h1>", stopped)
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
            document = without_credit(document)
            sources = re.findall(r'\b(?:src|href)\s*=\s*"([^"]*)"', document)
            self.assertIn(icon_uri(), sources)
            self.assertEqual([source for source in sources if not source.startswith(("data:", "#"))], [])
            self.assertEqual([source for source in sources if source.startswith(("http", "//"))], [])
            self.assertNotIn("http", document)

    def test_the_code_is_shown_unfolded_and_its_key_sits_under_twelve_heads_until_the_reveal(self):
        # Mutation: the details kept over the QR, as on main. Red: the QR and its plate sit inside a details.
        # Mutation: the key rendered shown. Red: data-shown is not "false". Mutation: a head drawn from the key's
        # characters. Red: the masks of two keys differ. Mutation: the timer's 60000 turned to 600000. Red: the
        # script pin misses it.
        first = self.secret()
        masks = []
        for accept, lang in (("es-CO,es;q=0.9", "es"), ("en-US,en;q=0.9", "en")):
            with self.subTest(lang=lang):
                shown, words, key = self.html(accept), page.WORDS[lang], codes.display(first)
                self.assertEqual(shown.count(key), 1)
                self.assertNotIn(first, shown)
                place = KeyPlace(shown, key)
                self.assertEqual((place.places, place.qr_places, place.data_places, place.details),
                                 ([[]], [[]], [[]], [{"class": "fold"}]))
                self.assertEqual(shown.count("data:image/webp;base64,"), 1)
                cards = re.findall(r'<figure class="key-card">.*?</figure>', shown, re.S)
                self.assertEqual(len(cards), 1)
                card = cards[0]
                self.assertTrue(card.startswith('<figure class="key-card"><svg class="qr"'), card[:120])
                self.assertEqual((card.count("<details"), card.count("<summary")), (0, 0))
                mask = MASK.search(card)
                self.assertIsNotNone(mask, "no mask of three groups of the same four heads in the code card")
                self.assertEqual(mask.group(1), html.escape(QR_PRESS[lang]))
                self.assertEqual(mask.group(0).count('<svg class="mx"'), 12)
                self.assertEqual(len(set(re.findall(r'<svg class="mx".*?</svg>', mask.group(3)))), 4)
                self.assertTrue(card.endswith(
                    f'<p class="key" data-shown="false"><span class="key-label">{html.escape(words["code_label"])}</span> '
                    f'<span class="key-val">{mask.group(0)}<span class="code" id="code">{key}</span></span></p>'
                    f'{label_markup(lang)}'
                    f'<button type="button" class="reveal" aria-controls="code" data-show="{SHOW[lang]}" '
                    f'data-hide="{HIDE[lang]}">{SHOW[lang]}</button>{page._PARTY}</figure>'), card[-600:])
                # The key's text is only the code's own: never in an attribute, a label or a name.
                values = re.findall(r'=\s*"([^"]*)"', shown)
                self.assertEqual([value for value in values if key in value or first in value], [])
                masks.append(mask.group(0))
        self.assertEqual(len(masks), 2, "a language's code card holds no mask")
        self.assertEqual(masks[0], masks[1].replace(html.escape(QR_PRESS["en"]), html.escape(QR_PRESS["es"])))
        # The same heads whatever the key: the new key «Olvidar este PC» makes is masked by the same bytes.
        self.assertEqual(self.call("POST", "/forget")[0], 303)
        again = self.html("es-CO,es;q=0.9")
        self.assertNotEqual(self.secret(), first)
        self.assertIn(f'<span class="code" id="code">{codes.display(self.secret())}</span>', again)
        self.assertEqual([found.group(0) for found in MASK.finditer(again)], masks[:1])
        for lang in ("es", "en"):
            self.assertEqual(tuple(page.WORDS[lang].get(name) for name in ("show_code", "hide_code")),
                             (SHOW[lang], HIDE[lang]))
            # The key's mask is named by the press words (lane pfpress); its old name left the tables.
            self.assertNotIn("mask", page.WORDS[lang])
        # The reveal shows the key and flips its label; 60 s after a show the key is masked again; one timer,
        # cleared on every press.
        self.assertIn("const k=document.querySelector('.key'),r=document.querySelector('.reveal');let hide;"
                      "function showKey(on){k.dataset.shown=String(on);r.textContent=on?r.dataset.hide:r.dataset.show;"
                      "clearTimeout(hide);if(on)hide=setTimeout(()=>showKey(false),60000);}"
                      "if(r)r.addEventListener('click',()=>showKey(k.dataset.shown!=='true'));", page._SCRIPT)
        self.assertEqual(page._SCRIPT.count("setTimeout("), 1)
        self.assertNotIn("details", page._SCRIPT)
        self.call("POST", "/typed", form={"linkId": LINK_ID, "secret": self.secret()})
        linked = self.html()
        self.assertEqual([marker for marker in ('class="key"', 'class="reveal"', 'class="mask"') if marker in linked],
                         [])

    def test_a_press_on_either_mask_shows_the_code_and_the_reveal_keeps_its_toggle(self):
        # Lane pfpress (OR-108): «Pulsa Mostrar el código» does what the reveal does. Mutation: the masks left
        # unbound. Red: the binding is not after the reveal's. Mutation: a mask's press bound to the toggle. Red: two
        # toggles and one show. Mutation: Space left to scroll the page, or the Space-only guard back (review fix1
        # MAJOR 1: Enter's keypress goes to the focused reveal and hides the code). Red: the binding differs, the
        # guard is present, and the keydown's preventDefault is not the one before press(). Mutation:
        # the pointer rule dropped. Red: the style misses it. Mutation: the key mask's press leaves focus where it
        # was (review MINOR 3). Red: no r.focus() after its showKey(true). Mutation: the QR's mask given the
        # keydown (review MINOR 2: it is an image, not a control). Red: the binding differs.
        self.assertIn(REVEAL_BINDING + MASK_BINDING, page._SCRIPT)
        self.assertEqual(page._SCRIPT.count("showKey(true);r.focus();"), 1)
        # Two keydown listeners: the reveal's, which only stops a held key's repeats (review fix2 MINOR 1:
        # mutations dropping e.repeat or the listener red REVEAL_BINDING), and the key mask's, cut out below.
        self.assertEqual(page._SCRIPT.count("addEventListener('keydown'"), 2)
        self.assertEqual(page._SCRIPT.count("e.repeat&&"), 1)
        keydown = page._SCRIPT.split("addEventListener('keydown'")[2].split("}});", 1)[0]
        self.assertEqual(keydown.count("e.preventDefault();press();"), 1)
        self.assertEqual(keydown.count("preventDefault"), 1)
        self.assertNotIn("if(e.key===' ')e.preventDefault();", page._SCRIPT)
        self.assertEqual(page._SCRIPT.count("showKey(k.dataset.shown!=='true')"), 1)
        self.assertEqual(page._SCRIPT.count("showKey(true)"), 2)
        shown = self.html("es-CO,es;q=0.9")
        style = re.search(r"<style>(.*?)</style>", shown, re.S).group(1)
        self.assertEqual(style.count(".qr-mask,.mask{cursor:pointer}"), 1)
        noscript = re.search(r"<noscript><style>([^<]*)</style></noscript>", shown).group(1)
        self.assertNotIn("cursor", noscript)

    def test_the_pairing_qr_s_modules_are_masked_until_the_code_is_shown_and_the_drawings_stay_visible(self):
        # Mutation: the whole QR hidden, as on a0572b4. Red: no modules group, and a rule hides .qr. Mutation: the
        # plate drawn inside the modules group. Red: the modules group comes first. Mutation: a rule hiding the party
        # at data-shown false. Red: a hiding rule's target is .party.
        hiding = re.compile(r"visibility:hidden|display:none|opacity:0\b")
        drawing = re.compile(r"\.(?:qr|party|px)(?![-\w])|^(?:svg|use|image|figure|\.key-card)(?![-\w])")
        for accept in ("es-CO,es;q=0.9", "en-US,en;q=0.9"):
            with self.subTest(accept=accept):
                shown = self.html(accept)
                card = re.findall(r'<figure class="key-card">.*?</figure>', shown, re.S)[0]
                drawn = re.findall(r'<svg class="qr".*?</svg>', card, re.S)[0]
                self.assertEqual(drawn.count('<g class="modules"'), 1)
                self.assertEqual(drawn.count('<g class="qr-mask"'), 1)
                # The plate is drawn first, outside both groups; the party is the card's, outside the QR.
                self.assertLess(drawn.index('<use href="#qp"/>'), drawn.index('<g class="modules"'))
                self.assertEqual(card.count('<div class="party" aria-hidden="true"><svg class="px"'), 1)
                self.assertNotIn('class="px"', drawn)
                style = re.search(r"<style>(.*?)</style><noscript>", shown, re.S).group(1)
                noscript = re.search(r"<noscript><style>([^<]*)</style></noscript>", shown).group(1)
                self.assertIn('.key-card:has(.key[data-shown="false"]) .modules{visibility:hidden}', style)
                self.assertIn('.key-card:has(.key[data-shown="true"]) .qr-mask{visibility:hidden}', style)
                for sheet in (style, noscript):
                    for selectors, body in re.findall(r"([^{}]+)\{([^{}]*)\}", sheet):
                        if hiding.search(body):
                            for selector in selectors.split(","):
                                target = re.split(r"[\s>+~]+", selector.strip())[-1]
                                self.assertIsNone(drawing.search(target), selector + "{" + body + "}")

    def test_the_qr_mask_is_the_same_for_every_key_and_holds_no_module_of_the_code(self):
        # Mutation: the mask drawn from the symbol's own modules. Red: two keys give two masks, and the mask differs
        # from the one over a symbol with no dark module. Mutation: no mask, as on a0572b4. Red: none found.
        mask = re.compile(r'<g class="qr-mask".*?</g>(?=</svg>)', re.S)
        modules = re.compile(r'<g class="modules".*?</g>(?=<g class="qr-mask")', re.S)
        served = []
        for _ in range(2):
            served.append((self.secret(), self.html("es-CO,es;q=0.9")))
            self.assertEqual(self.call("POST", "/forget")[0], 303)
        self.assertNotEqual(served[0][0], served[1][0])
        masks = [mask.findall(shown) for _, shown in served]
        self.assertEqual([len(found) for found in masks], [1, 1])
        self.assertEqual(masks[0][0], masks[1][0])
        self.assertNotEqual(*[modules.findall(shown)[0] for _, shown in served])
        self.assertNotIn("<use", masks[0][0])
        label = html.escape(page.WORDS["es"]["qr_press"])
        for secret, _ in served:
            symbol = qr.encode(qr.pairing_address(secret).encode("ascii")).modules
            blank = [[False] * len(symbol) for _ in symbol]
            self.assertEqual(mask.findall(qr.scene_svg(blank, plate_almena.PLATE_DATA_URI, mask=self.page.mask,
                                                       mask_words=label)), masks[0])
        # The mask over a symbol with no dark module is the served one (above), so no module of the code reaches it.

    def test_the_label_is_the_qr_s_accessible_name_and_the_scan_sentence_is_gone(self):
        # Mutation: the scan sentence kept, as on a0572b4. Red: the tables hold "scan" and the QR is named by it.
        self.assertNotIn(".scan{", page._STYLE)
        for accept, lang in (("es-CO,es;q=0.9", "es"), ("en-US,en;q=0.9", "en")):
            with self.subTest(lang=lang):
                self.assertNotIn("scan", page.WORDS[lang])
                self.assertEqual(page.WORDS[lang].get("qr_press"), QR_PRESS[lang])
                self.assertNotIn("qr_mask", page.WORDS[lang])
                shown = self.html(accept)
                card = re.findall(r'<figure class="key-card">.*?</figure>', shown, re.S)[0]
                self.assertIn(' aria-labelledby="qr-for"', re.search(r'<svg class="qr"[^>]*>', card).group(0))
                self.assertEqual(shown.count('id="qr-for"'), 1)
                self.assertIn('<p class="qr-for" id="qr-for">', card)
                self.assertIn(f'<g class="qr-mask" role="img" aria-label="{html.escape(QR_PRESS[lang])}">', card)
                self.assertNotIn(html.escape(QR_MASK[lang]), shown)
                for gone in ('class="scan"', 'id="scan"', html.escape(SCAN["es"]), html.escape(SCAN["en"])):
                    self.assertNotIn(gone, shown)

    def test_the_pairing_qr_is_hidden_until_the_code_is_shown(self):
        # Rewritten for round 2: the QR's modules, never the whole QR. Mutation: the modules' hiding rule dropped.
        # Red: the style misses it. Mutation: the modules left out of the noscript rule. Red: the no-script road keeps
        # them hidden. Mutation: the QR drawn outside the card whose key carries data-shown. Red: the card does not
        # start with the QR.
        rule = '.key-card:has(.key[data-shown="false"]) .modules'
        for accept in ("es-CO,es;q=0.9", "en-US,en;q=0.9"):
            with self.subTest(accept=accept):
                shown = self.html(accept)
                cards = re.findall(r'<figure class="key-card">.*?</figure>', shown, re.S)
                self.assertEqual(len(cards), 1)
                self.assertTrue(cards[0].startswith('<figure class="key-card"><svg class="qr"'), cards[0][:120])
                self.assertEqual(cards[0].count('<svg class="qr"'), 1)
                self.assertEqual(cards[0].count('<p class="key" data-shown="false">'), 1)
                style = re.search(r"<style>(.*?)</style><noscript>", shown, re.S).group(1)
                self.assertIn(rule + "{visibility:hidden}", style)
                self.assertIn('.key-card:has(.key[data-shown="true"]) .qr-mask{visibility:hidden}', style)
                self.assertNotIn(".qr{visibility:hidden}", style)
                noscript = re.search(r"<noscript><style>([^<]*)</style></noscript>", shown).group(1)
                rules = {selector.strip(): body for selectors, body in re.findall(r"([^{}]+)\{([^{}]*)\}", noscript)
                         for selector in selectors.split(",")}
                self.assertEqual(rules.get(rule), "visibility:visible")
                self.assertEqual(rules.get(".qr-mask"), "display:none")
        # The same data-shown and the same timer as the key: the script is the key's own, unchanged.
        self.assertIn("function showKey(on){k.dataset.shown=String(on);", page._SCRIPT)
        self.assertEqual(page._SCRIPT.count("setTimeout("), 1)

    def test_the_label_says_what_the_qr_is_for(self):
        # Mutation: the label left out of a word table. Red: the words differ. Mutation: the label drawn after the
        # reveal. Red: the card misses it before the button. Mutation: the label kept on the linked page. Red: the
        # linked page holds it.
        for accept, lang in (("es-CO,es;q=0.9", "es"), ("en-US,en;q=0.9", "en")):
            with self.subTest(lang=lang):
                self.assertEqual(page.WORDS[lang].get("qr_for"), QR_FOR[lang])
                shown = self.html(accept)
                card = re.findall(r'<figure class="key-card">.*?</figure>', shown, re.S)[0]
                self.assertIn(f'</p>{label_markup(lang)}<button type="button" class="reveal"', card)
                self.assertEqual(shown.count(label_markup(lang)), 1)
                self.assertIsNone(GAME_WORDS.search(QR_FOR[lang]))
        self.assertIn(".qr-for{margin:0}", page._STYLE)
        self.assertNotIn(".qr-for", page._NOSCRIPT_STYLE)
        self.call("POST", "/typed", form={"linkId": LINK_ID, "secret": self.secret()})
        for accept, lang in (("es-CO,es;q=0.9", "es"), ("en-US,en;q=0.9", "en")):
            with self.subTest(page="linked", lang=lang):
                linked = self.html(accept)
                self.assertEqual([marker for marker in ('class="qr-for"', html.escape(QR_LEAD[lang]))
                                  if marker in linked], [])

    def test_the_page_draws_the_bust_or_the_board_as_told(self):
        # Lane pfmaskimpl (OR-104, owner 2026-10-02 12:4x): the mask is the bust or the chessboard, both with the
        # instruction as plain text and named by it. Mutation: the board drawn for "bust". Red: the bust's mask holds
        # the board's cells. Mutation: the words left off the board. Red: the board's mask holds no text. Mutation:
        # the mask named by the hidden-code word. Red: the aria-label differs from the instruction.
        mask = re.compile(r'<g class="qr-mask".*?</g>(?=</svg>)', re.S)
        self.assertIn(".qm-1{fill:var(--brand)}.qm-2{fill:var(--tonal)}", page._STYLE)
        self.assertIn(f".qm-say{{fill:var(--px-line);stroke:{qr.GROUND};stroke-width:10px;stroke-linejoin:round;"
                      "paint-order:stroke;font:600 28px system-ui,sans-serif;text-anchor:middle;"
                      "dominant-baseline:central}.qm-board{font-size:32px}", page._STYLE)
        for name in qr.MASKS:
            drawing = page.PairingPage(self.state, mask=name)
            self.assertEqual(drawing.mask, name)
            for lang in ("es", "en"):
                with self.subTest(mask=name, lang=lang):
                    shown = drawing.render(lang)
                    drawn = re.findall(r'<svg class="qr".*?</svg>', shown, re.S)
                    self.assertEqual(len(drawn), 1)
                    found = mask.findall(drawn[0])
                    self.assertEqual(len(found), 1)
                    words = html.escape(QR_PRESS[lang])
                    self.assertTrue(found[0].startswith(f'<g class="qr-mask" role="img" aria-label="{words}">'),
                                    found[0][:120])
                    self.assertEqual(re.findall(r'<text class="([^"]*)"[^>]*>([^<]*)</text>', found[0]),
                                     [("qm-say" if name == "bust" else "qm-say qm-board", words)])
                    cells = re.findall(r'<path class="qm-([12])" d="([^"]*)"/>', found[0])
                    if name == "bust":
                        self.assertIn(' scale(10.5)" shape-rendering="crispEdges"><path class="pl" d="M14 3h5v1h-5z',
                                      found[0])
                        self.assertEqual(cells, [])
                    else:
                        self.assertEqual([(tone, d.count("z")) for tone, d in cells], [("1", 32), ("2", 32)])
                        self.assertNotIn('class="pl"', found[0])
                    self.assertNotIn(html.escape(QR_MASK[lang]), shown)
                    for gone in ("<rect", "rx=", "<image", "<use"):
                        self.assertNotIn(gone, found[0])

    def test_an_unset_mask_is_picked_from_the_two_at_construction(self):
        # Lane pfmaskimpl (owner 12:4x): one random pick per start, the same for every render of that page.
        # Mutation: the pick made at every render. Red: the patched choice is asked again and runs out. Mutation: the
        # bust always. Red: "board" never reaches the render. Mutation: the flat fallback drawn with its own mask.
        # Red: the fallback's kind differs from the scene's.
        picks = iter(qr.MASKS)
        asked = []

        def choice(names):
            asked.append(tuple(names))
            return next(picks)

        bust = '<path class="pl" d="M14 3h5v1h-5z'
        for name in qr.MASKS:
            with self.subTest(mask=name), mock.patch.object(page.random, "choice", choice):
                drawing = page.PairingPage(self.state)
                self.assertEqual(drawing.mask, name)
                renders = [drawing.render(lang) for lang in ("es", "en")]
                with mock.patch.object(page.qr, "scene_svg", return_value=None):  # the flat qr.svg fallback
                    renders += [drawing.render(lang) for lang in ("es", "en")]
                kinds = [("bust" if bust in shown else "board" if 'class="qm-1"' in shown else None,
                          'viewBox="0 0 576 576"' in shown,
                          html.escape(QR_PRESS[lang]) in re.search(r'<svg class="qr".*?</svg>', shown, re.S).group(0))
                         for shown, lang in zip(renders, ("es", "en", "es", "en"))]
                self.assertEqual(kinds, [(name, True, True), (name, True, True), (name, False, True),
                                         (name, False, True)])
        self.assertEqual(asked, [qr.MASKS, qr.MASKS])
        # Unpatched, the standard module's pick reaches both names over a few starts.
        self.assertEqual({page.PairingPage(self.state).mask for _ in range(32)}, set(qr.MASKS))

    def test_the_label_block_carries_the_lead_line_and_the_rest(self):
        # Lane pfmaskimpl (OR-105): the label with impact, the #state linked block's recipe, its leading capitals as a
        # lead line and the rest under it, the owner's words unchanged. Mutation: the lead line at the body's size.
        # Red: the rule differs. Mutation: the split after the first word. Red: the markup differs.
        self.assertIn(".key-card .qr-for{justify-self:stretch;padding:16px 20px;border-radius:12px;"
                      "background:var(--tonal);color:var(--on-tonal);font-size:16px;line-height:24px;"
                      "font-weight:500}", page._STYLE)
        self.assertIn(".key-card .qr-for b{display:block;margin:0 0 2px;font-size:20px;line-height:28px;"
                      "font-weight:600;letter-spacing:.02em}", page._STYLE)
        for accept, lang in (("es-CO,es;q=0.9", "es"), ("en-US,en;q=0.9", "en")):
            with self.subTest(lang=lang):
                self.assertEqual(page.WORDS[lang]["qr_for"], QR_FOR[lang])
                rest = QR_FOR[lang][len(QR_LEAD[lang]) + 1:]
                self.assertEqual((QR_FOR[lang][:len(QR_LEAD[lang]) + 1], rest[0].islower()),
                                 (QR_LEAD[lang] + " ", True))
                card = re.findall(r'<figure class="key-card">.*?</figure>', self.html(accept), re.S)[0]
                self.assertEqual(re.findall(r'<p class="qr-for" id="qr-for">.*?</p>', card),
                                 [f'<p class="qr-for" id="qr-for"><b>{html.escape(QR_LEAD[lang])}</b> '
                                  f'{html.escape(rest)}</p>'])
                self.assertIn(' aria-labelledby="qr-for"', re.search(r'<svg class="qr"[^>]*>', card).group(0))

    def test_the_check_now_form_keeps_the_panels_spacing(self):
        # Lane pfui (OR-106): from 880 px the key's card leaves for its own column with margin:0, and the form that
        # carries «Check now» sat flush under the card «Este PC», since only form{margin:0} named it. Mutation: the
        # rule left out. Red: no rule gives the form a top margin. Mutation: a margin other than the card
        # «Actividad»'s, the panel that follows a panel. Red: the two differ. Mutation: the rule put inside a width
        # query. Red: the rule sits inside a block, so the phone width misses it.
        style = page._STYLE
        found = [(selectors.strip(), body) for selectors, body in re.findall(r"([^{}]+)\{([^{}]*)\}", style)]
        # The relink form of the linked page follows the card «Este PC» the same way: the one rule names both.
        check = [(selectors, body) for selectors, body in found if "margin" in body and
                 {"form[action='/check']", "form[action='/relink']"} & {selector.strip()
                                                                         for selector in selectors.split(",")}]
        self.assertEqual(check, [("form[action='/check'],form[action='/relink']", "margin-top:16px")])
        check = [body for selectors, body in check]
        log_card = [body for selectors, body in found if selectors == ".log-card"]
        self.assertEqual(len(log_card), 1)
        self.assertEqual(re.search(r"(?:^|;)margin-top:([^;]+)", check[0]).group(1),
                         re.search(r"(?:^|;)margin:([^ ;]+)", log_card[0]).group(1))
        rule = "\nform[action='/check'],form[action='/relink']{margin-top:16px}\n"
        self.assertEqual(style.count(rule), 1)
        before = style[:style.index(rule)]
        self.assertEqual(before.count("{"), before.count("}"))
        # The form is still the link's own child right after the key's card, the place the rule spaces.
        self.assertEqual(self.html().count('</figure><form method="post" action="/check">'), 1)
        # The relink form on the linked page that offers it, rendered as the program builds the page (__main__.py:
        # a watcher whose snapshot is always a dict, and the events): the card «Este PC», then the form as the
        # link's own child, then the card «Actividad», each once. Mutation: the form put inside «Este PC» or after
        # «Actividad». Red: the order or the closed card before the form.
        self.call("POST", "/typed", form={"linkId": LINK_ID, "secret": self.secret()})
        for _ in range(3):
            self.state.record_ping(worker.Refused())
        seen = {"client": "waiting", "phase": None, "alert": None, "at": None, "pingResult": None, "pingAt": None,
                "paused": False}
        used = page.PairingPage(self.state, watch=lambda: dict(seen), on_quit=lambda: None, on_pause=lambda: None,
                                on_resume=lambda: None, events=lambda: [])
        for lang in ("es", "en"):
            with self.subTest(page="relink", lang=lang):
                offered = used.render(lang)
                marks = ('<div class="panel pc" role="group"', '<form method="post" action="/relink">',
                         '<div class="panel log-card">')
                self.assertEqual([offered.count(mark) for mark in marks], [1, 1, 1])
                at = [offered.index(mark) for mark in marks]
                self.assertEqual(at, sorted(at))
                card = offered[at[0]:at[1]]
                self.assertEqual((card.count("<div"), card.endswith("</div>")), (card.count("</div>"), True))

    def test_the_sponsored_style_is_picked_at_every_render_from_the_five_and_changes_only_the_picture(self):
        # Lane pfart (OR-113, owner 2026-10-03 20:1x: «aleatorios»). Mutation: the default seam answering a fixed
        # style. Red: fewer than five styles over the renders. Mutation: the style's picture drawn with a change of
        # anything else in the page. Red: two renders differ once the img is removed. Mutation: the data-style of
        # another style. Red: the picture is not the named style's.
        art = support.module("sponsor_art")
        self.call("POST", "/typed", form={"linkId": LINK_ID, "secret": self.secret()})
        picture = re.compile(r'<img class="qr2" src="([^"]+)" alt="[^"<>]*" width="264" height="264" '
                             r'data-style="([a-z]+)">')
        drawn = {}
        for _ in range(128):
            found = picture.findall(self.page.render("es"))
            self.assertEqual(len(found), 1)
            uri, style = found[0]
            self.assertIn(style, art.STYLES)
            self.assertEqual(uri, art.data_uri(style))
            drawn[style] = drawn.get(style, 0) + 1
        self.assertEqual(set(drawn), set(art.STYLES))
        with mock.patch.object(page.random, "choice", side_effect=lambda names: names[-1]) as choice:
            self.assertEqual(page._pick_sponsor_style(), "bosque")
        self.assertEqual(choice.call_args.args, (art.STYLES,))
        for lang in ("es", "en"):
            with self.subTest(lang=lang):
                renders = []
                for style in ("amor", "calma"):
                    with mock.patch.object(page, "_pick_sponsor_style", return_value=style):
                        renders.append(self.page.render(lang))
                self.assertNotEqual(renders[0], renders[1])
                bare = [picture.sub("", shown, count=1) for shown in renders]
                self.assertEqual(bare[0], bare[1])
                self.assertEqual((bare[0].count('class="qr2"'), bare[0].count("data-style")), (0, 0))

    def test_the_linked_page_keeps_the_pendiapp_com_qr_visible(self):
        # Mutation: the sponsored QR put under the key's data-shown or any hiding rule. Red: a rule names it, or the
        # aside carries a data-shown or a control.
        self.assertEqual(page.SPONSOR_ADDRESS, "https://pendiapp.com")
        self.call("POST", "/typed", form={"linkId": LINK_ID, "secret": self.secret()})
        for accept in ("es-CO,es;q=0.9", "en-US,en;q=0.9"):
            with self.subTest(accept=accept):
                linked = self.html(accept)
                side = re.findall(r'<aside class="side">.*?</aside>', linked, re.S)
                self.assertEqual(len(side), 1)
                self.assertEqual(side[0].count('<img class="qr2"'), 1)
                self.assertEqual([marker for marker in ("data-shown", "<button", "<details") if marker in side[0]], [])
                # aria-hidden on the figures is a screen reader's, never a hiding: only the bare attribute counts.
                self.assertIsNone(re.search(r"(?<![-\w])hidden(?=[\s>=])", side[0]))
                for sheet in (page._STYLE, page._NOSCRIPT_STYLE):
                    for selectors, body in re.findall(r"([^{}]+)\{([^{}]*)\}", sheet):
                        if re.search(r"\.(?:side|sponsor|arcade|qr2)\b", selectors):
                            self.assertIsNone(re.search(r"visibility:hidden|display:none|opacity:0\b", body),
                                              selectors + "{" + body + "}")

    def assert_the_key_reads_with_scripts_off(self, document):
        """The page's head holds one <noscript> whose only child is a <style> that shows the code and hides the
        mask and the reveal, the way a browser with scripts blocked reads the key; the key itself stays where it was,
        once in the document and never inside the noscript."""
        key = codes.display(self.secret())
        found = re.findall(r"<noscript>(.*?)</noscript>", document, re.S)
        self.assertEqual(len(found), 1, "no noscript, or more than one, on a page that draws the key")
        self.assertLess(document.index("<noscript>"), document.index("</head>"))
        style = re.fullmatch(r"<style>([^<]*)</style>", found[0])
        self.assertIsNotNone(style, f"the noscript holds more than one style: {found[0][:200]}")
        rules = {selector.strip(): body for selectors, body in re.findall(r"([^{}]+)\{([^{}]*)\}", style.group(1))
                 for selector in selectors.split(",")}
        # The same selector as the mask's own rule, later in the document, so it wins with scripts off.
        self.assertEqual(rules.get('.key[data-shown="false"] .code'), "visibility:visible")
        self.assertEqual((rules.get(".mask"), rules.get(".reveal")), ("display:none", "display:none"))
        self.assertNotIn(key, found[0])
        self.assertEqual(document.count(key), 1)
        self.assertEqual(KeyPlace(document, key).places, [[]])
        self.assertEqual(outside_references(document), [])
        self.assertIn('<p class="key" data-shown="false">', document)

    def test_the_waiting_page_shows_its_key_with_scripts_off_in_both_languages(self):
        # Mutation: the noscript style dropped. Red: no noscript. Mutation: the key written inside the noscript.
        # Red: the key counted twice.
        for accept in ("es-CO,es;q=0.9", "en-US,en;q=0.9"):
            with self.subTest(accept=accept):
                self.assert_the_key_reads_with_scripts_off(self.html(accept))

    def test_the_waiting_page_with_the_button_refused_shows_its_key_with_scripts_off(self):
        wait = html.escape(page.WORDS["es"]["button_wait"])
        for _ in range(20):  # the button is refused once its window of checks is full
            self.assertEqual(self.call("POST", "/check")[0], 303)
            refused = self.html("es-CO,es;q=0.9")
            if wait in refused:
                break
        self.assertIn(wait, refused)
        self.assert_the_key_reads_with_scripts_off(refused)

    def test_the_page_after_a_relink_shows_its_key_with_scripts_off(self):
        self.call("POST", "/typed", form={"linkId": LINK_ID, "secret": self.secret()})
        for _ in range(3):
            self.state.record_ping(worker.Refused())
        self.assertEqual(self.call("POST", "/relink")[0], 303)
        again = self.html("en-US,en;q=0.9")
        self.assertIn('<svg class="qr"', again)
        self.assert_the_key_reads_with_scripts_off(again)

    def test_the_lower_part_is_one_fold_closed_unless_the_typed_link_was_refused(self):
        # Mutation: the fold rendered open. Red: the waiting page's details carries open. Mutation: open left out on
        # a refused typed link. Red: the refused page's fold is closed.
        for accept, lang in (("es-CO,es;q=0.9", "es"), ("en-US,en;q=0.9", "en")):
            with self.subTest(lang=lang):
                shown, words = self.html(accept), page.WORDS[lang]
                self.assertEqual(KeyPlace(shown, "\0").details, [{"class": "fold"}])
                self.assertIn(f'<details class="fold"><summary>{FOLD[lang]}</summary><section class="more">'
                              f'<div class="panel"><h2>{html.escape(words["typed_title"])}</h2>', shown)
                self.assertIn("</form></div></section></details>", shown)
                self.assertEqual(page.WORDS[lang].get("fold"), FOLD[lang])
        self.assertEqual(self.call("POST", "/typed", form={"linkId": "WXYZ6789ABC", "secret": SECRET})[0], 303)
        refused = self.html()
        self.assertEqual(KeyPlace(refused, "\0").details, [{"class": "fold", "open": None}])
        self.assertIn(f'<details class="fold" open><summary>{FOLD["es"]}</summary>', refused)
        self.assertIn(page.WORDS["es"]["typed_refused"], refused)
        self.call("POST", "/typed", form={"linkId": LINK_ID, "secret": self.secret()})
        self.assertEqual(KeyPlace(self.html(), "\0").details, [{"class": "fold"}])

    def test_the_top_bar_names_the_creator_and_the_repository_on_every_page(self):
        # Mutation: the credit left in the foot. Red: the bar does not open with it. Mutation: the name left as
        # ElkinDev. Red: the credit differs. Mutation: the footer drawn with no quit. Red: the page with nothing to
        # stop holds a footer. Mutation: the link without rel noopener. Red: the credit differs.
        def credit(lang):
            return (f'<header class="bar"><p class="credit">{CREDIT[lang]} <b>Niklerk</b> · {CREDIT_LINK}'
                    'github.com/ElkinDev/PendiFy</a></p><form class="langsw" role="group"')

        quits = page.PairingPage(self.state, on_quit=lambda: None)
        documents = {}
        for lang in ("es", "en"):
            documents[("waiting", lang)] = (self.page.render(lang), False)
            documents[("waiting with quit", lang)] = (quits.render(lang), True)
        self.call("POST", "/typed", form={"linkId": LINK_ID, "secret": self.secret()})
        for lang in ("es", "en"):
            documents[("linked", lang)] = (self.page.render(lang), False)
        for _ in range(3):
            self.state.record_ping(worker.Refused())
        for lang in ("es", "en"):
            documents[("relink", lang)] = (self.page.render(lang), False)
        for (name, lang), (shown, has_quit) in documents.items():
            with self.subTest(page=name, lang=lang):
                self.assertEqual(shown.count(credit(lang)), 1)
                self.assertEqual(shown.count("http"), 1)
                self.assertNotIn("ElkinDev", shown.replace(CREDIT_LINK, "", 1).replace(
                    "github.com/ElkinDev/PendiFy</a>", "", 1))
                feet = re.findall(r"<footer[^>]*>(.*?)</footer>", shown, re.S)
                if not has_quit:
                    self.assertNotIn("<footer", shown)
                    continue
                self.assertEqual(shown.count("<footer"), 1)
                self.assertIn('<footer class="foot"><form method="post" action="/quit">', shown)
                self.assertEqual(len(feet), 1)
                self.assertRegex(feet[0], r'\A<form method="post" action="/quit">[^<]*<input [^>]*>[^<]*'
                                          r'<button type="submit">[^<]*</button></form>\Z')
        self.assertEqual((page.WORDS["es"].get("credit"), page.WORDS["en"].get("credit")), (CREDIT["es"], CREDIT["en"]))
        self.assertIn(".bar{display:flex;flex-wrap:wrap;justify-content:flex-end;align-items:center;gap:.45rem;"
                      "margin:0 0 8px}", page._STYLE)
        self.assertIn(".bar .credit{margin-right:auto}", page._STYLE)

    def test_the_linked_and_the_relink_pages_show_the_sponsored_qr_and_the_waiting_page_never(self):
        # Mutation: the aside drawn whatever the state. Red: the waiting page holds it. Mutation: the note kept on
        # the relink page. Red: its aside ends with the note. Lane pfart (OR-113): the code is one of the app's five
        # styles, the seam's pick, as an img of its PNG; the label, the frame, the caption and the party stay.
        # Mutation: the picture of another style drawn. Red: the style's data URI is not in the aside. Mutation: the
        # pick made once at import or at construction. Red: the patched seam does not reach the render.
        art = support.module("sponsor_art")

        def side(lang, note, style):
            words = SPONSOR[lang]
            return (f'<aside class="side"><figure class="sponsor"><figcaption class="sponsor-by">{words["by"]}'
                    '</figcaption><div class="arcade"><i></i><i></i><i></i><i></i><img class="qr2" '
                    f'src="{art.data_uri(style)}" alt="{words["qr"]}" width="264" height="264" data-style="{style}">'
                    f'</div><p class="sponsor-cap">{words["cap"]}</p>{page._PARTY}</figure>'
                    + (f'<p class="note">{words["note"]}</p>' if note else "") + "</aside></section>")

        def shown(lang, style):
            with mock.patch.object(page, "_pick_sponsor_style", return_value=style) as pick:
                drawn = self.page.render(lang)
            self.assertEqual(pick.call_count, 1)
            return drawn

        for lang in ("es", "en"):
            asked = AssertionError("the style asked on the waiting page")
            with mock.patch.object(page, "_pick_sponsor_style", side_effect=asked):
                waiting = self.page.render(lang)
            self.assertEqual([marker for marker in ("<aside", '<img class="qr2"', "data-style", SPONSOR[lang]["by"])
                              if marker in waiting], [])
        self.call("POST", "/typed", form={"linkId": LINK_ID, "secret": self.secret()})
        for lang in ("es", "en"):
            for style in art.STYLES:
                with self.subTest(page="linked", lang=lang, style=style):
                    linked = shown(lang, style)
                    self.assertEqual(linked.count("<aside"), 1)
                    self.assertEqual(linked.count(side(lang, True, style)), 1)
                    self.assertEqual((linked.count(art.data_uri(style)), linked.count("data-style=")), (1, 1))
        for _ in range(3):
            self.state.record_ping(worker.Refused())
        for lang in ("es", "en"):
            for style in art.STYLES:
                with self.subTest(page="relink", lang=lang, style=style):
                    offered = shown(lang, style)
                    self.assertIn('action="/relink"', offered)
                    self.assertEqual(offered.count("<aside"), 1)
                    self.assertEqual(offered.count(side(lang, False, style)), 1)
                    self.assertEqual((offered.count(art.data_uri(style)), offered.count("data-style=")), (1, 1))
                    self.assertNotIn(SPONSOR[lang]["note"], offered)
        for lang in ("es", "en"):
            words = page.WORDS[lang]
            self.assertEqual(tuple(words.get(name) for name in ("sponsor_by", "sponsor_cap", "sponsor_qr", "sponsor_note")),
                             tuple(SPONSOR[lang][name] for name in ("by", "cap", "qr", "note")))

    def test_the_party_holds_exactly_the_four_figures_on_every_page_that_draws_it(self):
        # Mutation: a fifth figure or another child in the party. Red: .px:nth-child(1..4) would name other figures.
        self.assertRegex(page._PARTY, r'\A<div class="party" aria-hidden="true">(?:<svg class="px" viewBox="0 0 16 16" '
                                      r'width="48" height="48" shape-rendering="crispEdges">(?:(?!</?svg|<div).)*'
                                      r"</svg>){4}</div>\Z")
        self.assertEqual(self.html().count('<div class="party"'), 1)
        self.call("POST", "/typed", form={"linkId": LINK_ID, "secret": self.secret()})
        self.assertEqual(self.html().count(page._PARTY), 1)

    def test_the_stylesheet_moves_each_figure_alone_and_drops_the_hop_and_the_summary(self):
        # Mutation: the hop kept beside the moves. Red: a hop rule in the style. Mutation: a move outside the guard.
        # Red: the guard's block misses it, or an animation of a figure shows twice.
        style = page._STYLE
        self.assertIsNone(re.search(r"\bhop\b", style))
        self.assertEqual([gone for gone in (".key-card summary", ".key-card details", ".shown{", "repeat(9,auto)",
                                            "1440px", "min(max(1040px,85vw)", "#watch,") if gone in style], [])
        self.assertIn(MOVES, style)
        self.assertEqual((style.count("@keyframes px-"), style.count("animation:px-")), (4, 4))
        self.assertIn('@media (prefers-reduced-motion:no-preference){#state[data-shown^="true"]::before{animation:'
                      'breathe 2.4s ease-in-out infinite}}\n@keyframes breathe{50%{opacity:.3}}\n', style)
        # The seat's corrections of the design sheet: the sponsored QR from its track, the watcher line keeps its cap
        # unless paused, an explicit row for an eleventh child, the fold's paragraphs at 71ch; no px cap on the page.
        for rule in ("@media (min-width:880px){.page{max-width:max(1040px,85vw)}",
                     "grid-template-rows:repeat(10,auto) 1fr;",
                     ".link:has(.key-card)>:is(#state,#watch[data-paused],.pc,.log-card),"
                     ".link:has(.side)>:is(#state,#watch[data-paused],.pc,.log-card){max-width:none}",
                     ".fold .panel p{max-width:71ch}",
                     "@media (min-width:880px){.qr2{width:min(330px,calc(clamp(340px,34vw,522px) - 82px));"
                     "height:min(330px,calc(clamp(340px,34vw,522px) - 82px))}}"):
            self.assertIn(rule, style)
        # Lane pcnw (OR-99): under 360 px the sponsored code is 198 px square, 6 px a module, so a 320 px window keeps
        # the sponsor's frame inside its gutters; the rule comes after the 399 px one so it wins. Mutation: the rule
        # placed before the 399 px one. Red: its index is not past the 399 px rule's.
        narrow = "@media (max-width:359px){.qr2{width:198px;height:198px}}\n"
        under_400 = "@media (max-width:399px){.qr2{width:231px;height:231px}}\n"
        self.assertEqual((style.count(narrow), style.count(under_400)), (1, 1))
        self.assertGreater(style.index(narrow), style.index(under_400))
        self.assertIn(".qr2{display:block;width:264px;height:264px;outline:4px solid var(--px-line)}\n", style)

    def test_the_theme_button_of_the_site_is_served_with_its_words_its_head_read_and_its_rules(self):
        # Mutation: the head read left out. Red: the head holds no pendi-theme read. Mutation: the dark variables
        # left out of the data-theme="dark" block. Red: the block is not the dark list.
        for accept, lang in (("es-CO,es;q=0.9", "es"), ("en-US,en;q=0.9", "en")):
            with self.subTest(lang=lang):
                shown = self.html(accept)
                self.assertEqual(shown.count('<button id="theme-toggle"'), 1)
                # The header opens with the credit, then the language switch right before the button (lane
                # pclang, OR-96; lane pccr, OR-98).
                self.assertRegex(shown, re.escape('<main class="page"><header class="bar"><p class="credit">')
                                 + r"(?:(?!</p>).)*</p>"
                                 + re.escape('<form class="langsw" '
                                             f'role="group" aria-label="{page.WORDS[lang]["language"]}" '
                                             'method="post" action="/lang">')
                                 + r"(?:(?!</form>).)*</form>"
                                 + re.escape(f'<button id="theme-toggle" class="icon-btn" type="button" '
                                             f'aria-pressed="false" aria-label="'
                                             f'{html.escape(page.WORDS[lang]["theme_toggle"])}"><svg class="moon"'))
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
        # The stop runs after the answer is written, on the server's thread: the reader may finish first.
        deadline = time.monotonic() + 2.0
        while len(quits) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
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


    def figure_motion(self):
        # The served style's four figure animations and their keyframes, in the order the figures stand.
        style = self.html().split("<style>")[1].split("</style>")[0]
        lines = re.findall(r"\.px:nth-child\((\d)\)\{animation:(px-[a-z]+) (\S+) steps\(1,end\) infinite\}", style)
        frames = {}
        for name, body in re.findall(r"@keyframes (px-[a-z]+)\{(.*?\})\}", style):
            frames[name] = sorted(float(pct) for selector in re.findall(r"([\d.,% ]+)\{", body)
                                  for pct in selector.replace("%", "").split(","))
        return style, lines, frames

    def with_updater(self, updater):
        """The page beside an updater and a quit, as main builds it."""
        self.page = page.PairingPage(self.state, on_quit=lambda: None, updater=updater)
        self.page.start()
        self.addCleanup(self.page.close)
        self.port, self.host = self.page.port, f"127.0.0.1:{self.page.port}"

    @staticmethod
    def updater(mode, installed=(True, None), hold=None, restart=None):
        """An updater whose check finds 0.1.6 over 0.1.5 and whose install answers `installed`, once `hold` is set
        when one is given; its restart is `restart`, else nothing."""
        update = support.module("update")

        def install(version):
            if hold is not None:
                hold.wait(5)
            return installed

        return update.Updater("0.1.5", mode, check=lambda: "0.1.6", install=install,
                              restart=restart or (lambda: None), clock=lambda seconds: True, log=lambda line: None)

    def test_the_state_carries_the_update_and_the_page_draws_its_line_and_its_button_by_state(self):
        # Mutation: «Reiniciar ahora» drawn in auto mode only. Red: notify's ready line has no button.
        # Mutation: the kept pip line left out of the title. Red: the failed line carries no title.
        # Mutation: the line drawn above the state line. Red: the state line is not followed by the update line.
        # Mutation: the poll compares no update. Red: the script never reloads on a new state.
        self.assertEqual([page.WORDS["es"][key] for key in UPDATE_KEYS], [
            "Hay una versión nueva: {version}", "Actualizar", "Instalando la versión {version}\u2026",
            "Actualización lista: {version}. Se aplica al reiniciar.", "Reiniciar ahora",
            "No se pudo instalar la versión {version}"])
        self.assertEqual([page.WORDS["en"][key] for key in UPDATE_KEYS], [
            "A new version is out: {version}", "Update", "Installing version {version}\u2026",
            "Update ready: {version}. It applies on restart.", "Restart now",
            "Version {version} could not be installed"])
        hold, failure = threading.Event(), 'ERROR: No matching distribution found for pendify==0.1.6 "<x>"'
        self.addCleanup(hold.set)
        available, installing, ready_notify = self.updater("notify"), self.updater("notify", hold=hold), \
            self.updater("notify")
        ready_auto, failed = self.updater("auto"), self.updater("auto", installed=(False, failure))
        for made in (available, installing, ready_notify, ready_auto, failed):
            made.round()
        installing.request_install()
        ready_notify.request_install()
        deadline = time.monotonic() + 5
        while ready_notify.snapshot()["state"] != "ready" and time.monotonic() < deadline:
            time.sleep(0.01)
        cases = ((self.updater("auto"), "none", None, None, None), (available, "available", "update_available",
                                                                    "update", "update_install"),
                 (installing, "installing", "update_installing", None, None),
                 (ready_auto, "ready", "update_ready", "restart", "update_restart"),
                 (ready_notify, "ready", "update_ready", "restart", "update_restart"),
                 (failed, "failed", "update_failed", None, None))
        for made, state, line, action, label in cases:
            self.with_updater(made)
            version = None if state == "none" else "0.1.6"
            error = failure if state == "failed" else None
            for accept, lang in (("es-CO,es;q=0.9", "es"), ("en-US,en;q=0.9", "en")):
                with self.subTest(state=state, mode=made.mode, lang=lang):
                    words = page.WORDS[lang]
                    answer = json.loads(self.call("GET", "/state", headers={"Accept-Language": accept})[2])
                    self.assertEqual(answer["update"], {"state": state, "version": version, "error": error})
                    shown = self.html(accept)
                    self.assertEqual(shown.count(f' data-update="{state} {version or ""}"'), 1)
                    lines = re.findall(r'<p id="update" class="note( warn)?"( title="[^"]*")?>([^<]*)</p>', shown)
                    self.assertEqual(lines, [] if line is None else [(
                        " warn" if error else "", f' title="{html.escape(error)}"' if error else "",
                        html.escape(words[line].format(version="0.1.6")))])
                    if line is not None:
                        self.assertRegex(shown, r'<p id="state"[^>]*>[^<]*</p><p id="update"')
                    forms = re.findall(r'<form method="post" action="/(update|restart)"><input type="hidden" '
                                       r'name="token" value="[^"]+"><button type="submit">([^<]*)</button></form>',
                                       shown)
                    self.assertEqual(forms, [] if action is None else [(action, html.escape(words[label]))])
        self.assertIn("const upd=s.dataset.update;", page._SCRIPT)
        poll = page._SCRIPT[:page._SCRIPT.index("location.reload();")]
        self.assertIn("||(upd!==undefined&&j.update&&j.update.state+' '+(j.update.version||'')!==upd)", poll)
        # Without an updater neither the state nor the page says anything of one.
        self.page = page.PairingPage(self.state)
        self.page.start()
        self.addCleanup(self.page.close)
        self.port, self.host = self.page.port, f"127.0.0.1:{self.page.port}"
        self.assertNotIn("update", json.loads(self.call("GET", "/state")[2]))
        self.assertNotIn("data-update", self.html())
        self.assertNotIn('id="update"', self.html())

    def test_restart_and_update_are_fenced_like_quit_and_restart_sets_the_flag_then_quits(self):
        # Mutation: /restart answered before the token check. Red: a POST with no token restarts the program.
        # Mutation: the quit called before the flag is set. Red: the quit sees no restart asked.
        self.assertEqual([self.call("POST", path)[0] for path in ("/restart", "/update")], [404, 404])
        quits = []
        made = self.updater("auto", restart=lambda: quits.append(made.restart_requested.is_set()))
        made.round()
        self.with_updater(made)
        hosts = ["evil.example", f"127.0.0.1:{self.port + 1}", f"localhost.evil.example:{self.port}", ""]
        refused = [self.call("POST", path, host=host)[0] for path in ("/restart", "/update") for host in hosts]
        refused += [self.call("POST", path, token=token)[0] for path in ("/restart", "/update")
                    for token in (False, "wrong", self.page.token[:-1], self.page.token + "x")]
        self.assertEqual((refused, quits, made.restart_requested.is_set()), ([403] * 16, [], False))
        status, headers, body = self.call("POST", "/restart")
        self.assertEqual((status, body), (200, page.WORDS["es"]["update_restarting"]))
        for name, value in FENCE.items():
            self.assertEqual(headers.get(name), value)
        deadline = time.monotonic() + 2
        while not quits and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(quits, [True])
        # Notify mode: «Actualizar» installs, then restarts the same way.
        installs, quits = [], []
        notify = self.updater("notify", restart=lambda: quits.append(notify.restart_requested.is_set()))
        notify.round()
        self.with_updater(notify)
        status, headers, _ = self.call("POST", "/update")
        self.assertEqual((status, headers["location"]), (303, "/"))
        deadline = time.monotonic() + 5
        while not quits and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual((quits, notify.snapshot()["state"]), ([True], "ready"))
        # A second «Actualizar» finds nothing to install and only shows the page again.
        self.assertEqual(self.call("POST", "/update")[0], 303)
        self.assertEqual(quits, [True])

    def test_restart_answers_restarting_in_both_languages_and_the_page_keeps_the_line_until_the_poll_fails(self):
        # Mutation: /restart answered 204 with no words. Red: the answer's body is empty.
        # Mutation: the catch draws the closed sentence after a restart the page asked for. Red: it never reads asked.
        # Mutation: a poll that answers after the ask draws its state again. Red: the poll does not return on asked.
        # Mutation: notify's install drawn without the words. Red: its state line carries no data-restarting.
        restarting = {"es": "Reiniciando. La página nueva se abre en unos segundos.",
                      "en": "Restarting. The new page opens in a few seconds."}
        self.assertEqual({lang: page.WORDS[lang]["update_restarting"] for lang in restarting}, restarting)
        for accept, lang in (("es-CO,es;q=0.9", "es"), ("en-US,en;q=0.9", "en")):
            with self.subTest(lang=lang):
                quits = []
                made = self.updater("auto", restart=lambda quits=quits: quits.append(True))
                made.round()
                self.with_updater(made)
                status, headers, body = self.call("POST", "/restart", headers={"Accept-Language": accept})
                self.assertEqual((status, headers["content-type"], body),
                                 (200, "text/plain; charset=utf-8", restarting[lang]))
                deadline = time.monotonic() + 2
                while not quits and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertEqual((quits, made.restart_requested.is_set()), ([True], True))
        # The script posts the restart's form itself and draws the answer in place of the state line; once asked,
        # a poll that answers changes nothing and one that fails keeps the line.
        script = page._SCRIPT
        for piece in ("const follows=s.dataset.restarting!==undefined;let asked=false;",
                      "function restarting(t){asked=true;s.textContent=t;s.dataset.shown='closed';}",
                      "const rf=document.querySelector('form[action=\"/restart\"]');",
                      "fetch('/restart',{method:'POST',body:new URLSearchParams(new FormData(rf))})",
                      ".then(t=>{restarting(t);})",
                      ".then(j=>{if(asked)return;"
                      "if(follows&&j.update&&j.update.state==='ready'){restarting(s.dataset.restarting);return;}",
                      ".catch(()=>{if(asked)return;s.textContent=follows?s.dataset.restarting:s.dataset.closed;"
                      "s.dataset.shown='closed';})"):
            with self.subTest(piece=piece):
                self.assertEqual(script.count(piece), 1)
        # «Actualizar» in notify mode: while its install runs the state line carries the words, so the restart that
        # follows reads the same; auto mode's ready carries none.
        hold = threading.Event()
        self.addCleanup(hold.set)
        notify, ready_auto = self.updater("notify", hold=hold), self.updater("auto")
        notify.round()
        notify.request_install()
        ready_auto.round()
        for made, carried in ((notify, True), (ready_auto, False)):
            self.with_updater(made)
            for accept, lang in (("es-CO,es;q=0.9", "es"), ("en-US,en;q=0.9", "en")):
                with self.subTest(mode=made.mode, lang=lang):
                    found = re.findall(r'<p id="state"[^>]* data-restarting="([^"]*)"', self.html(accept))
                    self.assertEqual(found, [html.escape(restarting[lang])] if carried else [])

    def test_the_figures_move_in_sequence_one_second_apart(self):
        # Mutation: the loop left at 30s. Red: a duration is not 15s. Mutation: the knight's burst moved back onto
        # the archer's end. Red: the gap between them is not 1.00 s.
        _, lines, frames = self.figure_motion()
        self.assertEqual([(n, name) for n, name, _ in lines],
                         [("1", "px-archer"), ("2", "px-knight"), ("3", "px-mage"), ("4", "px-creature")])
        self.assertEqual([duration for _, _, duration in lines], ["15s"] * 4)
        bursts = []
        for index, (_, name, duration) in enumerate(lines):
            seconds = [round(pct / 100 * float(duration[:-1]), 2) for pct in frames[name] if pct < 100]
            # The archer's burst opens the loop on its 0% frame; every other burst opens on its first frame past
            # the 0% rest.
            bursts.append(seconds if index == 0 else [s for s in seconds if s > 0])
        lengths = [[round(s - burst[0], 2) for s in burst] for burst in bursts]
        self.assertEqual(lengths, [[0, 0.6, 1.5], [0, 0.45, 0.9, 1.35], [0, 0.45, 1.8, 2.25],
                                   [0, 0.3, 0.6, 0.9, 1.2, 1.5, 1.8]])  # each burst keeps its own shape in seconds
        self.assertAlmostEqual(bursts[0][0], 0.0, delta=0.005)
        for previous, following in zip(bursts, bursts[1:]):
            with self.subTest(previous_end=previous[-1], next_start=following[0]):
                self.assertAlmostEqual(following[0] - previous[-1], 1.0, delta=0.05)
        self.assertLess(bursts[-1][-1], 15)

    def test_reduced_motion_keeps_the_figures_still(self):
        # Mutation: an animation declared on .px outside the no-preference block. Red: the style outside holds it.
        style, lines, _ = self.figure_motion()
        opening = "@media (prefers-reduced-motion:no-preference){"
        blocks = []
        for found in re.finditer(re.escape(opening), style):
            depth, end = 1, found.end()
            while depth:
                depth += {"{": 1, "}": -1}.get(style[end], 0)
                end += 1
            blocks.append((found.start(), found.end(), end))
        holding = [block for block in blocks if ".px:nth-child(" in style[block[1]:block[2]]]
        self.assertEqual(len(holding), 1)
        head, start, end = holding[0]
        inside, outside = style[start:end - 1], style[:head] + style[end:]
        self.assertEqual(len(lines), 4)
        for n, name, duration in lines:
            self.assertIn(f".px:nth-child({n}){{animation:{name} {duration} steps(1,end) infinite}}", inside)
        self.assertIsNone(re.search(r"\.px[^{]*\{[^}]*animation", outside))
        self.assertNotIn("@keyframes px-", outside)

    def replace_page(self, secret, quits=True):
        """A page beside a run file whose replace secret is `secret` (None before the publish, absent for no run
        file), its quit recorded in the list returned with it; `quits` False gives it no quit."""
        stopped = []
        replaced = page.PairingPage(self.state, on_quit=(lambda: stopped.append("quit")) if quits else None)
        if secret != "absent":
            replaced.run = types.SimpleNamespace(secret=secret)
        replaced.start()
        self.addCleanup(replaced.close)
        return replaced, stopped

    def post_replace(self, target, fields, language=None):
        """POST /replace to `target` as a newer copy sends it: its form only, no page token."""
        connection = http.client.HTTPConnection("127.0.0.1", target.port, timeout=5)
        self.addCleanup(connection.close)
        sent = {"Host": f"127.0.0.1:{target.port}", "Content-Type": "application/x-www-form-urlencoded"}
        sent.update({"Accept-Language": language} if language else {})
        connection.request("POST", "/replace", body=urllib.parse.urlencode(fields).encode(), headers=sent)
        response = connection.getresponse()
        return response.status, response.read().decode("utf-8")

    def test_replace_with_the_run_secret_closes_the_page_and_answers_202(self):
        # Mutation: /replace checked against the page token as every other route. Red: the newer copy, which has
        # no page token, is refused 403 and the older page keeps running.
        for language, word in ((None, "Cerrando"), ("en", "Closing")):
            with self.subTest(language=language):
                replaced, stopped = self.replace_page(RUN_SECRET)
                self.assertEqual(self.post_replace(replaced, {"secret": RUN_SECRET}, language), (202, word))
                self.assertEqual(stopped, ["quit"])

    def test_replace_with_a_wrong_secret_answers_403_and_closes_nothing(self):
        # Mutation: the secret compared only when one is posted. Red: a post with no secret stops the program.
        replaced, stopped = self.replace_page(RUN_SECRET)
        for name, fields in (("wrong", {"secret": "f" * 32}), ("missing", {}), ("empty", {"secret": ""}),
                             ("the page token", {"token": replaced.token})):
            with self.subTest(fields=name):
                self.assertEqual(self.post_replace(replaced, fields)[0], 403)
        self.assertEqual(stopped, [])

    def test_replace_is_no_route_without_a_run_secret(self):
        # Mutation: /replace listed whenever the page can quit, an absent secret read as empty. Red: an empty secret
        # stops a page that has no run secret.
        for name, secret, quits in (("no run file", "absent", True), ("not published yet", None, True),
                                    ("no quit", RUN_SECRET, False)):
            with self.subTest(page=name):
                replaced, stopped = self.replace_page(secret, quits)
                for fields in ({"secret": ""}, {"secret": RUN_SECRET}):
                    self.assertEqual(self.post_replace(replaced, fields)[0], 404)
                self.assertEqual(stopped, [])


if __name__ == "__main__":
    unittest.main()
