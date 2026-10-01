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

from . import codes, config, icon, plate_almena, qr

ADDRESS = "127.0.0.1"
MAX_FORM_BYTES = 4096
# A POST's body is read up to this bound before any answer, refusals included: a socket closed with
# unread bytes in it is reset on Windows, and the client meets the reset in place of the answer.
DRAIN_BYTES = 64 * 1024
# With no usable length the socket is read up to DRAIN_BYTES while bytes keep coming, each read waiting this long.
DRAIN_WAIT = 0.1
# img-src data: serves the QR scene's plate and the program's icon, each inlined as a data URI; none loads from an origin.
POLICY = ("default-src 'none'; img-src data:; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'; "
          "form-action 'self'; frame-ancestors 'none'; base-uri 'none'")
# The program's icon (icon.py, written by tools/make_icon.py), its 32 px PNG inlined as a data URI: the tab icon of both
# documents and the image left of the pairing page's title, allowed by img-src data: with no new origin.
ICON_URI = "data:image/png;base64," + icon.PNG_32
_TAB_ICON = f'<link rel="icon" type="image/png" href="{ICON_URI}">'
FENCE_HEADERS = (("Cache-Control", "no-store"), ("Referrer-Policy", "no-referrer"), ("X-Frame-Options", "DENY"),
                 ("Content-Security-Policy", POLICY), ("X-Content-Type-Options", "nosniff"))
# The program's name (owner 2026-10-01): the title and the h1 of the pairing page and of the stopped page.
NAME = "PendiFy"
# The foot's credit: the creator's handle and the repository, a link out that opens in a new tab with no opener and
# no referrer. A link loads nothing, so the policy above is unchanged.
CREATOR = "ElkinDev"
REPOSITORY = "https://github.com/ElkinDev/PendiFy"
# The address the linked page's sponsored QR encodes.
SPONSOR_ADDRESS = "https://pendiapp.com"

# "title" is the sentence under the program's name, the tagline; NAME is the title and the h1 of both documents.
WORDS = {
    "es": {
        "title": "Avisos de este PC",
        "intro": "Este programa envía sus avisos a tu cuenta de Pendi. Para eso, este PC tiene que quedar enlazado "
                 "con tu cuenta.",
        "scan": "Escanea este código con la cámara del teléfono donde tienes tu cuenta y confirma el enlace.",
        "code_label": "Clave de este PC:",
        "show_code": "Mostrar el código",
        "hide_code": "Ocultar el código",
        "mask": "Clave oculta",
        "fold": "Más opciones: escribir los valores del enlace u olvidar este PC",
        "credit": "Creado por",
        "sponsor_by": "Patrocinado por Pendiapp.com",
        "sponsor_cap": "Escanéalo para abrir pendiapp.com",
        "sponsor_qr": "Código QR de pendiapp.com",
        "sponsor_note": "Este PC ya está enlazado, por eso ya no se muestra su código. «Olvidar este PC», en «Más "
                        "opciones», crea un código nuevo para enlazar.",
        "theme_toggle": "Cambiar entre tema claro y oscuro",
        "language": "Idioma",
        "state_waiting": "Esperando la confirmación en el teléfono.",
        "state_wait": "El servicio pidió esperar un momento. Se volverá a preguntar solo.",
        "state_offline": "No se pudo conectar. Se volverá a intentar.",
        "state_paused": "Se dejó de preguntar. Pulsa «Comprobar ahora» para seguir.",
        "state_linked": "Este PC está enlazado. Los avisos llegarán a tu cuenta.",
        "state_linked_paused": "Este PC está enlazado. Los avisos están en pausa.",
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
        "watch_paused": "En pausa: este PC no lee el juego ni avisa a tu teléfono.",
        "watch_last": "Último aviso: {what}, a las {time}.",
        "alert_loading": "empezó la pantalla de carga",
        "alert_queue": "partida encontrada",
        "alert_started": "la partida empezó",
        "phase_none": "Sin partida",
        "phase_lobby": "En la sala",
        "phase_matchmaking": "Buscando partida",
        "phase_readycheck": "Partida encontrada",
        "phase_champselect": "Eligiendo",
        "phase_inprogress": "En partida",
        "phase_endofgame": "Fin de la partida",
        "phase_other": "Otro estado",
        "ping_sent": "enviado",
        "ping_refused": "rechazado por tu cuenta",
        "ping_not_delivered": "no entregado",
        "ping_failed": "falló el envío",
        "watch_phase": "Ahora: {phase}.",
        "watch_ping": "Aviso al teléfono: {result}, a las {time}.",
        "log_started": "El programa empezó.",
        "log_waiting": "Esperando el cliente del juego.",
        "log_connected": "Conectado al cliente del juego.",
        "log_lost": "Se perdió el cliente del juego. Buscándolo de nuevo.",
        "log_accepted": "Partida aceptada.",
        "log_loading": "Pantalla de carga: esperando que empiece la partida.",
        "log_match_started": "La partida empezó.",
        "log_ping": "Aviso al teléfono: {result}.",
        "log_paused": "Avisos en pausa.",
        "log_resumed": "Avisos reanudados.",
        "log_title": "Actividad",
        "log_help": "Lo que el programa vio y avisó desde que empezó en este PC.",
        "quit": "Salir",
        "this_pc": "Este PC",
        "pause": "Pausar avisos",
        "resume": "Reanudar avisos",
        "autostart_label": "Iniciar con Windows",
        "autostart_help": "Al encender el PC el programa empieza solo, sin abrir esta página.",
        "stopped": "El programa se detuvo: ya no vigila el cliente del juego ni envía avisos.",
        "start_again": "Para volver a iniciarlo, abre el acceso directo del Escritorio o ejecuta "
                       "pythonw -m pendify (o python -m pendify para verlo en una consola).",
    },
    "en": {
        "title": "Alerts from this PC",
        "intro": "This program sends its alerts to your Pendi account. For that, this PC has to be linked to your "
                 "account.",
        "scan": "Scan this code with the camera of the phone that holds your account and confirm the link.",
        "code_label": "This PC's key:",
        "show_code": "Show the code",
        "hide_code": "Hide the code",
        "mask": "Key hidden",
        "fold": "More options: type the link values, or forget this PC",
        "credit": "Created by",
        "sponsor_by": "Sponsored by Pendiapp.com",
        "sponsor_cap": "Scan it to open pendiapp.com",
        "sponsor_qr": "QR code of pendiapp.com",
        "sponsor_note": "This PC is already linked, so its code is no longer shown. «Forget this PC», under «More "
                        "options», makes a new code to link.",
        "theme_toggle": "Switch between light and dark theme",
        "language": "Language",
        "state_waiting": "Waiting for the confirmation on the phone.",
        "state_wait": "The service asked to wait a moment. It will ask again by itself.",
        "state_offline": "Could not connect. It will try again.",
        "state_paused": "Stopped asking. Press «Check now» to go on.",
        "state_linked": "This PC is linked. Alerts will reach your account.",
        "state_linked_paused": "This PC is linked. Alerts are paused.",
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
        "watch_paused": "Paused: this PC is not reading the game or alerting your phone.",
        "watch_last": "Last alert: {what}, at {time}.",
        "alert_loading": "the loading screen started",
        "alert_queue": "match found",
        "alert_started": "the match started",
        "phase_none": "No game",
        "phase_lobby": "In the lobby",
        "phase_matchmaking": "Looking for a match",
        "phase_readycheck": "Match found",
        "phase_champselect": "Choosing",
        "phase_inprogress": "In a game",
        "phase_endofgame": "Game over",
        "phase_other": "Other state",
        "ping_sent": "sent",
        "ping_refused": "refused by your account",
        "ping_not_delivered": "not delivered",
        "ping_failed": "sending failed",
        "watch_phase": "Now: {phase}.",
        "watch_ping": "Alert to the phone: {result}, at {time}.",
        "log_started": "The program started.",
        "log_waiting": "Waiting for the game client.",
        "log_connected": "Connected to the game client.",
        "log_lost": "Lost the game client. Looking for it again.",
        "log_accepted": "Match accepted.",
        "log_loading": "Loading screen: waiting for the match to start.",
        "log_match_started": "The match started.",
        "log_ping": "Alert to the phone: {result}.",
        "log_paused": "Alerts paused.",
        "log_resumed": "Alerts resumed.",
        "log_title": "Activity",
        "log_help": "What the program saw and alerted since it started on this PC.",
        "quit": "Quit",
        "this_pc": "This PC",
        "pause": "Pause alerts",
        "resume": "Resume alerts",
        "autostart_label": "Start with Windows",
        "autostart_help": "When the PC turns on, the program starts by itself, without opening this page.",
        "stopped": "The program stopped: it no longer watches the game client or sends alerts.",
        "start_again": "To start it again, open the shortcut on the Desktop or run pythonw -m pendify "
                       "(or python -m pendify to see it in a console).",
    },
}

