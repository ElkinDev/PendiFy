"""The README's alert series: three stills of the program's own page and the looping animation made of them.

Run from anywhere: python tools/render_alert_stills.py (Pillow and Microsoft Edge needed, neither by the package).

Each still is the linked page as src/pendify/page.py draws it (PairingPage.render), in English and in the dark theme,
fed demonstration stand-ins for the state, the watcher and its log, written to a temporary folder and screenshotted
by Edge headless at 1920 x 945, the size of docs/pendify-page.png. Nothing is drawn by hand. The script never starts
the program, its page server or its tray, calls no port, and reads no configuration: the page's assets are data URIs
already, so the HTML file references nothing else. The page polls /state every 5 s and writes its closed sentence
when the poll fails, so the screenshot is taken at load, long before the first poll.

The run is reproducible: fixed wall times built from local clock values (every zone draws the same clock), the
sponsor style of the owner's picture, a fixed size and scale, and the page's reduced-motion drawing, so the party
figures and the breathing light stand still and a second run gives the same pixels.
"""
import hashlib
import re
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from pendify import page, qr  # noqa: E402  (the checkout's own package, ahead of any installed copy)

DOCS = ROOT / "docs"
EDGE = Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe")
WIDTH, HEIGHT = 1920, 945
GIF_SIZE = (960, 472)
FRAME_MS = 2000
COLOURS = 128
EDGE_SECONDS = 60
# The style of the sponsored QR in the owner's picture (sponsor_art.STYLES).
SPONSOR_STYLE = "amor"
# A visibly fake value for the page's form token: the forms are never posted, the page is never served.
DEMO_TOKEN = "demonstration-token-not-real"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
KEPT_CHUNKS = {b"IHDR", b"pHYs", b"sRGB", b"IDAT", b"IEND"}

# The demonstration log, in the watcher's own order (watcher.py: started, waiting, connected, each phase that
# differs, the accept and then the alert of the found match, the loading screen, the start and its alert); each
# row is (local clock, kind, detail) and reads in the page's own words (page.LOG_WORDS, page.PHASE_WORDS).
ROWS = (
    ("20:14:05", "started", None),
    ("20:14:06", "waiting", None),
    ("20:15:12", "connected", None),
    ("20:15:20", "phase", "Matchmaking"),
    ("20:17:03", "phase", "ReadyCheck"),
    ("20:17:05", "accepted", None),
    ("20:17:06", "ping", "sent"),
    ("20:17:31", "phase", "ChampSelect"),
    ("20:19:48", "phase", "InProgress"),
    ("20:19:48", "loading", None),
    ("20:21:57", "match_started", None),
    ("20:21:58", "ping", "sent"),
)


def wall(clock):
    """A demonstration wall time: 2026-10-06 at `clock` (HH:MM:SS) in this PC's zone, as the page reads it back."""
    hours, minutes, seconds = (int(part) for part in clock.split(":"))
    return time.mktime((2026, 10, 6, hours, minutes, seconds, 0, 0, -1))


# The three states in arrival order: the still's name, how many log rows it shows, and the watcher's snapshot.
STATES = (
    ("alert-1-waiting.png", 2,
     {"client": "waiting", "paused": False, "phase": None, "alert": None, "at": None, "pingResult": None,
      "pingAt": None}),
    ("alert-2-match-found.png", 7,
     {"client": "connected", "paused": False, "phase": "ReadyCheck", "alert": "queue", "at": wall("20:17:05"),
      "pingResult": "sent", "pingAt": wall("20:17:06")}),
    ("alert-3-match-started.png", 12,
     {"client": "connected", "paused": False, "phase": "InProgress", "alert": "started", "at": wall("20:21:57"),
      "pingResult": "sent", "pingAt": wall("20:21:58")}),
)


class DemoState:
    """The pairing state as the page reads it: linked, no code shown, nothing refused, dark, English."""

    @staticmethod
    def snapshot():
        return {"state": "linked", "linked": True, "showCode": False, "relinkOffered": False, "configFailed": False,
                "buttonRefused": False, "typedRefused": False}

    @staticmethod
    def theme():
        return "dark"

    @staticmethod
    def lang():
        return "en"


