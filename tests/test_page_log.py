"""PageLogTest: /state hands out the watcher's events as the log's lines (lane pclog round 1), and the page draws
them in the card «Actividad» of form A (the log design, round 2 of 2026-10-01, frames A1 to A6).

The words are the design brief's, byte for byte (briefs/pclog-design-2026-10-01.md, "What the log is"); each line
carries its seq, its time as HH:MM:SS in this PC's zone, its text in the answer's language and whether it reports a
failure. The card follows the card «Este PC», or ends the first column on the page that still shows the code, the
newest line first; the poll adds the lines above the seq the card carries, by text, and keeps the last fifty.
"""
import ast
import html
import http.client
import json
import re
import time
import unittest
from pathlib import Path

import support
from support import LINK_ID, SECRET, FakeClock
from test_page import GAME_WORDS

config = support.module("config")
page = support.module("page")
pairing = support.module("pairing")
watcher = support.module("watcher")
worker = support.module("worker")


def local(hour, minute, second):
    """The wall time of that clock time on 2026-10-01 in this PC's zone."""
    return time.mktime((2026, 10, 1, hour, minute, second, 0, 0, -1))


# Every kind the watcher notes, in a session's order: (kind, detail, clock time, Spanish, English, warn).
SESSION = (
    ("started", None, (9, 0, 1), "El programa empezó.", "The program started.", False),
    ("waiting", None, (9, 0, 1), "Esperando el cliente del juego.", "Waiting for the game client.", False),
    ("connected", None, (9, 0, 7), "Conectado al cliente del juego.", "Connected to the game client.", False),
    ("phase", "Lobby", (9, 0, 7), "En la sala.", "In the lobby.", False),
    ("phase", "Matchmaking", (9, 0, 41), "Buscando partida.", "Looking for a match.", False),
    ("phase", "ReadyCheck", (9, 2, 3), "Partida encontrada.", "Match found.", False),
    ("accepted", None, (9, 2, 5), "Partida aceptada.", "Match accepted.", False),
    ("ping", "sent", (9, 2, 6), "Aviso al teléfono: enviado.", "Alert to the phone: sent.", False),
    ("phase", "ChampSelect", (9, 2, 21), "Eligiendo.", "Choosing.", False),
    ("phase", "InProgress", (9, 3, 50), "En partida.", "In a game.", False),
    ("loading", None, (9, 3, 50), "Pantalla de carga: esperando que empiece la partida.",
     "Loading screen: waiting for the match to start.", False),
    ("match_started", None, (9, 4, 12), "La partida empezó.", "The match started.", False),
    ("ping", "refused", (9, 4, 13), "Aviso al teléfono: rechazado por tu cuenta.",
     "Alert to the phone: refused by your account.", True),
    ("ping", "not_delivered", (9, 4, 14), "Aviso al teléfono: no entregado.", "Alert to the phone: not delivered.",
     True),
    ("ping", "failed", (9, 4, 15), "Aviso al teléfono: falló el envío.", "Alert to the phone: sending failed.", True),
    ("phase", "EndOfGame", (9, 31, 0), "Fin de la partida.", "Game over.", False),
    ("phase", "None", (9, 31, 9), "Sin partida.", "No game.", False),
    ("phase", "Reconnect", (9, 32, 0), "Otro estado.", "Other state.", False),
    ("lost", None, (9, 32, 30), "Se perdió el cliente del juego. Buscándolo de nuevo.",
     "Lost the game client. Looking for it again.", True),
    ("paused", None, (10, 5, 0), "Avisos en pausa.", "Alerts paused.", True),
    ("resumed", None, (23, 59, 59), "Avisos reanudados.", "Alerts resumed.", False),
)
LANGUAGES = (("es", 3), ("en-US,en;q=0.8", 4))
# The card's two words, as the sheet writes them.
TITLES = {"es": ("Actividad", "Lo que el programa vio y avisó desde que empezó en este PC."),
          "en": ("Activity", "What the program saw and alerted since it started on this PC.")}
LINK = re.compile(r'<section class="link">(.*?)</section><section class="more">', re.S)
LOG_CARD = re.compile(r'<div class="panel log-card">.*?</ol></div></div>', re.S)
PC_CARD = re.compile(r'<div class="panel pc"[^>]*>.*?</div>', re.S)  # the card «Este PC» holds no div of its own


def markup(shown):
    """The page's body without its script: the elements drawn, not the style's or the script's text."""
    return shown[shown.index("<body>"):shown.rindex("<script>")]


def drawn(lines):
    """The list's markup of /state's lines, the newest first: a time and its text, a failure's text in its class."""
    items = []
    for line in reversed(lines):
        span = '<span class="warn">' if line["warn"] else "<span>"
        items.append(f'<li><time datetime="{line["time"]}">{line["time"]}</time>{span}{html.escape(line["text"])}'
                     f"</span></li>")
    return "".join(items)