# The phase as the client names it, to its word; any other name reads phase_other.
PHASE_WORDS = {"None": "phase_none", "Lobby": "phase_lobby", "Matchmaking": "phase_matchmaking",
               "ReadyCheck": "phase_readycheck", "ChampSelect": "phase_champselect", "InProgress": "phase_inprogress",
               "EndOfGame": "phase_endofgame", "PreEndOfGame": "phase_endofgame", "WaitingForStats": "phase_endofgame"}
PING_RESULTS = ("sent", "refused", "not_delivered", "failed")
# The log's lines (lane pclog round 1, the words of briefs/pclog-design-2026-10-01.md): each kind the watcher notes to
# its word; a phase reads its phase word and a period, a ping its result's word. Lost, the pause and a ping that was
# not sent are flagged, so a drawing can mark a failure.
LOG_WORDS = {"started": "log_started", "waiting": "log_waiting", "connected": "log_connected", "lost": "log_lost",
             "accepted": "log_accepted", "loading": "log_loading", "match_started": "log_match_started",
             "paused": "log_paused", "resumed": "log_resumed"}
LOG_WARNS = frozenset({"lost", "paused"})
# A line of these kinds ends the phase collapse: the same phase after a reconnect or a resume is shown again.
LOG_PHASE_BREAKS = frozenset({"lost", "connected", "paused", "resumed"})

# The design's stylesheet (page design, round 2 of 2026-09-30): light and dark by the system's choice, system
# fonts only, nothing loaded. Its form[action=...] selectors quote the value with ' so no page carries the text
# action="/relink" or action="/quit" of a form it does not show.
# The theme's colours, light and dark. The system's choice sets them; the theme button's choice, kept in data-theme
# on the html element, overrides it in either direction; with no choice stored the system's governs (pendiapp.com's
# rule, assets/tokens.css).
_LIGHT = ("--bg:#FAF8FE;--card:#FFFFFF;--tint:#EFEAF8;--ink:#1E1533;--ink2:#574E70;"
          "--line:#D8D0EA;--hair:#ECE6F7;--brand:#6D28D9;--on-brand:#FFFFFF;--tonal:#E9DEFB;--on-tonal:#4C1D95;"
          "--danger:#C21F45;--danger-bg:#F6DDE3;--on-danger-bg:#671025;--ring:rgba(109,40,217,.24);--hover:rgba(30,21,"
          "51,.06);--px-line:#1E1533;--shadow:0 1px 2px rgba(30,21,51,.05),0 12px 32px rgba(30,21,51,.07);")
_DARK = ("--bg:#131022;--card:#1E1A31;--tint:#272138;--ink:#ECE8F6;"
         "--ink2:#A79FC2;--line:#3A3452;--hair:#2B2740;--brand:#C3B1F7;--on-brand:#24124F;--tonal:#40277C;"
         "--on-tonal:#E9DEFB;--danger:#FB7196;--danger-bg:#853C50;--on-danger-bg:#FEEAEF;--ring:rgba(195,177,247,.30);"
         "--hover:rgba(255,255,255,.08);--px-line:#0D0A18;--shadow:0 1px 2px rgba(0,0,0,.30),0 16px 40px rgba(0,0,0,"
         ".32);")
