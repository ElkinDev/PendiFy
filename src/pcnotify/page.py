"""The pairing page, served on 127.0.0.1 only (design P2, P5, P6, residuals a and e).

Fences: a request whose Host is not 127.0.0.1:<port> or localhost:<port> answers 403, so a page on
another name that rebinds to this address can never read the secret; every POST carries this run's
random token, else 403; every answer carries no-store, no-referrer, DENY and a policy that loads
nothing from any origin; no request line and no body is ever logged.
"""
import hmac
import html
import json
import secrets
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import codes, config, qr

ADDRESS = "127.0.0.1"
MAX_FORM_BYTES = 4096
# A POST's body is read up to this bound before any answer, refusals included: a socket closed with
# unread bytes in it is reset on Windows, and the client meets the reset in place of the answer.
DRAIN_BYTES = 64 * 1024
# With no usable length the socket is read up to DRAIN_BYTES while bytes keep coming, each read waiting this long.
DRAIN_WAIT = 0.1
POLICY = ("default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'; "
          "form-action 'self'; frame-ancestors 'none'; base-uri 'none'")
FENCE_HEADERS = (("Cache-Control", "no-store"), ("Referrer-Policy", "no-referrer"), ("X-Frame-Options", "DENY"),
                 ("Content-Security-Policy", POLICY), ("X-Content-Type-Options", "nosniff"))

WORDS = {
    "es": {
        "title": "Avisos de este PC",
        "intro": "Este programa envía sus avisos a tu cuenta de Pendi. Para eso, este PC tiene que quedar enlazado "
                 "con tu cuenta.",
        "scan": "Escanea este código con la cámara del teléfono donde tienes tu cuenta y confirma el enlace.",
        "code_label": "Clave de este PC:",
        "state_waiting": "Esperando la confirmación en el teléfono.",
        "state_wait": "El servicio pidió esperar un momento. Se volverá a preguntar solo.",
        "state_offline": "No se pudo conectar. Se volverá a intentar.",
        "state_paused": "Se dejó de preguntar. Pulsa «Comprobar ahora» para seguir.",
        "state_linked": "Este PC está enlazado. Los avisos llegarán a tu cuenta.",
        "state_refused": "Tu cuenta ya no acepta los avisos de este PC. Puedes volver a enlazarlo.",
        "state_closed": "El programa no está abierto. Vuelve a iniciarlo para seguir.",
        "check": "Comprobar ahora",
        "button_wait": "Espera un momento antes de volver a comprobar.",
        "typed_title": "¿Creaste el enlace escribiendo los valores? Cópialos aquí:",
        "link_id_label": "Identificador del enlace",
        "secret_label": "Clave",
        "save": "Guardar",
        "typed_refused": "Esos valores no son válidos: cada uno tiene doce caracteres.",
        "config_failed": "No se pudo leer ni guardar la configuración de este PC.",
        "relink": "Volver a enlazar",
        "forget": "Olvidar este PC",
        "forget_sentence": "Olvidar este PC crea una clave nueva y quita el enlace de este PC. El enlace anterior "
                           "sigue activo en tu cuenta hasta que lo desenlaces en Pendi o caduque.",
        "watch_waiting": "Esperando el cliente del juego en este PC.",
        "watch_connected": "Conectado al cliente del juego.",
        "watch_last": "Último aviso: {what}, a las {time}.",
        "alert_loading": "empezó la pantalla de carga",
        "alert_queue": "partida encontrada",
        "alert_started": "la partida empezó",
        "quit": "Salir",
        "stopped": "El programa se detuvo: ya no vigila el cliente del juego ni envía avisos.",
        "start_again": "Para volver a iniciarlo, abre el acceso directo del Escritorio o ejecuta "
                       "pythonw -m pcnotify (o python -m pcnotify para verlo en una consola).",
    },
    "en": {
        "title": "Alerts from this PC",
        "intro": "This program sends its alerts to your Pendi account. For that, this PC has to be linked to your "
                 "account.",
        "scan": "Scan this code with the camera of the phone that holds your account and confirm the link.",
        "code_label": "This PC's key:",
        "state_waiting": "Waiting for the confirmation on the phone.",
        "state_wait": "The service asked to wait a moment. It will ask again by itself.",
        "state_offline": "Could not connect. It will try again.",
        "state_paused": "Stopped asking. Press «Check now» to go on.",
        "state_linked": "This PC is linked. Alerts will reach your account.",
        "state_refused": "Your account no longer accepts this PC's alerts. You can link it again.",
        "state_closed": "The program is not running. Start it again to go on.",
        "check": "Check now",
        "button_wait": "Wait a moment before checking again.",
        "typed_title": "Did you make the link by typing the values? Copy them here:",
        "link_id_label": "Link id",
        "secret_label": "Key",
        "save": "Save",
        "typed_refused": "Those values are not valid: each has twelve characters.",
        "config_failed": "This PC's settings could not be read or saved.",
        "relink": "Link again",
        "forget": "Forget this PC",
        "forget_sentence": "Forgetting this PC makes a new key and removes this PC's link. The old link stays "
                           "active in your account until you unlink it in Pendi or it expires.",
        "watch_waiting": "Waiting for the game client on this PC.",
        "watch_connected": "Connected to the game client.",
        "watch_last": "Last alert: {what}, at {time}.",
        "alert_loading": "the loading screen started",
        "alert_queue": "match found",
        "alert_started": "the match started",
        "quit": "Quit",
        "stopped": "The program stopped: it no longer watches the game client or sends alerts.",
        "start_again": "To start it again, open the shortcut on the Desktop or run pythonw -m pcnotify "
                       "(or python -m pcnotify to see it in a console).",
    },
}

