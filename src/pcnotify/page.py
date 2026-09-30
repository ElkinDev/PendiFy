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

from . import codes, config, plate_almena, qr

ADDRESS = "127.0.0.1"
MAX_FORM_BYTES = 4096
# A POST's body is read up to this bound before any answer, refusals included: a socket closed with
# unread bytes in it is reset on Windows, and the client meets the reset in place of the answer.
DRAIN_BYTES = 64 * 1024
# With no usable length the socket is read up to DRAIN_BYTES while bytes keep coming, each read waiting this long.
DRAIN_WAIT = 0.1
# img-src data: is the QR scene's plate, one image inlined as a data URI; no image loads from any origin.
POLICY = ("default-src 'none'; img-src data:; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'; "
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
        "show_code": "Mostrar el código",
        "theme_toggle": "Cambiar entre tema claro y oscuro",
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
        "show_code": "Show the code",
        "theme_toggle": "Switch between light and dark theme",
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
        "quit": "Quit",
        "stopped": "The program stopped: it no longer watches the game client or sends alerts.",
        "start_again": "To start it again, open the shortcut on the Desktop or run pythonw -m pcnotify "
                       "(or python -m pcnotify to see it in a console).",
    },
}

# The phase as the client names it, to its word; any other name reads phase_other.
PHASE_WORDS = {"None": "phase_none", "Lobby": "phase_lobby", "Matchmaking": "phase_matchmaking",
               "ReadyCheck": "phase_readycheck", "ChampSelect": "phase_champselect", "InProgress": "phase_inprogress",
               "EndOfGame": "phase_endofgame", "PreEndOfGame": "phase_endofgame", "WaitingForStats": "phase_endofgame"}
PING_RESULTS = ("sent", "refused", "not_delivered", "failed")

# The design's stylesheet (mockup-pcnotify-page-r2-2026-09-30.html): light and dark by the system's choice, system
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
          ".key-card details{justify-self:stretch;text-align:center}\n"
          ".shown{display:grid;justify-items:center;gap:16px}\n"
          ".key{display:grid;gap:2px;margin:0}\n"
          ".key-label{font-size:12px;line-height:16px;font-weight:500;color:var(--ink2)}\n"
          ".key-card summary{display:inline-flex;align-items:center;list-style:none}\n"
          ".key-card summary::-webkit-details-marker{display:none}\n"
          ".key-card details[open] summary{margin-bottom:16px}\n"
          ".bar{display:flex;justify-content:flex-end;margin:0 0 8px}\n"
          ".icon-btn{display:inline-grid;place-items:center;width:38px;height:38px;min-height:0;padding:0;"
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
          "button,.key-card summary{min-height:44px;padding:10px 24px;border:1px solid transparent;"
          "border-radius:999px;background:var(--tonal);color:var(--on-tonal);font:500 14px/20px system-ui,"
          "sans-serif;cursor:pointer;transition:transform 160ms cubic-bezier(.23,1,.32,1),box-shadow 160ms ease}\n"
          "button:hover,.key-card summary:hover{box-shadow:inset 0 0 0 999px var(--hover)}\n"
          "button:active,.key-card summary:active{transform:scale(.97)}\n"
          "button:focus-visible,.key-card summary:focus-visible{outline:2px solid var(--brand);outline-offset:2px}\n"
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
          "ease-in-out infinite}.px{animation:hop .5s steps(1,end) 4}.px:nth-child(2){animation-delay:.12s}"
          ".px:nth-child(3){animation-delay:.24s}.px:nth-child(4){animation-delay:.36s}}\n"
          "@keyframes breathe{50%{opacity:.3}}\n"
          "@keyframes hop{50%{transform:translateY(-3px)}}\n"
          "@media (min-width:880px){body{padding:56px 32px 48px}h1{font-size:36px;line-height:44px}"
          ".link:has(.key-card){display:grid;grid-template-columns:minmax(0,1fr) 340px;grid-template-rows:repeat(9,"
          "auto) 1fr;column-gap:64px}.link>*{grid-column:1}.link>.key-card{grid-column:2;grid-row:1/-1;"
          "align-self:start;margin:0}form[action='/check'] button,form[action='/relink'] button{width:auto}"
          ".more{grid-template-columns:1fr 1fr;gap:24px;margin-top:48px}.panel{padding:24px}"
          "form[action='/typed']{grid-template-columns:1fr 1fr}form[action='/typed'] button{grid-column:1/-1}}\n")