_STYLE = (":root{color-scheme:light dark;" + _LIGHT + "}\n"
          "@media (prefers-color-scheme:dark){:root{" + _DARK + "}}\n"
          ':root[data-theme="light"]{color-scheme:light;' + _LIGHT + "}\n"
          ':root[data-theme="dark"]{color-scheme:dark;' + _DARK + "}\n"
          "*{box-sizing:border-box}\n"
          "body{margin:0;padding:32px 16px 40px;background:var(--bg);color:var(--ink);font:400 16px/24px system-ui,"
          "sans-serif;-webkit-font-smoothing:antialiased}\n"
          "body>h1,body>p{max-width:40rem;margin-inline:auto}\n"
          ".page{max-width:1040px;margin:0 auto}\n"
          "h1{margin:0 0 8px;font-size:24px;line-height:32px;font-weight:600;letter-spacing:-.01em}\n"
          ".title-row{display:flex;align-items:center;gap:12px;margin:0 0 8px}.title-row h1{margin:0}\n"
          ".title-row img{flex:none;image-rendering:pixelated}\n"
          ".intro{margin:0 0 24px;max-width:46ch;color:var(--ink2)}\n"
          "#state{display:flex;gap:12px;align-items:flex-start;max-width:40rem;margin:0;padding:12px 16px;"
          "border-radius:12px;background:var(--tint);font-weight:500}\n"
          '#state::before{content:"";flex:none;width:10px;height:10px;margin-top:7px;border-radius:50%;'
          "background:var(--brand)}\n"
          '#state[data-shown="falsefalse"]{padding:16px 20px;background:var(--tonal);color:var(--on-tonal);'
          "font-size:20px;line-height:28px;font-weight:600}\n"
          '#state[data-shown="falsefalse"]::before{width:8px;height:15px;margin:3px 4px 0 6px;border-radius:0;'
          "background:none;border:solid currentColor;border-width:0 3px 3px 0;transform:rotate(45deg)}\n"
          '#state[data-shown="falsetrue"]{background:var(--danger-bg);color:var(--on-danger-bg)}\n'
          '#state[data-shown="falsetrue"]::before{background:none;border:2px solid currentColor}\n'
          "#watch{margin:12px 0 0;max-width:40rem;font-size:14px;line-height:20px;color:var(--ink2)}\n"
          ".scan{margin:24px 0 16px;max-width:40ch}\n"
          ".note{margin:12px 0 0;font-size:14px;line-height:20px;color:var(--ink2)}\n"
          ".note.warn{color:var(--danger)}\n"
          ".key-card{display:grid;justify-items:center;gap:16px;margin:0 0 16px;padding:20px;border-radius:16px;"
          "background:var(--card);border:1px solid var(--hair);box-shadow:var(--shadow)}\n"
          ".qr{display:block;width:100%;max-width:288px;height:auto;border-radius:12px;"
          "box-shadow:0 0 0 1px var(--hair)}\n"
          ".key{display:grid;gap:2px;margin:0}\n"
          ".key-label{font-size:12px;line-height:16px;font-weight:500;color:var(--ink2)}\n"
          # The header's actions as pendiapp.com's .nav-actions holds them: the language switch, then the theme button.
          ".bar{display:flex;justify-content:flex-end;align-items:center;gap:.45rem;margin:0 0 8px}\n"
          # pendiapp.com's language switch (assets/styles.css, .langsw), its variables mapped to the page's own:
          # --hairline to --hair, --muted to --ink2, --brand and --on-brand as they are. Each entry is a submit
          # button, so the page's button look is set back to the site's link: no minimum height, no border, no fill,
          # the group's font; the page's button focus ring stays. The switch is one form, the pill: .langsw's inline
          # flex and the page's form{margin:0} already sit it inline as the span did, so no rule is added for it.
          ".langsw{display:inline-flex;border:1px solid var(--hair);border-radius:999px;padding:2px;font-size:.8rem;"
          "font-weight:600}\n"
          ".langsw button{min-height:0;padding:.28rem .62rem;border:0;border-radius:999px;background:transparent;"
          "color:var(--ink2);font:inherit}\n"
          '.langsw button[aria-current="true"]{background:var(--brand);color:var(--on-brand)}\n'
          ".icon-btn{display:inline-grid;place-items:center;width:44px;height:44px;min-height:0;padding:0;"
          "border-radius:999px;border:1px solid var(--hair);background:transparent;color:var(--ink);cursor:pointer;"
          "transition:background .2s cubic-bezier(.23,1,.32,1),transform .2s cubic-bezier(.23,1,.32,1)}\n"
          ".icon-btn:hover{background:var(--tint);box-shadow:none}\n"
          ".icon-btn:active{transform:scale(.94)}\n"
          ".icon-btn .sun{display:none}\n"
          ".icon-btn .moon{display:block}\n"
          '@media (prefers-color-scheme:dark){:root:not([data-theme="light"]) .icon-btn .sun{display:block}'
          ':root:not([data-theme="light"]) .icon-btn .moon{display:none}}\n'
          ':root[data-theme="dark"] .icon-btn .sun{display:block}\n'
          ':root[data-theme="dark"] .icon-btn .moon{display:none}\n'
          ':root[data-theme="light"] .icon-btn .sun{display:none}\n'
          ':root[data-theme="light"] .icon-btn .moon{display:block}\n'
          '.code{font:600 22px/28px ui-monospace,"Cascadia Mono",Consolas,monospace;letter-spacing:.12em;'
          "font-variant-numeric:tabular-nums}\n"
          ".party{display:flex;justify-content:center;align-items:flex-end;gap:18px;width:100%;padding-top:4px;"
          "border-bottom:2px solid var(--line)}\n"
          ".px{display:block;width:48px;height:48px}\n"
          ".pl{fill:var(--px-line)}\n"
          "form{margin:0}\n"
          "button{min-height:44px;padding:10px 24px;border:1px solid transparent;"
          "border-radius:999px;background:var(--tonal);color:var(--on-tonal);font:500 14px/20px system-ui,"
          "sans-serif;cursor:pointer;transition:transform 160ms cubic-bezier(.23,1,.32,1),box-shadow 160ms ease}\n"
          "button:hover{box-shadow:inset 0 0 0 999px var(--hover)}\n"
          "button:active{transform:scale(.97)}\n"
          "button:focus-visible{outline:2px solid var(--brand);outline-offset:2px}\n"
          "form[action='/check'] button,form[action='/relink'] button{width:100%;background:var(--brand);"
          "color:var(--on-brand)}\n"
          "form[action='/forget'] button{background:transparent;border-color:var(--line);color:var(--danger)}\n"
          "form[action='/quit'] button{padding-inline:16px;background:transparent;color:var(--ink2)}\n"
          ".more{display:grid;gap:16px;margin-top:32px}\n"
          ".panel{padding:20px;border-radius:16px;background:var(--card);border:1px solid var(--hair)}\n"
          ".panel h2{margin:0 0 16px;font-size:16px;line-height:24px;font-weight:600}\n"
          ".panel p{margin:0 0 16px;font-size:14px;line-height:20px;color:var(--ink2)}\n"
          ".panel .note{margin:12px 0 0}\n"
          "form[action='/typed']{display:grid;gap:12px}\n"
          "label{display:grid;gap:6px;font-size:14px;line-height:20px;font-weight:500;color:var(--ink2)}\n"
          "input{width:100%;height:44px;padding:0 14px;border:1px solid var(--line);border-radius:12px;"
          'background:var(--bg);color:var(--ink);font:500 16px/24px ui-monospace,"Cascadia Mono",Consolas,monospace;'
          "letter-spacing:.08em;text-transform:uppercase}\n"
          "input:focus{outline:none;border-color:var(--brand);box-shadow:0 0 0 3px var(--ring)}\n"
          "form[action='/typed'] button{justify-self:start;margin-top:4px}\n"
          ".foot{display:flex;justify-content:flex-end;margin-top:24px;padding-top:12px;"
          "border-top:1px solid var(--hair)}\n"
          '@media (prefers-reduced-motion:no-preference){#state[data-shown^="true"]::before{animation:breathe 2.4s '
          "ease-in-out infinite}}\n"
          "@keyframes breathe{50%{opacity:.3}}\n"
          "@media (min-width:880px){body{padding:56px 32px 48px}h1{font-size:36px;line-height:44px}"
          "form[action='/check'] button,form[action='/relink'] button{width:auto}"
          ".more{grid-template-columns:1fr 1fr;gap:24px;margin-top:48px}.panel{padding:24px}"
          "form[action='/typed']{grid-template-columns:1fr 1fr}form[action='/typed'] button{grid-column:1/-1}}\n"
          # The card «Este PC» of placement A (controls design, round 2 of 2026-10-01, its separate style block
          # without placement B's rules): the resume in the filled pair of «Comprobar ahora», the paused watcher line
          # in the refused pair with a two-bar mark, the switch of the start with Windows under a hairline.
          "form[action='/resume'] button{background:var(--brand);color:var(--on-brand)}\n"
          "#watch[data-paused]{display:flex;gap:12px;align-items:flex-start;padding:12px 16px;border-radius:12px;"
          "background:var(--danger-bg);color:var(--on-danger-bg);font-weight:500}\n"
          '#watch[data-paused]::before{content:"";flex:none;width:10px;height:12px;margin-top:4px;'
          "border:solid currentColor;border-width:0 3px}\n"
          ".switch{display:flex;gap:12px;align-items:flex-start;font-weight:400;cursor:pointer}\n"
          ".switch input{-webkit-appearance:none;appearance:none;flex:none;width:52px;height:32px;margin:0;padding:0;"
          "border:2px solid var(--ink2);border-radius:999px;background:radial-gradient(circle at 14px 50%,var(--ink2) "
          "8px,transparent 8.5px) var(--tint);cursor:pointer}\n"
          ".switch input:checked{border-color:var(--brand);background:radial-gradient(circle at 34px 50%,"
          "var(--on-brand) 8px,transparent 8.5px) var(--brand)}\n"
          ".switch input:focus{border-color:var(--ink2);box-shadow:none}\n"
          ".switch input:checked:focus{border-color:var(--brand)}\n"
          ".switch input:focus-visible{outline:2px solid var(--brand);outline-offset:2px}\n"
          ".sw-text{display:grid;gap:2px;padding-top:6px}\n"
          ".sw-label{font-weight:500;color:var(--ink)}\n"
          ".sw-help{color:var(--ink2)}\n"
          ".pc{display:grid;gap:16px;max-width:40rem;margin:24px 0 0}\n"
          ".pc h2{margin:0}\n"
          ".pc form{justify-self:start}\n"
          ".pc .switch{padding-top:16px;border-top:1px solid var(--hair)}\n"
          # The card «Actividad» of form A (log design, round 2 of 2026-10-01, its separate style block without
          # candidate B's rules): a list of 224 px that scrolls inside the card, each line its time in tabular figures
          # beside its text, a failure's text in the danger colour, the focus ring of the page's buttons.
          ".log-card{max-width:40rem;margin:16px 0 0}\n"
          ".log-card h2{margin:0 0 4px}\n"
          ".log{max-height:224px;overflow-y:auto;overscroll-behavior:contain;border-radius:12px}\n"
          ".log:focus-visible{outline:2px solid var(--brand);outline-offset:2px}\n"
          ".log ol{margin:0;padding:0;list-style:none}\n"
          ".log li{display:grid;grid-template-columns:max-content minmax(0,1fr);column-gap:16px;padding:4px 0;"
          "font-size:14px;line-height:20px}\n"
          ".log time{color:var(--ink2);font-variant-numeric:tabular-nums}\n"
          ".log .warn{color:var(--danger)}\n")