class PageLogTest(unittest.TestCase):
    def setUp(self):
        tmp = support.temp_dir()
        self.addCleanup(tmp.cleanup)
        self.store = config.ConfigStore(Path(tmp.name))
        self.store.set_typed(LINK_ID, SECRET)
        self.state = pairing.PairingState(self.store, lambda secret: worker.Refused(), clock=FakeClock())
        self.snapshot = {"client": "waiting", "phase": None, "alert": None, "at": None, "pingResult": None,
                         "pingAt": None, "paused": False}
        self.events = [(seq, local(*clock), kind, detail)
                       for seq, (kind, detail, clock, *_) in enumerate(SESSION, start=1)]
        self.page = self.serve(watch=lambda: dict(self.snapshot), on_pause=lambda: None, on_resume=lambda: None,
                               events=lambda: list(self.events))

    def serve(self, state=None, **kwargs):
        served = page.PairingPage(state or self.state, **kwargs)
        served.start()
        self.addCleanup(served.close)
        return served

    def card(self, language, served=None):
        """The page, its first column and the card «Actividad» in it, which is there once."""
        shown = self.get("/", language, served)
        link = LINK.search(shown)
        self.assertIsNotNone(link)
        cards = LOG_CARD.findall(link.group(1))
        self.assertEqual(len(cards), 1)
        return shown, link.group(1), cards[0]

    def texts(self, *events):
        """The Spanish log's texts of `events`, each given as (kind, detail), a second apart."""
        self.events = [(seq, local(9, 0, seq), kind, detail) for seq, (kind, detail) in enumerate(events, start=1)]
        return [line["text"] for line in self.log("es")]

    def get(self, path, language, served=None):
        served = served or self.page
        connection = http.client.HTTPConnection("127.0.0.1", served.port, timeout=5)
        self.addCleanup(connection.close)
        connection.request("GET", path, headers={"Host": f"127.0.0.1:{served.port}", "Accept-Language": language})
        answer = connection.getresponse()
        self.assertEqual(answer.status, 200)
        return answer.read().decode("utf-8")

    def log(self, language, served=None):
        return json.loads(self.get("/state", language, served))["log"]

    def test_state_carries_every_kind_of_line_in_both_languages_word_for_word_with_its_time_and_warn(self):
        # Mutation: HH:MM as the watcher line says it. Red: «09:00» for «09:00:01».
        # Mutation: warn on every ping. Red: the sent ping flagged.
        for language, column in LANGUAGES:
            with self.subTest(language=language):
                expected = [{"seq": seq, "time": "%02d:%02d:%02d" % row[2], "text": row[column], "warn": row[5]}
                            for seq, row in enumerate(SESSION, start=1)]
                self.assertEqual(self.log(language), expected)
                for line in expected:
                    self.assertIsNone(GAME_WORDS.search(line["text"]), line["text"])

    def test_the_three_phases_that_end_a_match_give_one_line(self):
        # Mutation: the phase collapse removed. Red: «Fin de la partida.» three times.
        self.events = [(7, local(9, 30, 0), "phase", "EndOfGame"), (8, local(9, 30, 1), "phase", "PreEndOfGame"),
                       (9, local(9, 30, 5), "phase", "WaitingForStats"), (10, local(9, 31, 0), "phase", "Lobby")]
        self.assertEqual(self.log("es"), [{"seq": 7, "time": "09:30:00", "text": "Fin de la partida.", "warn": False},
                                          {"seq": 10, "time": "09:31:00", "text": "En la sala.", "warn": False}])
        self.assertEqual([line["text"] for line in self.log("en")], ["Game over.", "In the lobby."])
        # A ping between two end phases breaks nothing: still one line.
        self.assertEqual(self.texts(("phase", "EndOfGame"), ("ping", "sent"), ("phase", "PreEndOfGame")),
                         ["Fin de la partida.", "Aviso al teléfono: enviado."])

    def test_a_phase_after_a_reconnect_or_a_resume_is_shown_again_though_it_reads_like_the_one_before(self):
        # Mutation: the collapse against the last phase line kept anywhere (round 1). Red: one «En la sala.» in
        # each session. Mutation: only lost and connected end the collapse. Red: the pause session gives one.
        self.assertEqual(self.texts(("phase", "Lobby"), ("lost", None), ("connected", None), ("phase", "Lobby")),
                         ["En la sala.", "Se perdió el cliente del juego. Buscándolo de nuevo.",
                          "Conectado al cliente del juego.", "En la sala."])
        self.assertEqual(self.texts(("phase", "Lobby"), ("paused", None), ("resumed", None), ("phase", "Lobby")),
                         ["En la sala.", "Avisos en pausa.", "Avisos reanudados.", "En la sala."])
        for kind in ("lost", "connected", "paused", "resumed"):
            with self.subTest(kind=kind):
                texts = self.texts(("phase", "Lobby"), (kind, None), ("phase", "Lobby"))
                self.assertEqual((len(texts), texts[0], texts[2]), (3, "En la sala.", "En la sala."))

    def test_an_event_of_a_kind_the_page_does_not_know_is_dropped(self):
        self.events = [(1, local(9, 0, 0), "started", None), (2, local(9, 0, 1), "vanished", None),
                       (3, local(9, 0, 2), "ping", "unheard"), (4, local(9, 0, 3), "resumed", None)]
        self.assertEqual([(line["seq"], line["text"]) for line in self.log("es")],
                         [(1, "El programa empezó."), (4, "Avisos reanudados.")])

    def test_a_page_with_no_events_answers_no_log(self):
        for served in (self.serve(watch=lambda: dict(self.snapshot)), self.serve()):
            for language, _ in LANGUAGES:
                with self.subTest(language=language):
                    self.assertNotIn("log", json.loads(self.get("/state", language, served)))

    def test_the_card_follows_the_card_this_pc_and_draws_the_lines_state_answers_the_newest_first(self):
        # Frames A1 to A5. Mutation: the lines drawn oldest first. Red: «El programa empezó.» on top. Mutation: the
        # failure class on every line. Red: the sent ping in it.
        for language, column in LANGUAGES:
            lang = page.language(language)
            with self.subTest(language=language):
                title, helper = TITLES[lang]
                self.assertEqual((page.WORDS[lang].get("log_title"), page.WORDS[lang].get("log_help")), (title, helper))
                shown, link, card = self.card(language)
                pc_card = PC_CARD.search(link)
                self.assertIsNotNone(pc_card)
                self.assertTrue(link[pc_card.end():].startswith(card))
                answered = self.log(language)
                self.assertEqual(card, f'<div class="panel log-card"><h2 id="log-title">{html.escape(title)}</h2>'
                                       f'<p id="log-help">{html.escape(helper)}</p><div class="log" role="region" '
                                       f'aria-labelledby="log-title" aria-describedby="log-help" '
                                       f'data-seq="{len(SESSION)}"><ol>{drawn(answered)}</ol></div></div>')
                self.assertTrue(card.endswith(f"<span>{html.escape(SESSION[0][column])}</span></li></ol></div></div>"))
                self.assertEqual(card.count('<span class="warn">'), sum(row[5] for row in SESSION))
                self.assertEqual(card.count("<li>"), len(SESSION))
                # The two names the list is read by exist once each, and the list names them.
                for name in ('id="log-title"', 'id="log-help"', 'aria-labelledby="log-title"',
                             'aria-describedby="log-help"'):
                    self.assertEqual(shown.count(name), 1, name)
                self.assertNotIn("tabindex", card)  # rendered with no tab stop: the script sets it while it scrolls
                for line in answered:
                    self.assertIsNone(GAME_WORDS.search(line["text"]), line["text"])
                self.assertIsNone(GAME_WORDS.search(card))

    def test_on_the_page_that_offers_the_relink_the_card_ends_the_column_and_the_relink_does_not_move(self):
        # The refused page takes the code page's rule (ruling after round 2). Mutation: the card right after the
        # card «Este PC» on this page too. Red: the card between that card and the relink form, which moves down.
        for _ in range(pairing.REFUSED_PINGS_FOR_RELINK):
            self.state.record_ping(worker.Refused())
        bare = self.serve(watch=lambda: dict(self.snapshot), on_pause=lambda: None, on_resume=lambda: None)
        for language, _ in LANGUAGES:
            words = page.WORDS[page.language(language)]
            with self.subTest(language=language):
                shown, link, card = self.card(language)

                def relink(token):
                    return (f'<form method="post" action="/relink"><input type="hidden" name="token" '
                            f'value="{token}"><button type="submit">{html.escape(words["relink"])}</button></form>')

                self.assertTrue(link.endswith(relink(self.page.token) + card))
                self.assertLessEqual(PC_CARD.search(link).end(), link.index(relink(self.page.token)))
                # Without the events the column is the same up to and including the relink form: it does not move.
                without = LINK.search(self.get("/", language, bare)).group(1)
                self.assertNotIn('class="panel log-card"', without)
                self.assertTrue(without.endswith(relink(bare.token)))
                self.assertEqual(link[:link.index(relink(self.page.token))].replace(self.page.token, "TOKEN"),
                                 without[:without.index(relink(bare.token))].replace(bare.token, "TOKEN"))

    def test_on_the_page_that_still_shows_the_code_the_card_ends_the_first_column_and_the_code_does_not_move(self):
        # Mutation: the card drawn right after the card «Este PC» on this page too. Red: the scan sentence and the
        # code card under it, frame A6.
        tmp = support.temp_dir()
        self.addCleanup(tmp.cleanup)
        fresh = pairing.PairingState(config.ConfigStore(Path(tmp.name)), lambda secret: worker.Refused(),
                                     clock=FakeClock())
        served = self.serve(fresh, watch=lambda: dict(self.snapshot), on_pause=lambda: None,
                            on_resume=lambda: None, events=lambda: list(self.events))
        for language, _ in LANGUAGES:
            words = page.WORDS[page.language(language)]
            with self.subTest(language=language):
                shown, link, card = self.card(language, served)
                self.assertIn('<figure class="key-card">', link)
                check = (f'<form method="post" action="/check"><input type="hidden" name="token" '
                         f'value="{served.token}"><button type="submit">{html.escape(words["check"])}</button></form>')
                self.assertTrue(link.endswith(check + card))
                # The scan sentence and the code card follow the card «Este PC» as they did before the log.
                pc_card = PC_CARD.search(link)
                self.assertTrue(link[pc_card.end():].startswith(
                    f'<p class="scan" id="scan">{html.escape(words["scan"])}</p><figure class="key-card">'))
                self.assertEqual(card.count("<li>"), len(SESSION))

    def test_a_page_without_the_events_draws_no_card_and_one_with_none_yet_draws_an_empty_list(self):
        # Mutation: the card drawn without the callable. Red: «Actividad» on a page with no events.
        for served in (self.serve(watch=lambda: dict(self.snapshot)), self.serve()):
            for language, _ in LANGUAGES:
                with self.subTest(language=language):
                    shown = markup(self.get("/", language, served))
                    self.assertNotIn('class="panel log-card"', shown)
                    self.assertNotIn('class="log"', shown)
                    for word in TITLES[page.language(language)]:
                        self.assertNotIn(html.escape(word), shown)
        _, _, card = self.card("es", self.serve(watch=lambda: dict(self.snapshot), events=lambda: []))
        self.assertTrue(card.endswith(' data-seq="0"><ol></ol></div></div>'))

    def test_the_poll_adds_by_text_at_the_top_only_the_lines_above_the_cards_seq_and_keeps_fifty(self):
        # Mutation: the lines added through innerHTML. Red: a line's text read as markup. Mutation: every line of
        # the answer added. Red: no compare with the seq. Mutation: no trim. Red: no 50.
        for markup in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write"):
            self.assertNotIn(markup, page._SCRIPT)
        self.assertIn("const g=document.querySelector('.log');", page._SCRIPT)
        self.assertIn("location.reload();if(g&&j.log){const o=g.firstElementChild;let last=Number(g.dataset.seq);"
                      "for(const l of j.log){if(l.seq<=last)continue;const li=document.createElement('li');"
                      "const t=document.createElement('time');t.setAttribute('datetime',l.time);t.textContent=l.time;"
                      "const x=document.createElement('span');if(l.warn)x.className='warn';x.textContent=l.text;"
                      "li.append(t,x);o.insertBefore(li,o.firstChild);last=l.seq;}g.dataset.seq=String(last);"
                      "while(o.children.length>50)o.lastElementChild.remove();tabStop();}})", page._SCRIPT)
        self.assertEqual(watcher.LOG_LINES, 50)  # the card keeps what the ring keeps

    def test_the_list_is_a_tab_stop_only_while_its_lines_are_taller_than_its_box(self):
        # Mutation: tabindex rendered. Red: a stop on a list that does not scroll (frames A2 and A6). Mutation: the
        # stop set once at load. Red: no tabStop() after an insert (the pin above).
        self.assertIn("const g=document.querySelector('.log');function tabStop(){if(g.scrollHeight>g.clientHeight)"
                      "g.setAttribute('tabindex','0');else g.removeAttribute('tabindex');}if(g)tabStop();",
                      page._SCRIPT)
        self.assertNotIn("tabindex", markup(self.get("/", "es")))

    def test_the_program_hands_the_watchers_events_to_the_page(self):
        # Mutation: the page built without them. Red: no events keyword on the page's call.
        tree = ast.parse((support.package_dir() / "__main__.py").read_text(encoding="utf-8"))
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Attribute) and node.func.attr == "PairingPage"]
        self.assertEqual(len(calls), 1)
        given = {keyword.arg: ast.unparse(keyword.value) for keyword in calls[0].keywords}
        self.assertEqual((given["watch"], given["events"]), ("watch.snapshot", "watch.events"))


if __name__ == "__main__":
    unittest.main()