# Polls the state; reloads when what the page shows changes; says so when the program is gone. While the program
# is gone the state line's data-shown is a value no style rule names, so the look of what the page showed (the
# linked page's check mark) never sits beside the closed sentence; an answer puts the load value back.
_SCRIPT = ("const s=document.getElementById('state');const w=document.getElementById('watch');"
           "const shown=s.dataset.shown;"
           "setInterval(()=>fetch('/state').then(r=>r.json()).then(j=>{s.textContent=j.text;s.dataset.shown=shown;"
           "if(w&&j.watchText)w.textContent=j.watchText;"
           "if(String(j.showCode)+String(j.relinkOffered)!==shown)location.reload();})"
           ".catch(()=>{s.textContent=s.dataset.closed;s.dataset.shown='closed';}),5000);"
           # The code shows for a minute: 60 s after the details opens it closes again; one timer, cleared on every
           # toggle, so a close by hand leaves none running.
           "const d=document.querySelector('.key-card details');let hide;"
           "if(d)d.addEventListener('toggle',()=>{clearTimeout(hide);"
           "if(d.open)hide=setTimeout(()=>{d.open=false;},60000);});"
           # The theme button (pendiapp.com's assets/theme.js): the choice goes to data-theme and to localStorage.
           "(function(){var btn=document.getElementById('theme-toggle');if(!btn)return;"
           "function current(){var t=document.documentElement.getAttribute('data-theme');"
           "if(t==='light'||t==='dark')return t;return matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light';}"
           "function reflect(){btn.setAttribute('aria-pressed',String(current()==='dark'));}reflect();"
           "btn.addEventListener('click',function(){var next=current()==='dark'?'light':'dark';"
           "document.documentElement.setAttribute('data-theme',next);"
           "try{localStorage.setItem('pendi-theme',next);}catch(e){}reflect();});})();")
# Read in <head> before the first paint, so a stored theme choice never flashes the other theme (pendiapp.com's
# index.html head script).
_THEME_READ = ("(function(){try{var t=localStorage.getItem('pendi-theme');"
               "if(t==='light'||t==='dark')document.documentElement.setAttribute('data-theme',t);}catch(e){}})();")
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


def _figure(rows):
    """One figure as an inline svg of 48 px: a path per colour, each a run of same-colour pixels per row."""
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
    paths = "".join(f'<path class="pl" d="{"".join(d)}"/>' if key == "o" else
                    f'<path fill="{_FIGURE_COLOURS[key]}" d="{"".join(d)}"/>' for key, d in runs.items())
    return (f'<svg class="px" viewBox="0 0 {len(rows[0])} 16" width="48" height="48" shape-rendering="crispEdges">'
            f"{paths}</svg>")


_PARTY = '<div class="party" aria-hidden="true">' + "".join(_figure(rows) for rows in _FIGURES) + "</div>"


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