# The design sheet of 2026-10-01, round 2: its rules as drawn, after every rule above as its own block was, with the
# seat's corrections after its review: the sponsored QR's side follows its column, the watcher line keeps its 40rem
# unless paused, ten explicit rows before the last, the fold's paragraphs capped at 71ch, and the four moves'
# keyframes inside the reduced-motion guard with the rules that run them.
# Width: 85 % of the window at every desktop width, never narrower than 1040 px, no upper cap; the code's column grows
# with the window and the code with it, up to 480 px. Column 1 is one measure: two tracks and a 24 px gutter, every
# block spans both and the boxes lose their 40rem cap, while the two paragraphs keep theirs (.intro 46ch, .scan 40ch).
# From a 1600 px window «Este PC» and «Actividad» share one row, a track each, as the two panels of the fold do; dense
# placement draws the log card beside «Este PC» while the markup keeps its order, so the narrow page is unchanged.
_STYLE += ("@media (min-width:880px){.page{max-width:max(1040px,85vw)}\n"
           ".link:has(.key-card),.link:has(.side){display:grid;grid-template-columns:minmax(0,1fr) 24px minmax(0,1fr) "
           "64px clamp(340px,34vw,522px);grid-template-rows:repeat(10,auto) 1fr;grid-auto-flow:row dense;"
           "column-gap:0}\n"
           ".link>*{grid-column:1/4}\n"
           ".link>.key-card,.link>.side{grid-column:5;grid-row:1/-1;align-self:start;margin:0}\n"
           ".link:has(.key-card)>:is(#state,#watch[data-paused],.pc,.log-card),"
           ".link:has(.side)>:is(#state,#watch[data-paused],.pc,.log-card){max-width:none}\n"
           ".key-card .qr{max-width:480px}}\n"
           "@media (min-width:1600px){.link:has(>.pc):has(>.log-card)>.pc{grid-column:1;align-content:start}\n"
           ".link:has(>.pc):has(>.log-card)>.log-card{grid-column:3;margin-top:24px}}\n"
           # The code is shown; the key sits under the mask until «Mostrar el código» (the script sets data-shown).
           ".key{justify-items:center;text-align:center}\n"
           ".key-val{display:grid;place-items:center;min-height:32px}\n"
           ".key-val>*{grid-area:1/1}\n"
           '.key[data-shown="false"] .code{visibility:hidden}\n'
           '.key[data-shown="true"] .mask{visibility:hidden}\n'
           ".mask{display:flex;align-items:flex-end;gap:10px}\n"
           ".mask .grp{display:flex;align-items:flex-end;gap:2px}\n"
           ".mask svg{display:block;flex:none}\n"
           # Each figure its own move, once every 30 s, for as long as the page is open; still when the system asks
           # for less motion.
           "@media (prefers-reduced-motion:no-preference){\n"
           ".px{transform-origin:50% 100%}\n"
           ".px:nth-child(1){animation:px-archer 30s steps(1,end) infinite}\n"
           ".px:nth-child(2){animation:px-knight 30s steps(1,end) infinite}\n"
           ".px:nth-child(3){animation:px-mage 30s steps(1,end) infinite}\n"
           ".px:nth-child(4){animation:px-creature 30s steps(1,end) infinite}\n"
           "@keyframes px-archer{0%{transform:none}2%{transform:scaleX(-1)}5%,100%{transform:none}}\n"
           "@keyframes px-knight{0%{transform:none}5%{transform:translateX(3px)}6.5%{transform:translateX(6px)}"
           "8%{transform:translateX(3px)}9.5%,100%{transform:none}}\n"
           "@keyframes px-mage{0%{transform:none}10%{transform:translateY(-3px)}11.5%{transform:translateY(-6px)}"
           "16%{transform:translateY(-3px)}17.5%,100%{transform:none}}\n"
           "@keyframes px-creature{0%{transform:none}18%{transform:translate(3px,-6px)}"
           "19%{transform:translate(3px,-3px)}20%{transform:translate(0,-6px)}21%{transform:translate(0,-3px)}"
           "22%{transform:translate(-3px,-6px)}23%{transform:translate(-3px,-3px)}24%,100%{transform:none}}}\n"
           # The typed link and «Olvidar este PC» in one panel, folded.
           ".fold{margin-top:32px;border-radius:16px;background:var(--card);border:1px solid var(--hair)}\n"
           ".fold>summary{display:flex;align-items:center;justify-content:space-between;gap:16px;min-height:56px;"
           "padding:16px 20px;border-radius:15px;list-style:none;cursor:pointer;font-weight:500;"
           "transition:box-shadow 160ms ease}\n"
           ".fold>summary::-webkit-details-marker{display:none}\n"
           '.fold>summary::after{content:"";flex:none;width:8px;height:8px;margin:-4px 4px 0 0;border:solid '
           "var(--ink2);border-width:0 2px 2px 0;transform:rotate(45deg)}\n"
           ".fold[open]>summary::after{margin-top:4px;transform:rotate(-135deg)}\n"
           "@media (prefers-reduced-motion:no-preference){.fold>summary::after{transition:transform 200ms "
           "cubic-bezier(.23,1,.32,1),margin 200ms cubic-bezier(.23,1,.32,1)}}\n"
           ".fold>summary:hover{box-shadow:inset 0 0 0 999px var(--hover)}\n"
           ".fold>summary:focus-visible{outline:2px solid var(--brand);outline-offset:2px}\n"
           ".fold[open]>summary{border-radius:15px 15px 0 0}\n"
           ".fold .more{margin:0;padding:0 20px 20px;gap:0;border-top:1px solid var(--hair)}\n"
           ".fold .panel{padding:20px 0 0;border:0;border-radius:0;background:none}\n"
           ".fold .panel+.panel{margin-top:20px;border-top:1px solid var(--hair)}\n"
           ".fold .panel p{max-width:71ch}\n"
           "@media (min-width:880px){.fold{margin-top:48px}.fold>summary{padding:16px 24px}.fold .more{padding:0 24px "
           "24px;gap:0 48px}.fold .panel{padding:24px 0 0}.fold .panel+.panel{margin:0;padding-left:48px;border-top:0;"
           "border-left:1px solid var(--hair)}}\n"
           # The program's name in the title row, and the sentence it took the place of under it.
           ".tagline{margin:0 0 16px;font-size:16px;line-height:24px;font-weight:500;color:var(--ink)}\n"
           "@media (min-width:880px){.tagline{font-size:18px;line-height:28px}}\n"
           # The creator and the repository at the foot, «Salir» stays right.
           ".foot{justify-content:space-between;align-items:center;flex-wrap:wrap;gap:12px 24px}\n"
           ".credit{margin:0;font-size:14px;line-height:20px;color:var(--ink2)}\n"
           ".credit b{font-weight:600;color:var(--ink)}\n"
           ".credit a{color:var(--brand);text-decoration:underline;text-decoration-thickness:1px;"
           "text-underline-offset:3px;border-radius:4px}\n"
           ".credit a:hover{text-decoration-thickness:2px}\n"
           ".credit a:focus-visible{outline:2px solid var(--brand);outline-offset:2px}\n"
           # The linked page keeps its right column: a QR to pendiapp.com in a pixel arcade frame of its own, the four
           # figures under it, and why the pairing code is gone. The QR is dark on light in both themes; from 880 px
           # its side follows the column, so the frame never runs past the page.
           ".side{display:grid;gap:16px;margin:32px 0 0}\n"
           ".sponsor{display:grid;justify-items:center;gap:16px;margin:0;padding:20px;border-radius:16px;"
           "background:var(--card);border:1px solid var(--hair);box-shadow:var(--shadow)}\n"
           ".sponsor-by{position:relative;z-index:1;margin:0 0 -32px;padding:6px 18px 9px;background:#8E3D96;"
           'color:#FFFFFF;font:600 12px/16px ui-monospace,"Cascadia Mono",Consolas,monospace;letter-spacing:.02em;'
           "clip-path:polygon(0 0,100% 0,100% 4px,calc(100% - 4px) 4px,calc(100% - 4px) calc(100% - 4px),"
           "100% calc(100% - 4px),100% 100%,0 100%,0 calc(100% - 4px),4px calc(100% - 4px),4px 4px,0 4px);"
           "box-shadow:inset 0 -3px 0 #40277C}\n"
           ".arcade{position:relative;padding:28px 20px 20px;background:#6D28D9;box-shadow:inset 4px 4px 0 #C3B1F7,"
           "inset -4px -4px 0 #40277C;clip-path:polygon(8px 0,calc(100% - 8px) 0,calc(100% - 8px) 4px,"
           "calc(100% - 4px) 4px,calc(100% - 4px) 8px,100% 8px,100% calc(100% - 8px),calc(100% - 4px) "
           "calc(100% - 8px),calc(100% - 4px) calc(100% - 4px),calc(100% - 8px) calc(100% - 4px),calc(100% - 8px) "
           "100%,8px 100%,8px calc(100% - 4px),4px calc(100% - 4px),4px calc(100% - 8px),0 calc(100% - 8px),0 8px,"
           "4px 8px,4px 4px,8px 4px)}\n"
           ".arcade i{position:absolute;width:8px;height:8px;background:#FBBE3C;"
           "box-shadow:inset -2px -2px 0 #A5510B}\n"
           ".arcade i:nth-of-type(1){top:6px;left:6px}.arcade i:nth-of-type(2){top:6px;right:6px}"
           ".arcade i:nth-of-type(3){bottom:6px;left:6px}.arcade i:nth-of-type(4){bottom:6px;right:6px}\n"
           ".qr2{display:block;width:264px;height:264px;outline:4px solid var(--px-line)}\n"
           "@media (max-width:399px){.qr2{width:231px;height:231px}}\n"
           "@media (min-width:880px){.qr2{width:min(330px,calc(clamp(340px,34vw,522px) - 82px));"
           "height:min(330px,calc(clamp(340px,34vw,522px) - 82px))}}\n"
           ".sponsor-cap{margin:0;font-size:14px;line-height:20px;color:var(--ink2)}\n"
           ".side .note{margin:0}\n")