class DemoAutostart:
    """The start with Windows as in the owner's picture: available, off."""
    available = True

    @staticmethod
    def enabled():
        return False


def tree_version():
    """The version this tree builds (pyproject.toml), for the top bar's credit."""
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    return re.search(r'^version = "([^"]+)"$', text, re.MULTILINE).group(1)


def render(rows, seen):
    """The linked page in English with `rows` in its log and `seen` as the watcher's snapshot."""
    events = [(seq, wall(clock), kind, detail) for seq, (clock, kind, detail) in enumerate(rows, 1)]
    demo = page.PairingPage(DemoState(), watch=lambda: seen, on_quit=lambda: None, autostart=DemoAutostart(),
                            events=lambda: events, mask=qr.MASKS[0])
    demo.token = DEMO_TOKEN
    return demo.render("en")


def screenshot(html_path, png_path, profile):
    """Edge headless draws the file at 1920 x 945, scale 1, reduced motion, in a profile of its own."""
    command = [str(EDGE), "--headless", "--disable-gpu", "--hide-scrollbars", f"--window-size={WIDTH},{HEIGHT}",
               f"--user-data-dir={profile}", "--no-first-run", "--force-device-scale-factor=1",
               "--force-prefers-reduced-motion", f"--screenshot={png_path}", html_path.as_uri()]
    began = time.monotonic()
    subprocess.run(command, check=True, timeout=EDGE_SECONDS, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return time.monotonic() - began


def strip_png(data):
    """The PNG with only the chunks a plain picture needs, by a walk over its chunks, and the types it dropped; the
    pixels are untouched."""
    if data[:8] != PNG_SIGNATURE:
        raise SystemExit("Edge wrote no PNG")
    kept, dropped, i = [PNG_SIGNATURE], [], 8
    while i + 8 <= len(data):
        length = struct.unpack(">I", data[i:i + 4])[0]
        chunk = data[i:i + 12 + length]
        if chunk[4:8] in KEPT_CHUNKS:
            kept.append(chunk)
        else:
            dropped.append(chunk[4:8].decode("latin-1"))
        i += 12 + length
        if chunk[4:8] == b"IEND":
            break
    out = b"".join(kept)
    size = struct.unpack(">II", out[16:24])
    if size != (WIDTH, HEIGHT):
        raise SystemExit(f"Edge drew {size}, not {(WIDTH, HEIGHT)}")
    return out, dropped


def make_gif(stills, gif_path):
    """The stills in arrival order, halved, palette reduced, 2 s each, looping forever, with no comment."""
    frames = []
    for path in stills:
        with Image.open(path) as still:
            half = still.convert("RGB").resize(GIF_SIZE, Image.Resampling.LANCZOS)
        frame = half.quantize(colors=COLOURS, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
        frame.info.clear()
        frames.append(frame)
    frames[0].save(gif_path, save_all=True, append_images=frames[1:], duration=FRAME_MS, loop=0, optimize=True)


def main():
    if not EDGE.is_file():
        raise SystemExit(f"no Microsoft Edge at {EDGE}")
    page._pick_sponsor_style = lambda: SPONSOR_STYLE
    version = tree_version()
    page._running_version = lambda: version
    DOCS.mkdir(exist_ok=True)
    stills = []
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
        work = Path(folder)
        for name, count, seen in STATES:
            html_path = work / (Path(name).stem + ".html")
            html_path.write_text(render(ROWS[:count], seen), encoding="utf-8")
            raw = work / name
            seconds = screenshot(html_path, raw, work / "profile")
            still = DOCS / name
            stripped, dropped = strip_png(raw.read_bytes())
            still.write_bytes(stripped)
            stills.append(still)
            print(f"{name}: Edge {seconds:.1f} s, dropped {dropped or 'nothing'}, "
                  f"sha256 {hashlib.sha256(stripped).hexdigest()}")
    gif = DOCS / "alert-flow.gif"
    make_gif(stills, gif)
    data = gif.read_bytes()
    print(f"{gif.name}: {len(data)} bytes, sha256 {hashlib.sha256(data).hexdigest()}")


if __name__ == "__main__":
    main()