def _clock_time(at):
    """A wall time as the page says it, hours and minutes in this PC's zone."""
    return time.strftime("%H:%M", time.localtime(at))


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
        """The watcher's line in `lang`: waiting or connected, then the phase while connected once one is read,
        then the last alert and its time, then the last ping's result and its time when a ping was made; None when
        no watcher runs beside the page. It names no game, no maker and no product."""
        if self.watch is None:
            return None
        snapshot, words = self.watch(), WORDS[lang]
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
        # What this is and what to do, with the QR card beside it on a wide window; the two other roads in their
        # own cards under it; quit at the foot. The words and their order are the page's before the design.
        link = [f"<h1>{words['title']}</h1>", f'<p class="intro">{words["intro"]}</p>',
                f'<p id="state" role="status" data-shown="{str(snapshot["showCode"]).lower()}'
                f'{str(snapshot["relinkOffered"]).lower()}" data-closed="{words["state_closed"]}">'
                f'{words["state_" + snapshot["state"]]}</p>']
        if snapshot["configFailed"]:
            link.append(f'<p class="note warn">{words["config_failed"]}</p>')
        watch = self.watch_text(lang)
        if watch is not None:
            link.append(f'<p id="watch" role="status">{html.escape(watch)}</p>')
        if secret is not None:
            modules = qr.encode(qr.pairing_address(secret).encode("ascii")).modules
            drawn = qr.scene_svg(modules, plate_almena.PLATE_DATA_URI, labelledby="scan")
            if drawn is None:  # the scene is drawn for version 3 only
                drawn = qr.svg(modules, labelledby="scan")
            link += [f'<p class="scan" id="scan">{words["scan"]}</p>',
                     # The QR and the key are hidden until their person asks (a page shown on a stream prints
                     # neither): a native details, closed, its summary the one control; _SCRIPT closes it again
                     # 60 s after it opens.
                     f'<figure class="key-card"><details><summary>{words["show_code"]}</summary><div class="shown">'
                     f'{drawn}<p class="key"><span class="key-label">{words["code_label"]}</span> <span class="code">'
                     f'{codes.display(secret)}</span></p>{_PARTY}</div></details></figure>',
                     _form("check", token, f'<button type="submit">{words["check"]}</button>')]
            if snapshot["buttonRefused"]:
                link.append(f'<p class="note">{words["button_wait"]}</p>')
        if snapshot["relinkOffered"]:
            link.append(_form("relink", token, f'<button type="submit">{words["relink"]}</button>'))
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
        foot = "" if self.on_quit is None else f'<footer class="foot">{_form("quit", token, quit_button)}</footer>'
        bar = (f'<header class="bar"><button id="theme-toggle" class="icon-btn" type="button" aria-pressed="false" '
               f'aria-label="{words["theme_toggle"]}">{_THEME_ICONS}</button></header>')
        body = (f'<main class="page">{bar}<section class="link">{"".join(link)}</section><section class="more">'
                f'<div class="panel">{"".join(typed)}</div><div class="panel">{"".join(forget)}</div></section>'
                f"{foot}</main>")
        return (f'<!doctype html><html lang="{lang}"><head><meta charset="utf-8"><meta name="viewport" '
                f'content="width=device-width, initial-scale=1"><title>{words["title"]}</title><style>{_STYLE}'
                f"</style><script>{_THEME_READ}</script></head><body>{body}<script>{_SCRIPT}</script></body></html>")

    def render_stopped(self, lang):
        """The one small page /quit answers: the program stopped, and how to start it again."""
        words = {key: html.escape(value) for key, value in WORDS[lang].items()}
        return (f'<!doctype html><html lang="{lang}"><head><meta charset="utf-8"><meta name="viewport" '
                f'content="width=device-width, initial-scale=1"><title>{words["title"]}</title><style>{_STYLE}'
                f'</style><script>{_THEME_READ}</script></head><body><h1>{words["title"]}</h1><p>{words["stopped"]}</p>'
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
                # Answered before the stop, so the exit that follows never cuts the answer short; stopped even
                # when the answer cannot be written, and that failure goes on to the server's handle_error.
                try:
                    self._send(200, "text/html; charset=utf-8",
                               page.render_stopped(language(self.headers.get("Accept-Language"))))
                finally:
                    page.on_quit()
                return
            page.act(path, form)
            self._send(303, "text/plain; charset=utf-8", "", (("Location", "/"),))

    return Handler