# Polls the state; reloads when what the page shows changes, the watcher's pause included (data-paused, served only
# beside a watcher), so a second tab follows a pause or a resume made in another; the start with Windows as well, on
# a page that draws the switch only, against the switch as it was rendered (defaultChecked, from enabled(), the read
# /state answers, so a reload cannot loop, and a poll landing between a change and its post's answer cannot reload
# the page under the post); the language too, against the one the page was drawn in (<html lang>), so a second tab
# or another browser turns to a language chosen with the switch; says so when the program is gone.
# While the program is gone the state line's data-shown is a value no style rule names, so the look of what the page
# showed (the linked page's check mark) never sits beside the closed sentence; an answer puts the load value back.
_SCRIPT = ("const s=document.getElementById('state');const w=document.getElementById('watch');"
           "const shown=s.dataset.shown;const paused=s.dataset.paused;const lang=document.documentElement.lang;"
           "const sw=document.querySelector('input[name=autostart]');"
           # The card «Actividad»'s list is a tab stop only while its lines are taller than its box, read at load and
           # after each insert (the design review's open item 3: a stop on a list that does not scroll is dead).
           "const g=document.querySelector('.log');function tabStop(){if(g.scrollHeight>g.clientHeight)"
           "g.setAttribute('tabindex','0');else g.removeAttribute('tabindex');}if(g)tabStop();"
           "setInterval(()=>fetch('/state').then(r=>r.json()).then(j=>{s.textContent=j.text;s.dataset.shown=shown;"
           "if(w&&j.watchText)w.textContent=j.watchText;"
           "if(String(j.showCode)+String(j.relinkOffered)!==shown||(paused!==undefined&&String(j.paused)!==paused)"
           "||j.lang!==lang||(sw&&j.autostart!==undefined&&String(j.autostart)!==String(sw.defaultChecked)))"
           "location.reload();"
           # The log's lines above the highest seq the card has drawn go on top, oldest first so the newest ends
           # first, built as elements with their text (never as markup); the card keeps the last 50, as the ring
           # does. An answer with no log changes nothing.
           "if(g&&j.log){const o=g.firstElementChild;let last=Number(g.dataset.seq);"
           "for(const l of j.log){if(l.seq<=last)continue;const li=document.createElement('li');"
           "const t=document.createElement('time');t.setAttribute('datetime',l.time);t.textContent=l.time;"
           "const x=document.createElement('span');if(l.warn)x.className='warn';x.textContent=l.text;"
           "li.append(t,x);o.insertBefore(li,o.firstChild);last=l.seq;}g.dataset.seq=String(last);"
           "while(o.children.length>50)o.lastElementChild.remove();tabStop();}})"
           ".catch(()=>{s.textContent=s.dataset.closed;s.dataset.shown='closed';}),5000);"
           # The key shows for a minute: the reveal sets data-shown on the key, which swaps the mask for the code, and
           # flips its own label; 60 s after a show the key is masked again; one timer, cleared on every press, so a
           # hide by hand leaves none running. The sheet's function is named showKey here, since the script's first
           # line already declares a const named shown.
           "const k=document.querySelector('.key'),r=document.querySelector('.reveal');let hide;"
           "function showKey(on){k.dataset.shown=String(on);r.textContent=on?r.dataset.hide:r.dataset.show;"
           "clearTimeout(hide);if(on)hide=setTimeout(()=>showKey(false),60000);}"
           "if(r)r.addEventListener('click',()=>showKey(k.dataset.shown!=='true'));"
           # The theme button (pendiapp.com's assets/theme.js): the choice goes to data-theme and to localStorage,
           # and it is posted to /theme, which keeps it in config.json: the page's port changes on every run, so
           # its localStorage is a new origin each time. The server's value, served as data-theme, wins on the next
           # page: localStorage is the fallback only while config.json holds no theme. A post whose write fails is
           # answered as a failed save is (303 to the page, which says the file could not be written), and the
           # choice then lasts until the next page, which keeps the config's theme.
           "(function(){var btn=document.getElementById('theme-toggle');if(!btn)return;"
           "function current(){var t=document.documentElement.getAttribute('data-theme');"
           "if(t==='light'||t==='dark')return t;return matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light';}"
           "function reflect(){btn.setAttribute('aria-pressed',String(current()==='dark'));}reflect();"
           "btn.addEventListener('click',function(){var next=current()==='dark'?'light':'dark';"
           "document.documentElement.setAttribute('data-theme',next);"
           "try{localStorage.setItem('pendi-theme',next);}catch(e){}reflect();"
           "var f=document.querySelector('input[name=token]');"
           "if(f)fetch('/theme',{method:'POST',body:new URLSearchParams({token:f.value,theme:next})})"
           ".catch(function(){});});})();"
           # The switch of the start with Windows posts its change as the theme button posts, then the page is read
           # again, so the switch shows what the registry holds; a post that fails writes nothing, so the switch is put
           # back as it was, nothing is reloaded, and the poll says the program is gone.
           "(function(){var sw=document.querySelector('input[name=autostart]');if(!sw)return;"
           "sw.addEventListener('change',function(){var f=document.querySelector('input[name=token]');"
           "fetch('/autostart',{method:'POST',body:new URLSearchParams({token:f.value,on:sw.checked?'1':'0'})})"
           ".then(function(){location.reload();}).catch(function(){sw.checked=!sw.checked;});});})();")
# Read in <head> before the first paint, so a stored theme choice never flashes the other theme (pendiapp.com's
# index.html head script). The choice kept in config.json, served as data-theme on <html>, wins: localStorage is
# read only when the page came with none.
_THEME_READ = ("(function(){try{var e=document.documentElement;if(e.hasAttribute('data-theme'))return;"
               "var t=localStorage.getItem('pendi-theme');"
               "if(t==='light'||t==='dark')e.setAttribute('data-theme',t);}catch(x){}})();")
# The theme button's two icons, copied from pendiapp.com's header; the style shows the one of the other theme.
_THEME_ICONS = ('<svg class="moon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
                'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
                '<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z"/></svg>'
                '<svg class="sun" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
                'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
                '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 '
                '12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>')

# Four original 16 by 16 pixel figures under the key (an archer, a knight, a mage and a small winged creature), in
# the app's colours; 'o' is the outline, drawn in the theme's outline colour, and '.' is empty.
_FIGURE_COLOURS = {"p": "#8E3D96", "d": "#40277C", "w": "#E9DEFB", "v": "#6D28D9", "l": "#C3B1F7", "s": "#A79FC2",
                   "h": "#ECE8F6", "a": "#A5510B", "y": "#FBBE3C", "b": "#2A5AD6", "c": "#6BA6FB", "q": "#D8A9E0"}