_STYLE = ("body{font-family:system-ui,sans-serif;max-width:40rem;margin:2rem auto;padding:0 1rem;line-height:1.4}"
          ".code{font-family:monospace;font-size:1.4rem;letter-spacing:.1em}label{display:block;margin:.4rem 0}"
          "form{margin:.8rem 0}")
# Polls the state; reloads when what the page shows changes; says so when the program is gone.
_SCRIPT = ("const s=document.getElementById('state');const w=document.getElementById('watch');"
           "const shown=s.dataset.shown;"
           "setInterval(()=>fetch('/state').then(r=>r.json()).then(j=>{s.textContent=j.text;"
           "if(w&&j.watchText)w.textContent=j.watchText;"
           "if(String(j.showCode)+String(j.relinkOffered)!==shown)location.reload();})"
           ".catch(()=>{s.textContent=s.dataset.closed;}),5000);")


def language(accept_language):
    """English when the first tag of Accept-Language is English, Spanish otherwise and when absent."""
    first = (accept_language or "").split(",")[0].split(";")[0].strip()
    return "en" if first.split("-")[0].lower() == "en" else "es"


def _report_failure(request, client_address):
    """A request that broke inside the handler is said on stderr by its type only, never with its data;
    a browser that dropped its connection is not a failure of the page."""
    failure = sys.exc_info()[1]
    if not isinstance(failure, ConnectionError):
        _say_failure(failure)


def _say_failure(failure):
    """The one fixed stderr line of a failed request: its type, never its data."""
    print(f" [page] a request failed: {type(failure).__name__}", file=sys.stderr)


def _form(action, token, inner):
    return (f'<form method="post" action="/{action}"><input type="hidden" name="token" value="{token}">'
            f"{inner}</form>")


