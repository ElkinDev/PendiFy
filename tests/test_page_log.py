"""PageLogTest: /state hands out the watcher's events as the log's lines (lane pclog round 1).

The words are the design brief's, byte for byte (briefs/pclog-design-2026-10-01.md, "What the log is"); each line
carries its seq, its time as HH:MM:SS in this PC's zone, its text in the answer's language and whether it reports a
failure. This round draws nothing: the page and its script hold no log element, class or word.
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
LOG_ID_OR_CLASS = re.compile(r'\b(?:id|class)="([^"]*)"')


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

    def serve(self, **kwargs):
        served = page.PairingPage(self.state, **kwargs)
        served.start()
        self.addCleanup(served.close)
        return served

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

    def test_this_round_draws_nothing_of_the_log(self):
        # Mutation: a block drawn under the card. Red: its title or its lines on the page.
        self.assertNotIn("j.log", page._SCRIPT)
        for language, column in LANGUAGES:
            with self.subTest(language=language):
                shown = self.get("/", language)
                self.assertIn('id="watch"', shown)
                for word in ("Actividad", "Activity", "Lo que el programa vio", "What the program saw"):
                    self.assertNotIn(word, shown)
                for row in SESSION:
                    self.assertNotIn(html.escape(row[column]), shown)
                self.assertEqual([name for name in LOG_ID_OR_CLASS.findall(shown) if "log" in name], [])

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