_FIGURES = (
    ("....oooo........", "...oppppo....a..", "..opppppo....la.", "..oppppppo...l.a", ".oppdddddpo..l.a",
     ".opdwdddwdo..l.a", ".opdddddddo..l.a", ".oppdddddpo..l.a", "..oopppppoo..l.a", ".opovvvvvopwwl.a",
     ".opovvvvvoo..l.a", ".opoaaaaaoo..la.", ".opovvvvvop..a..", "..oovvvvvoo.....", "...odo.odo......",
     "...ooo.ooo......"),
    (".......pp.......", "......ppp.......", "....oooooooo....", "...ohhsssssso...", "...ohssssssso...",
     "...osolooloso...", "...ohssssssso...", "....osssssso....", "..ossssssooooooo", ".ohssssssovvvvvo",
     ".ohssssssovvyvvo", ".odddddddovydyvo", ".ohssssssovvyvvo", "..ossssssoovvvo.", "...oso.oso.ovo..",
     "...ooo.ooo..o..."),
    ("........oo......", ".......ovvo..oo.", "......ovvvo.oqyo", "......ovvvvooyyo", ".....ovvvvvo.oo.",
     "..oollllllllo.a.", "...owwwwwwo...a.", "...owdwwdwo...a.", "...ohhhhhho..wa.", "..oddhhhhddddwa.",
     "..odddhhdddoo.a.", "..oddddddddo..a.", "..oddllllddo..a.", ".oddddddddddo.a.", ".oddddddddddo.a.",
     ".oooooooooooo.a."),
    ("................", "................", "................", "........y.y.....", ".......oooooo...",
     "......occcbbo...", "..o...obbwobo...", ".oco..obbbbbbo..", ".occo.obbboooo..", ".ocllobbbbo.....",
     "..occobbwwbo....", "...oobwwwwbo....", ".o..obwwwwbbo...", ".ob.obwwwwbbo...", "..obobbbbbbbo...",
     "...ooooooooo...."),
)


def _paths(rows):
    """A pixel drawing's paths: one per colour, each a run of same-colour pixels per row."""
    runs = {}
    for y, row in enumerate(rows):
        x = 0
        while x < len(row):
            if row[x] == ".":
                x += 1
                continue
            start = x
            while x < len(row) and row[x] == row[start]:
                x += 1
            runs.setdefault(row[start], []).append(f"M{start} {y}h{x - start}v1h-{x - start}z")
    return "".join(f'<path class="pl" d="{"".join(d)}"/>' if key == "o" else
                   f'<path fill="{_FIGURE_COLOURS[key]}" d="{"".join(d)}"/>' for key, d in runs.items())


def _figure(rows):
    """One figure as an inline svg of 48 px."""
    return (f'<svg class="px" viewBox="0 0 {len(rows[0])} 16" width="48" height="48" shape-rendering="crispEdges">'
            f"{_paths(rows)}</svg>")


# The party holds the four figures and nothing else, so .px:nth-child(1) to (4) are the archer, the knight, the mage
# and the creature, each with its own move.
_PARTY = '<div class="party" aria-hidden="true">' + "".join(_figure(rows) for rows in _FIGURES) + "</div>"

# The mask over the key: one 8 by 8 head per character, the party's four in small (hood, helm, hat, creature), twelve
# in three groups of four. The heads are the same whatever the key, so the mask tells nothing of it; a screen reader
# reads the mask's name, never its drawings.
_HEADS = (
    ("..oooo..", ".oppppo.", "oppppppo", "opdddddo", "opdwdwdo", "opdddddo", ".oppppo.", "..oooo.."),
    ("...pp...", ".oooooo.", "ohhsssso", "ohssssso", "osooooso", "ohssssso", ".osssso.", "..oooo.."),
    ("...oo...", "..ovvo..", ".ovvvvo.", "ollllllo", ".owwwwo.", ".odwwdo.", ".owwwwo.", "..oooo.."),
    ("..y..y..", ".oooooo.", "obbbbbbo", "obwwbbbo", "obwobbco", "obbbbcco", ".obbbbo.", "..oooo.."),
)
_MASK_GROUP = ('<span class="grp">' + "".join('<svg class="mx" viewBox="0 0 8 8" width="16" height="16" '
                                              f'shape-rendering="crispEdges" aria-hidden="true">{_paths(rows)}</svg>'
                                              for rows in _HEADS) + "</span>")


# With scripts blocked the reveal does nothing, so the key reads as it did before the mask: the code shown, the mask
# and the reveal gone. The code's rule repeats the mask's own selector and comes later, so it wins.
_NOSCRIPT_STYLE = '.key[data-shown="false"] .code{visibility:visible}.mask,.reveal{display:none}'


def _mask(words):
    """The mask over the key, three groups of the four heads, named for a screen reader. `words` are escaped."""
    return f'<span class="mask" role="img" aria-label="{words["mask"]}">{_MASK_GROUP * 3}</span>'


def _sponsor_runs():
    """The sponsored QR's dark modules as one path's runs, one module tall, inside the quiet zone; and the side of
    the whole square, quiet zone included."""
    modules = qr.encode(SPONSOR_ADDRESS.encode("ascii")).modules
    runs = []
    for y, row in enumerate(modules):
        x = 0
        while x < len(row):
            if not row[x]:
                x += 1
                continue
            start = x
            while x < len(row) and row[x]:
                x += 1
            runs.append(f"M{start + qr.QUIET_ZONE} {y + qr.QUIET_ZONE}h{x - start}v1h-{x - start}z")
    return "".join(runs), len(modules) + 2 * qr.QUIET_ZONE


_SPONSOR_RUNS, _SPONSOR_SIDE = _sponsor_runs()


def _sponsor_qr(words):
    """The sponsored QR: square dark modules on a light field, the same in both themes. `words` are escaped."""
    return (f'<svg class="qr2" viewBox="0 0 {_SPONSOR_SIDE} {_SPONSOR_SIDE}" role="img" '
            f'aria-label="{words["sponsor_qr"]}" shape-rendering="crispEdges"><rect width="{_SPONSOR_SIDE}" '
            f'height="{_SPONSOR_SIDE}" fill="#FFFFFF"/><path fill="#1E1533" d="{_SPONSOR_RUNS}"/></svg>')


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


def _paused(snapshot):
    """True when the watcher's snapshot says it is paused."""
    return snapshot.get("paused") is True


def _state_word(snapshot, seen):
    """The state line's word, the same for the page and for its poll: linked with the watcher paused reads
    state_linked_paused, every other state its own."""
    if snapshot["state"] == "linked" and seen is not None and _paused(seen):
        return "state_linked_paused"
    return "state_" + snapshot["state"]


def _clock_time(at):
    """A wall time as the page says it, hours and minutes in this PC's zone."""
    return time.strftime("%H:%M", time.localtime(at))


def _log_time(at):
    """A wall time as the log says it, hours, minutes and seconds in this PC's zone."""
    return time.strftime("%H:%M:%S", time.localtime(at))


def _form(action, token, inner):
    return (f'<form method="post" action="/{action}"><input type="hidden" name="token" value="{token}">'
            f"{inner}</form>")


_CURRENT = ' aria-current="true"'


def _switch(words, token, lang):
    """pendiapp.com's language switch: ES then EN, the page's language marked aria-current. The page changes state
    only by a POST with its token, so the switch is one form to /lang that is the group, named «Idioma» or "Language"
    for a screen reader, and each entry is a submit button posting its own value. `words` are escaped already."""
    entries = "".join(f'<button type="submit" name="lang" value="{code}"{_CURRENT if code == lang else ""}>'
                      f"{code.upper()}</button>" for code in config.LANGS)
    return (f'<form class="langsw" role="group" aria-label="{words["language"]}" method="post" action="/lang">'
            f'<input type="hidden" name="token" value="{token}">{entries}</form>')