class PairingPage:
    def __init__(self, state, watch=None, on_quit=None):
        """`watch` answers the watcher's snapshot; without it the page shows no watcher line. `on_quit` stops
        the program; without it the page shows no quit button and /quit is no route."""
        self.state = state
        self.watch = watch
        self.on_quit = on_quit
        self.token = secrets.token_urlsafe(32)
        self.server = None
        self.port = None

    def start(self):
        """Serve on 127.0.0.1 on a port the system assigns; the page's address."""
        self.server = ThreadingHTTPServer((ADDRESS, 0), _handler(self))
        self.server.daemon_threads = True
        self.server.handle_error = _report_failure
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True).start()
        return f"http://{ADDRESS}:{self.port}/"

    def close(self):
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()

    def host_allowed(self, host):
        return (host or "").strip().lower() in (f"127.0.0.1:{self.port}", f"localhost:{self.port}")

    def watch_text(self, lang):
        """The watcher's line in `lang`: waiting or connected, then the last alert and its time; None
        when no watcher runs beside the page. It names no game, no maker and no product."""
        if self.watch is None:
            return None
        snapshot, words = self.watch(), WORDS[lang]
        text = words["watch_connected" if snapshot.get("client") == "connected" else "watch_waiting"]
        if snapshot.get("alert") in ("loading", "queue", "started") and snapshot.get("at") is not None:
            text += " " + words["watch_last"].format(what=words["alert_" + snapshot["alert"]],
                                                     time=time.strftime("%H:%M", time.localtime(snapshot["at"])))
        return text

    def state_json(self, lang):
        snapshot = self.state.snapshot()
        answer = {**snapshot, "text": WORDS[lang]["state_" + snapshot["state"]]}
        watch = self.watch_text(lang)
        if watch is not None:
            answer["watchText"] = watch
        return answer

    def render(self, lang):
        words = {key: html.escape(value) for key, value in WORDS[lang].items()}
        snapshot = self.state.snapshot()
        secret = self.state.secret_for_display() if snapshot["showCode"] else None
        token = html.escape(self.token)
        parts = [f"<h1>{words['title']}</h1>", f"<p>{words['intro']}</p>",
                 f'<p id="state" role="status" data-shown="{str(snapshot["showCode"]).lower()}'
                 f'{str(snapshot["relinkOffered"]).lower()}" data-closed="{words["state_closed"]}">'
                 f'{words["state_" + snapshot["state"]]}</p>']
        if snapshot["configFailed"]:
            parts.append(f"<p>{words['config_failed']}</p>")
        watch = self.watch_text(lang)
        if watch is not None:
            parts.append(f'<p id="watch" role="status">{html.escape(watch)}</p>')
        if secret is not None:
            parts += [f"<p>{words['scan']}</p>", qr.svg(qr.encode(qr.pairing_address(secret).encode("ascii")).modules),
                      f'<p>{words["code_label"]} <span class="code">{codes.display(secret)}</span></p>',
                      _form("check", token, f'<button type="submit">{words["check"]}</button>')]
            if snapshot["buttonRefused"]:
                parts.append(f"<p>{words['button_wait']}</p>")
        if snapshot["relinkOffered"]:
            parts.append(_form("relink", token, f'<button type="submit">{words["relink"]}</button>'))
        parts += [f"<h2>{words['typed_title']}</h2>",
                  _form("typed", token, f'<label>{words["link_id_label"]} <input name="linkId" maxlength="32" '
                                        f'autocomplete="off"></label><label>{words["secret_label"]} <input '
                                        f'name="secret" maxlength="32" autocomplete="off"></label>'
                                        f'<button type="submit">{words["save"]}</button>')]
        if snapshot["typedRefused"]:
            parts.append(f"<p>{words['typed_refused']}</p>")
        parts += [f"<h2>{words['forget']}</h2>", f"<p>{words['forget_sentence']}</p>",
                  _form("forget", token, f'<button type="submit">{words["forget"]}</button>')]
        if self.on_quit is not None:
            parts.append(_form("quit", token, f'<button type="submit">{words["quit"]}</button>'))
        return (f'<!doctype html><html lang="{lang}"><head><meta charset="utf-8"><meta name="viewport" '
                f'content="width=device-width, initial-scale=1"><title>{words["title"]}</title><style>{_STYLE}'
                f"</style></head><body>{''.join(parts)}<script>{_SCRIPT}</script></body></html>")

    def render_stopped(self, lang):
        """The one small page /quit answers: the program stopped, and how to start it again."""
        words = {key: html.escape(value) for key, value in WORDS[lang].items()}
        return (f'<!doctype html><html lang="{lang}"><head><meta charset="utf-8"><meta name="viewport" '
                f'content="width=device-width, initial-scale=1"><title>{words["title"]}</title><style>{_STYLE}'
                f'</style></head><body><h1>{words["title"]}</h1><p>{words["stopped"]}</p>'
                f'<p>{words["start_again"]}</p></body></html>')

    def act(self, path, form):
        """The route's action; a config file that cannot be read or replaced is said on the page and on
        stderr, and the request is still answered."""
        try:
            if path == "/check":
                self.state.ask()
            elif path == "/typed":
                self.state.typed(form.get("linkId", ""), form.get("secret", ""))
            elif path == "/forget":
                self.state.forget()
            elif path == "/relink":
                self.state.accept_relink()
        except config.ConfigError as failure:
            _say_failure(failure)
            self.state.config_failed()