class PairingPage:
    def __init__(self, state, watch=None, on_quit=None, on_pause=None, on_resume=None, autostart=None, events=None):
        """`watch` answers the watcher's snapshot; without it the page shows no watcher line. `on_quit` stops
        the program; without it the page shows no quit button and /quit is no route. `on_pause` and `on_resume`
        pause and resume the watcher; without them /pause and /resume are no routes. `autostart` is the start
        with Windows; without it, or where it is not available, /autostart is no route and /state says nothing of
        it. Beside a watcher the page draws the card «Este PC» after the watcher line: the pause, or the resume while
        paused, and the switch of the start with Windows where it is available. `events` answers the watcher's
        events, oldest first; with it /state carries the log's lines and the page draws them in the card «Actividad»,
        without it no log key and no card."""
        self.state = state
        self.watch = watch
        self.on_quit = on_quit
        self.on_pause, self.on_resume = on_pause, on_resume
        self.autostart = autostart if autostart is not None and autostart.available else None
        self.events = events
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

    def language_of(self, accept_language):
        """The page's language, the one resolver of every page, /state and the stopped page: the switch's choice
        kept in the config file when there is one, else the browser's first Accept-Language tag (language())."""
        return self.state.lang() or language(accept_language)

    def watch_text(self, lang):
        """The watcher's line in `lang`: waiting or connected, then the phase while connected once one is read,
        then the last alert and its time, then the last ping's result and its time when a ping was made; None when
        no watcher runs beside the page. It names no game, no maker and no product."""
        if self.watch is None:
            return None
        return self._watch_text(self.watch(), lang)

    @staticmethod
    def _watch_text(snapshot, lang):
        """watch_text on one snapshot: paused, the paused sentence alone."""
        words = WORDS[lang]
        if _paused(snapshot):
            return words["watch_paused"]
        connected = snapshot.get("client") == "connected"
        parts = [words["watch_connected" if connected else "watch_waiting"]]
        # Connected with nothing read yet (None) prints no phase clause; the client's own "None" reads phase_none.
        if connected and snapshot.get("phase") is not None:
            phase = PHASE_WORDS.get(snapshot["phase"], "phase_other")
            parts.append(words["watch_phase"].format(phase=words[phase]))
        if snapshot.get("alert") in ("loading", "queue", "started") and snapshot.get("at") is not None:
            parts.append(words["watch_last"].format(what=words["alert_" + snapshot["alert"]],
                                                    time=_clock_time(snapshot["at"])))
        if snapshot.get("pingResult") in PING_RESULTS and snapshot.get("pingAt") is not None:
            parts.append(words["watch_ping"].format(result=words["ping_" + snapshot["pingResult"]],
                                                    time=_clock_time(snapshot["pingAt"])))
        return " ".join(parts)

    @staticmethod
    def _log_lines(events, lang):
        """The log's lines in `lang`, oldest first, each {seq, time, text, warn}. A phase whose word is the word of
        the last phase line kept is dropped, so the three phases that end a match give one line, until a line of
        LOG_PHASE_BREAKS is kept: the same phase after a reconnect or a resume is shown again. A kind or a ping
        result the page does not know is dropped."""
        words, lines, last_phase = WORDS[lang], [], None
        for seq, at, kind, detail in events:
            if kind == "phase":
                text = words[PHASE_WORDS.get(detail, "phase_other")] + "."
                if text == last_phase:
                    continue
                last_phase = text
            elif kind == "ping" and detail in PING_RESULTS:
                text = words["log_ping"].format(result=words["ping_" + detail])
            elif kind in LOG_WORDS:
                text = words[LOG_WORDS[kind]]
            else:
                continue
            lines.append({"seq": seq, "time": _log_time(at), "text": text,
                          "warn": kind in LOG_WARNS or (kind == "ping" and detail != "sent")})
            if kind in LOG_PHASE_BREAKS:
                last_phase = None
        return lines

    def _log_card(self, words, lang):
        """The card «Actividad» (form A, frames A1 to A6): its title, its helper, and the list named by both, the
        newest line first, carrying the highest seq it draws for the poll (0 before any line). `words` are escaped
        already. Rendered with no tab stop: the script sets one while the list scrolls."""
        lines = self._log_lines(self.events(), lang)
        items = []
        for line in reversed(lines):
            span = '<span class="warn">' if line["warn"] else "<span>"
            items.append(f'<li><time datetime="{line["time"]}">{line["time"]}</time>{span}{html.escape(line["text"])}'
                         f"</span></li>")
        last = lines[-1]["seq"] if lines else 0
        return (f'<div class="panel log-card"><h2 id="log-title">{words["log_title"]}</h2><p id="log-help">'
                f'{words["log_help"]}</p><div class="log" role="region" aria-labelledby="log-title" '
                f'aria-describedby="log-help" data-seq="{last}"><ol>{"".join(items)}</ol></div></div>')

    def state_json(self, lang):
        """The state for the page's poll: the state line's sentence, and beside a watcher its line and whether it
        is paused, and where the start with Windows is available whether it is on."""
        snapshot = self.state.snapshot()
        seen = self.watch() if self.watch is not None else None
        answer = {**snapshot, "text": WORDS[lang][_state_word(snapshot, seen)], "lang": lang}
        if seen is not None:
            answer["watchText"] = self._watch_text(seen, lang)
            answer["paused"] = _paused(seen)
        if self.autostart is not None:
            answer["autostart"] = self.autostart.enabled()
        if self.events is not None:
            answer["log"] = self._log_lines(self.events(), lang)
        return answer

    def render(self, lang):
        words = {key: html.escape(value) for key, value in WORDS[lang].items()}
        snapshot = self.state.snapshot()
        seen = self.watch() if self.watch is not None else None
        secret = self.state.secret_for_display() if snapshot["showCode"] else None
        token = html.escape(self.token)
        # Beside a watcher the state line carries the pause it was rendered with, which the poll compares.
        paused = "" if seen is None else f' data-paused="{str(_paused(seen)).lower()}"'
        # What this is and what to do, with the QR card beside it on a wide window; the two other roads in one fold
        # under it; the credit and quit at the foot. The title row reads the program's name, its sentence under it.
        link = [f'<div class="title-row"><img alt="" width="32" height="32" src="{ICON_URI}"><h1>{NAME}</h1>'
                f"</div>", f'<p class="tagline">{words["title"]}</p>', f'<p class="intro">{words["intro"]}</p>',
                f'<p id="state" role="status" data-shown="{str(snapshot["showCode"]).lower()}'
                f'{str(snapshot["relinkOffered"]).lower()}"{paused} data-closed="{words["state_closed"]}">'
                f'{words[_state_word(snapshot, seen)]}</p>']
        if snapshot["configFailed"]:
            link.append(f'<p class="note warn">{words["config_failed"]}</p>')
        if seen is not None:
            # While paused the watcher line is the paused block (data-paused); the card follows it.
            held = ' data-paused=""' if _paused(seen) else ""
            link += [f'<p id="watch" role="status"{held}>{html.escape(self._watch_text(seen, lang))}</p>',
                     self._card(words, token, _paused(seen))]
        # The card «Actividad» follows the card «Este PC»; on the page that still shows the code (frame A6) and on the
        # page that offers the relink it ends the first column instead, so neither the code nor the relink control
        # moves. No card without the events.
        log_card = None if self.events is None else self._log_card(words, lang)
        last = secret is not None or snapshot["relinkOffered"]
        if log_card is not None and not last:
            link.append(log_card)
        if secret is not None:
            modules = qr.encode(qr.pairing_address(secret).encode("ascii")).modules
            drawn = qr.scene_svg(modules, plate_almena.PLATE_DATA_URI, labelledby="scan")
            if drawn is None:  # the scene is drawn for version 3 only
                drawn = qr.svg(modules, labelledby="scan")
            link += [f'<p class="scan" id="scan">{words["scan"]}</p>',
                     # The code is shown (owner 2026-10-01); the key's text sits under the mask until its person
                     # presses the reveal, a real button whose label says what a press does; _SCRIPT masks the key
                     # again 60 s after a show.
                     f'<figure class="key-card">{drawn}<p class="key" data-shown="false"><span class="key-label">'
                     f'{words["code_label"]}</span> <span class="key-val">{_mask(words)}<span class="code" id="code">'
                     f'{codes.display(secret)}</span></span></p><button type="button" class="reveal" '
                     f'aria-controls="code" data-show="{words["show_code"]}" data-hide="{words["hide_code"]}">'
                     f'{words["show_code"]}</button>{_PARTY}</figure>',
                     _form("check", token, f'<button type="submit">{words["check"]}</button>')]
            if snapshot["buttonRefused"]:
                link.append(f'<p class="note">{words["button_wait"]}</p>')
        if snapshot["relinkOffered"]:
            link.append(_form("relink", token, f'<button type="submit">{words["relink"]}</button>'))
        if log_card is not None and last:
            link.append(log_card)
        if snapshot["linked"]:
            # The linked page keeps its right column: the sponsored QR with the party under it and, unless the relink
            # is offered (its state line says the account refuses this PC), why the pairing code is gone.
            note = "" if snapshot["relinkOffered"] else f'<p class="note">{words["sponsor_note"]}</p>'
            link.append(f'<aside class="side"><figure class="sponsor"><figcaption class="sponsor-by">'
                        f'{words["sponsor_by"]}</figcaption><div class="arcade"><i></i><i></i><i></i><i></i>'
                        f'{_sponsor_qr(words)}</div><p class="sponsor-cap">{words["sponsor_cap"]}</p>{_PARTY}'
                        f"</figure>{note}</aside>")
        typed = [f"<h2>{words['typed_title']}</h2>",
                 _form("typed", token, f'<label>{words["link_id_label"]} <input name="linkId" maxlength="32" '
                                       f'autocomplete="off"></label><label>{words["secret_label"]} <input '
                                       f'name="secret" maxlength="32" autocomplete="off"></label>'
                                       f'<button type="submit">{words["save"]}</button>')]
        if snapshot["typedRefused"]:
            typed.append(f'<p class="note warn">{words["typed_refused"]}</p>')
        forget = [f"<h2>{words['forget']}</h2>", f"<p>{words['forget_sentence']}</p>",
                  _form("forget", token, f'<button type="submit">{words["forget"]}</button>')]
        quit_button = f'<button type="submit">{words["quit"]}</button>'
        quit_form = "" if self.on_quit is None else _form("quit", token, quit_button)
        foot = (f'<footer class="foot"><p class="credit">{words["credit"]} <b>{CREATOR}</b> · <a href="{REPOSITORY}" '
                f'target="_blank" rel="noopener noreferrer">{REPOSITORY.removeprefix("https://")}</a></p>{quit_form}'
                "</footer>")
        # The typed link and the forget in one fold, open when the typed values were refused so the note shows.
        fold = " open" if snapshot["typedRefused"] else ""
        bar = (f'<header class="bar">{_switch(words, token, lang)}<button id="theme-toggle" class="icon-btn" '
               f'type="button" aria-pressed="false" '
               f'aria-label="{words["theme_toggle"]}">{_THEME_ICONS}</button></header>')
        theme = self.state.theme()
        kept = "" if theme is None else f' data-theme="{html.escape(theme)}"'
        body = (f'<main class="page">{bar}<section class="link">{"".join(link)}</section><details class="fold"{fold}>'
                f'<summary>{words["fold"]}</summary><section class="more"><div class="panel">{"".join(typed)}</div>'
                f'<div class="panel">{"".join(forget)}</div></section></details>{foot}</main>')
        return (f'<!doctype html><html lang="{lang}"{kept}><head><meta charset="utf-8"><meta name="viewport" '
                f'content="width=device-width, initial-scale=1"><title>{NAME}</title>{_TAB_ICON}<style>{_STYLE}'
                f"</style><noscript><style>{_NOSCRIPT_STYLE}</style></noscript><script>{_THEME_READ}</script></head>"
                f"<body>{body}<script>{_SCRIPT}</script></body></html>")

    def _card(self, words, token, paused):
        """The card «Este PC» (placement A, frames A1 to A6): the pause form, or the resume form while paused, then,
        where the start with Windows is available, its switch under a hairline, checked when the Run value exists.
        `words` are escaped already."""
        action = "resume" if paused else "pause"
        inner = [f'<h2 id="pc-title">{words["this_pc"]}</h2>',
                 _form(action, token, f'<button type="submit">{words[action]}</button>')]
        if self.autostart is not None:
            checked = " checked" if self.autostart.enabled() else ""
            inner.append(f'<label class="switch"><input type="checkbox" role="switch" name="autostart" '
                         f'aria-labelledby="sw-label" aria-describedby="sw-help"{checked}><span class="sw-text">'
                         f'<span class="sw-label" id="sw-label">{words["autostart_label"]}</span><span class="sw-help" '
                         f'id="sw-help">{words["autostart_help"]}</span></span></label>')
        return f'<div class="panel pc" role="group" aria-labelledby="pc-title">{"".join(inner)}</div>'

    def render_stopped(self, lang):
        """The one small page /quit answers: the program stopped, and how to start it again."""
        words = {key: html.escape(value) for key, value in WORDS[lang].items()}
        theme = self.state.theme()
        kept = "" if theme is None else f' data-theme="{html.escape(theme)}"'
        return (f'<!doctype html><html lang="{lang}"{kept}><head><meta charset="utf-8"><meta name="viewport" '
                f'content="width=device-width, initial-scale=1"><title>{NAME}</title>{_TAB_ICON}<style>{_STYLE}'
                f'</style><script>{_THEME_READ}</script></head><body><h1>{NAME}</h1><p>{words["stopped"]}</p>'
                f'<p>{words["start_again"]}</p></body></html>')

    def set_theme(self, choice):
        """/theme: the theme button's choice kept in the config file; "kept", or "refused" for a value that is
        not light, dark or system, which changes nothing, or "failed" for a config file that cannot be replaced,
        said as act() says it and answered as a failed save is."""
        try:
            self.state.set_theme(choice)
        except ValueError:
            return "refused"
        except config.ConfigError as failure:
            _say_failure(failure)
            self.state.config_failed()
            return "failed"
        return "kept"

    def set_lang(self, choice):
        """/lang: the switch's choice kept in the config file; "kept", or "refused" for a value that is not es or
        en, which changes nothing, or "failed" for a config file that cannot be replaced, said as act() says it."""
        try:
            self.state.set_lang(choice)
        except ValueError:
            return "refused"
        except config.ConfigError as failure:
            _say_failure(failure)
            self.state.config_failed()
            return "failed"
        return "kept"

    def set_autostart(self, on):
        """/autostart: the start with Windows turned on or off; a registry failure is said on stderr by the
        Autostart and changes nothing, and the page's next state says what holds."""
        if on:
            self.autostart.enable()
        else:
            self.autostart.disable()

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
            elif path == "/pause":
                self.on_pause()
            elif path == "/resume":
                self.on_resume()
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
            lang = page.language_of(self.headers.get("Accept-Language"))
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
            routes = ("/check", "/typed", "/forget", "/relink", "/theme", "/lang")
            routes += ("/quit",) if page.on_quit is not None else ()
            routes += ("/pause",) if page.on_pause is not None else ()
            routes += ("/resume",) if page.on_resume is not None else ()
            routes += ("/autostart",) if page.autostart is not None else ()
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
                # Answered before the stop, so the exit that follows never cuts the answer short; stopped even
                # when the answer cannot be written, and that failure goes on to the server's handle_error.
                try:
                    self._send(200, "text/html; charset=utf-8",
                               page.render_stopped(page.language_of(self.headers.get("Accept-Language"))))
                finally:
                    page.on_quit()
                return
            if path == "/theme":  # the page's script posts it and reads no page back
                outcome = page.set_theme(form.get("theme", ""))
                if outcome == "refused":
                    return self._refuse(400)
                if outcome == "kept":
                    return self._send(204, "text/plain; charset=utf-8", "")
                return self._send(303, "text/plain; charset=utf-8", "", (("Location", "/"),))
            if path == "/lang":  # es or en, then the page again, drawn in it; anything else changes nothing
                if page.set_lang(form.get("lang", "")) == "refused":
                    return self._refuse(400)
                return self._send(303, "text/plain; charset=utf-8", "", (("Location", "/"),))
            if path == "/autostart":  # on is 1 or 0; anything else changes nothing
                if form.get("on") not in ("1", "0"):
                    return self._refuse(400)
                page.set_autostart(form["on"] == "1")
                return self._send(303, "text/plain; charset=utf-8", "", (("Location", "/"),))
            page.act(path, form)
            self._send(303, "text/plain; charset=utf-8", "", (("Location", "/"),))

    return Handler