def _handler(page):
    class Handler(BaseHTTPRequestHandler):
        server_version = "local"
        sys_version = ""
        # A socket that declares a body and sends none is dropped after it, with no answer, and its thread ends.
        timeout = 10

        def log_message(self, format, *args):  # no request line, no body, ever
            pass

        def end_headers(self):
            for name, value in FENCE_HEADERS:
                self.send_header(name, value)
            super().end_headers()

        def _send(self, status, content_type, text, extra=()):
            data = text.encode("utf-8")
            self.send_response(status)
            for name, value in extra:
                self.send_header(name, value)
            self.send_header("Connection", "close")  # no client reuses a socket the handler is done with
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)

        def _refuse(self, status):
            self._send(status, "text/plain; charset=utf-8", {403: "forbidden", 404: "not found", 400: "bad request",
                                                             411: "length required", 413: "too large"}[status])

        def do_GET(self):
            self._get(seen=True)

        def do_HEAD(self):
            # The fences and the headers of GET, no body; a HEAD is not a person looking at the page.
            self._get(seen=False)

        def _get(self, seen):
            if not page.host_allowed(self.headers.get("Host")):
                return self._refuse(403)
            path = urllib.parse.urlsplit(self.path).path
            lang = language(self.headers.get("Accept-Language"))
            if seen and path in ("/", "/state"):
                page.state.page_seen()
            if path == "/":
                return self._send(200, "text/html; charset=utf-8", page.render(lang))
            if path == "/state":
                return self._send(200, "application/json", json.dumps(page.state_json(lang)))
            return self._refuse(404)

        def _drain(self):
            """What the socket holds, up to DRAIN_BYTES, for a body with no usable length: read while bytes
            come within DRAIN_WAIT, so no answer leaves bytes unread."""
            self.connection.settimeout(DRAIN_WAIT)
            try:
                drained = 0
                while drained < DRAIN_BYTES:
                    chunk = self.rfile.read1(DRAIN_BYTES - drained)
                    if not chunk:
                        return
                    drained += len(chunk)
            except TimeoutError:  # nothing more came within DRAIN_WAIT: the drain ends, it did not fail
                return
            finally:
                self.connection.settimeout(self.timeout)

        def do_POST(self):
            declared = self.headers.get("Content-Length")
            encoded = self.headers.get("Transfer-Encoding") is not None
            plain = declared is not None and declared.strip().isascii() and declared.strip().isdigit()
            length = int(declared) if plain and not encoded else None
            # Read before any answer, up to DRAIN_BYTES: the declared body, or what the socket holds when there is
            # no usable length; a longer declaration leaves the rest unread.
            if length is None:
                self._drain()
            body = self.rfile.read(min(length, DRAIN_BYTES)) if length else b""
            if not page.host_allowed(self.headers.get("Host")):
                return self._refuse(403)
            path = urllib.parse.urlsplit(self.path).path
            routes = ("/check", "/typed", "/forget", "/relink") + (("/quit",) if page.on_quit is not None else ())
            if path not in routes:
                return self._refuse(404)
            if length is None:  # chunked or absent is 411, anything but plain digits 400
                return self._refuse(411 if encoded or declared is None else 400)
            if length > MAX_FORM_BYTES:
                return self._refuse(413)
            raw = body.decode("utf-8", "replace")
            try:
                fields = urllib.parse.parse_qs(raw, keep_blank_values=True, max_num_fields=8) if raw else {}
            except ValueError:  # more fields than any form of the page has
                return self._refuse(400)
            form = {key: values[0] for key, values in fields.items()}
            if not hmac.compare_digest(form.get("token", "").encode(), page.token.encode()):
                return self._refuse(403)
            if path == "/quit":
                # Answered before the stop, so the exit that follows never cuts the answer short.
                self._send(200, "text/html; charset=utf-8",
                           page.render_stopped(language(self.headers.get("Accept-Language"))))
                return page.on_quit()
            page.act(path, form)
            self._send(303, "text/plain; charset=utf-8", "", (("Location", "/"),))

    return Handler
