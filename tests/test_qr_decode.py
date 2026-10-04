"""QrDecodeTest: a real decode of the encoder's matrices by ZXing, with no gradle.

The jar is the one gradle already cached for the app; the JDK is Android Studio's. Java runs the source
file directly (source-file mode), so nothing is compiled to disk. The phone's camera on the bench stays
the proof of record (design section 4); this case proves the matrix is a QR code a real reader accepts.
"""
import base64
import hashlib
import re
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import support
from support import LINK_ID, SECRET

qr = support.module("qr")
plate_almena = support.module("plate_almena")
config = support.module("config")
page = support.module("page")
pairing = support.module("pairing")
worker = support.module("worker")

JAR = Path("C:/Users/nikle/.gradle/caches/modules-2/files-2.1/com.google.zxing/core/3.5.4/"
           "955fcd6bcd0723ddfb8ee6ed502d5fdf0e9676a9/core-3.5.4.jar")
JAVA = Path("C:/Program Files/Android/Android Studio/jbr/bin/java.exe")
SOURCE = Path(__file__).resolve().parent / "QrMatrixDecode.java"
MULTI_BLOCK = [("v4 " * 21)[:62], ("v5 " * 28)[:84], ("v6 " * 36)[:106]]
# The scene's rasteriser: tests/support.svg_samples paints rects and paths but no <image>, so the scene, whose finders,
# timing and quiet zone live in the plate image, is rasterised by headless Edge, as the design lane's r3 probe did.
EDGE = Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe")
# The page's QR box (page.py .qr max-width), the pad of card around it in each crop, the card colours of both themes.
BOX, PAD = 288, 16
CARDS = ("#FFFFFF", "#1E1A31")
# Eight secrets, each a version 3 pairing address; the design lane's sample first.
SCENE_SECRETS = ("K7QM4PXD9HTR", SECRET, LINK_ID, "HJKM2345NPQR", "MNPQ2345RSTV", "Z9Y8X7W6V5T4", "EFGH6789JKMN",
                 "QRST2345VWXY")


# The prefix of the linked page's sponsored picture (lane pfart, OR-113).
PNG_URI = "data:image/png;base64,"


def linked_page():
    """The pairing page of a linked PC, rendered from a real state over a config file in a temporary folder."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
        store = config.ConfigStore(Path(folder))
        state = pairing.PairingState(store, lambda secret: worker.Refused(), clock=support.FakeClock())
        state.typed(LINK_ID, store.read().secret)
        return page.PairingPage(state).render("es")


def matrix_text(modules):
    return "\n".join("".join("1" if module else "0" for module in row) for row in modules)


def zxing_decode(matrices):
    run = subprocess.run([str(JAVA), "-cp", str(JAR), str(SOURCE)], input="\n\n".join(map(matrix_text, matrices)),
                         capture_output=True, text=True, encoding="utf-8", timeout=120)
    return run.returncode, run.stdout.splitlines(), run.stderr


@unittest.skipUnless(JAR.is_file(), f"the ZXing core 3.5.4 jar is absent from the gradle cache: {JAR}")
@unittest.skipUnless(JAVA.is_file(), f"no JDK at Android Studio's jbr: {JAVA}")
class QrDecodeTest(unittest.TestCase):
    def test_zxing_decodes_the_pairing_address_for_three_secrets(self):
        # Mutation: the zigzag of the data starts downward. Red: ZXing reads scrambled codewords and exits 1.
        # (The format bits drawn without the 0x5412 mask are no mutation for ZXing, which also tries the
        # unmasked word; QrEncodeTest's BCH read-back is the pin of that one.)
        addresses = [qr.pairing_address(secret) for secret in (SECRET, LINK_ID, "HJKM2345NPQR")]
        codes = [qr.encode(address.encode("ascii")) for address in addresses]
        self.assertEqual({code.version for code in codes}, {3})
        status, decoded, errors = zxing_decode([code.modules for code in codes])
        self.assertEqual(status, 0, errors[-1500:])
        self.assertEqual(decoded, addresses)

    def test_zxing_decodes_the_multi_block_versions(self):
        # Mutation: the blocks concatenated instead of interleaved. Red: versions 4 to 6 fail to decode.
        texts = MULTI_BLOCK
        codes = [qr.encode(text.encode("ascii")) for text in texts]
        self.assertEqual([code.version for code in codes], [4, 5, 6])
        status, decoded, errors = zxing_decode([code.modules for code in codes])
        self.assertEqual(status, 0, errors[-1500:])
        self.assertEqual(decoded, texts)

    def test_the_drawn_symbol_keeps_every_module_centre_and_zxing_reads_the_drawing(self):
        # Mutation: the finder rings drawn with rx 2.2, as design r1 drew them. Red: the 12 finder corner centres of
        # every symbol read light, (0, 0) (0, 6) (0, 22) and on, 12 of the 1369 of a version 3 symbol.
        texts = ([qr.pairing_address(secret) for secret in (SECRET, LINK_ID, "HJKM2345NPQR")]
                 + [("v1 " * 5)[:14], ("v2 " * 9)[:26]] + MULTI_BLOCK)
        codes = [qr.encode(text.encode("ascii")) for text in texts]
        self.assertEqual([code.version for code in codes], [3, 3, 3, 1, 2, 4, 5, 6])
        drawings = [qr.svg(code.modules) for code in codes]
        differ = {}
        for text, code, drawing in zip(texts, codes, drawings):
            centres = support.svg_samples(drawing, 1)  # one point a module, at its centre, quiet zone included
            wanted = support.quiet_padded(code.modules, qr.QUIET_ZONE)
            differ[text] = [(r - qr.QUIET_ZONE, c - qr.QUIET_ZONE) for r, row in enumerate(wanted)
                            for c, dark in enumerate(row) if centres[r][c] != dark]
        self.assertEqual(differ, {text: [] for text in texts})
        # The drawing itself, four points a module with the page dark where no shape covers it: ZXing's detector
        # has to find the rounded finders before it decodes.
        status, decoded, errors = zxing_decode([[[sample is not False for sample in row]
                                                 for row in support.svg_samples(drawing, 4)] for drawing in drawings])
        self.assertEqual(status, 0, errors[-1500:])
        self.assertEqual(decoded, texts)

    def test_zxing_reads_each_sponsored_picture_at_its_own_size_and_at_the_page_s_four_sides(self):
        # Lane pfart (OR-113, owner 2026-10-03 20:1x): the linked page's sponsored QR is one of five of the app's own
        # styles, a PNG drawn by the app's engine. Each picture, taken from the linked page rendered with the seam set
        # to its style, read by ZXing as it is and scaled by area averaging to every side the page's styles give the
        # code (lane pcnw, OR-99: 198 px under 360 px). Mutation: a style's bytes answering another text's code. Red:
        # ZXing reads that text at all five sides. Mutation: the 359 px rule left out. Red: the sides are three.
        sponsor_art = support.module("sponsor_art")
        requests, where = [], []
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as work:
            for style in sponsor_art.STYLES:
                with mock.patch.object(page, "_pick_sponsor_style", return_value=style):
                    shown = linked_page()
                pictures = re.findall(r'<img class="qr2" src="data:image/png;base64,([A-Za-z0-9+/]+=*)"', shown)
                self.assertEqual(len(pictures), 1, style)
                picture = base64.b64decode(pictures[0], validate=True)
                self.assertEqual(picture, base64.b64decode(sponsor_art.data_uri(style)[len(PNG_URI):]), style)
                path = Path(work) / f"{style}.png"
                path.write_bytes(picture)
                sides = sorted((int(side) for side in
                                re.findall(r"\.qr2\{(?:display:block;)?width:(?:min\()?(\d+)px", shown)), reverse=True)
                self.assertEqual(sides, [330, 264, 231, 198])
                for side in [png_size(path)[0]] + sides:
                    requests.append(f"{path} {side}")
                    where.append(f"{style} at {side} px")
            status, decoded, errors = zxing_read_scaled(requests)
        self.assertEqual(status, 0, errors[-1500:])
        self.assertEqual(len(requests), 25)
        self.assertEqual(len(decoded), len(requests), errors[-1500:])
        self.assertEqual(dict(zip(where, decoded)),
                         {spot: f"{spot.split()[-2]} {page.SPONSOR_ADDRESS}" for spot in where})


def png_size(path):
    """A PNG's width and height from its IHDR chunk."""
    head = Path(path).read_bytes()[:24]
    if head[:8] != b"\x89PNG\r\n\x1a\n" or head[12:16] != b"IHDR":
        raise AssertionError(f"{path} is not a PNG")
    return struct.unpack(">II", head[16:24])


def edge_screenshot(work, page, width, height, scale):
    """The page rasterised by headless Edge at a device scale; the PNG's path."""
    png = Path(work) / f"scene-{scale}x.png"
    run = subprocess.run([str(EDGE), "--headless=new", "--disable-gpu", "--hide-scrollbars",
                          f"--user-data-dir={Path(work) / 'profile'}", "--virtual-time-budget=3000",
                          f"--screenshot={png}", f"--window-size={width},{height}", f"--force-device-scale-factor={scale}",
                          page.as_uri()], capture_output=True, text=True, timeout=180)
    if not png.is_file() or png.stat().st_size == 0:
        raise AssertionError(f"Edge wrote no screenshot (exit {run.returncode}): {run.stderr[-1500:]}")
    return png


def zxing_read_scaled(requests):
    """Each request "path side" read by ZXing from that PNG at that side, scaled by area averaging when the side is
    not its own; one line each, the side read and the text."""
    run = subprocess.run([str(JAVA), "-cp", str(JAR), str(SOURCE), "--scaled"], input="\n".join(requests),
                         capture_output=True, text=True, encoding="utf-8", timeout=180)
    return run.returncode, run.stdout.splitlines(), run.stderr


def zxing_read_crops(requests):
    """Each request (png, x, y, width, height, half) read by ZXing from that crop of the PNG; one line each."""
    lines = "\n".join(f"{png} {x} {y} {w} {h} {int(half)}" for png, x, y, w, h, half in requests)
    run = subprocess.run([str(JAVA), "-cp", str(JAR), str(SOURCE), "--png"], input=lines, capture_output=True,
                         text=True, encoding="utf-8", timeout=180)
    return run.returncode, run.stdout.splitlines(), run.stderr


@unittest.skipUnless(JAR.is_file(), f"the ZXing core 3.5.4 jar is absent from the gradle cache: {JAR}")
@unittest.skipUnless(JAVA.is_file(), f"no JDK at Android Studio's jbr: {JAVA}")
@unittest.skipUnless(EDGE.is_file(), f"no Edge to rasterise the scene's plate image: {EDGE}")
class SceneDecodeTest(unittest.TestCase):
    def test_zxing_reads_the_scene_of_eight_symbols_at_the_page_box_and_at_half_on_a_double_density_screen(self):
        # Mutation: the centre dots' colours swapped, GROUND on the dark modules and INK on the light ones. Red:
        # ZXing reads none of the 48 crops.
        # Each symbol on the light and the dark card, 288 px in a 320 px crop: at device scale 1 read at that size
        # (the page's box on a normal screen); at device scale 2 read at 640 px and at half of it, 320 px (the
        # page's box on a double-density screen, and half of it).
        addresses = [qr.pairing_address(secret) for secret in SCENE_SECRETS]
        codes = [qr.encode(address.encode("ascii")) for address in addresses]
        self.assertEqual({code.version for code in codes}, {3})
        cell = BOX + 2 * PAD
        cells = [(i, bg, (i % 4) * cell, (2 * (i // 4) + k) * cell) for i in range(len(codes))
                 for k, bg in enumerate(CARDS)]
        drawings = [qr.scene_svg(code.modules, plate_almena.PLATE_DATA_URI, uid=f"s{i}")
                    for i, code in enumerate(codes)]
        body = "".join(f'<div style="position:absolute;left:{x}px;top:{y}px;padding:{PAD}px;background:{bg}">'
                       f'<div style="width:{BOX}px;height:{BOX}px">'
                       f'{drawings[i].replace("<svg ", f"<svg width={BOX} height={BOX} ", 1)}</div></div>'
                       for i, bg, x, y in cells)
        width, height = 4 * cell, 4 * cell
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as work:
            page = Path(work) / "scene.html"
            page.write_text('<!doctype html><html><head><meta charset="utf-8"><style>html,body{margin:0;'
                            'background:#888}svg{display:block}</style></head><body>' + body + "</body></html>",
                            encoding="utf-8")
            requests, wanted, where = [], [], []
            for scale in (1, 2):
                png = edge_screenshot(work, page, width, height, scale)
                self.assertEqual(png_size(png), (width * scale, height * scale))
                for i, bg, x, y in cells:
                    for half in ((False,) if scale == 1 else (False, True)):
                        requests.append((png, x * scale, y * scale, cell * scale, cell * scale, half))
                        wanted.append(addresses[i])
                        where.append(f"symbol {i} card {bg} scale {scale} crop {cell * scale} px half {half}")
            status, decoded, errors = zxing_read_crops(requests)
        self.assertEqual(status, 0, errors[-1500:])
        self.assertEqual(len(requests), 48)
        self.assertEqual(dict(zip(where, decoded)), dict(zip(where, wanted)))

    def test_both_masks_decode_to_nothing_and_the_revealed_qr_decodes(self):
        # Lane pfmaskimpl (OR-104): the owner tried to scan the 0.1.5 mask. Both masks, the bust and the board, over
        # real payloads, in the scene and in the flat drawing, rasterised by Edge under the page's own style (the
        # outline and the board's tokens are its rules), the modules group cut out as the hidden state hides it: ZXing
        # reads nothing. The same drawings with the mask cut out, as the shown state hides it, read the address.
        # Mutation: the mask drawn from the symbol's modules. Red: a hidden crop reads the address. Mutation: the mask
        # left over the shown state. Red: a shown crop reads nothing.
        words = "Press Show the code"
        crops = []
        for secret in (SECRET, LINK_ID):
            address = qr.pairing_address(secret)
            modules = qr.encode(address.encode("ascii")).modules
            for mask in qr.MASKS:
                for kind in ("scene", "svg"):
                    for state in ("hidden", "shown"):
                        uid = f"m{len(crops)}"
                        drawing = (qr.scene_svg(modules, plate_almena.PLATE_DATA_URI, uid=uid, mask=mask,
                                                mask_words=words) if kind == "scene" else
                                   qr.svg(modules, mask=mask, mask_words=words))
                        if state == "hidden":
                            head, rest = drawing.split('<g class="modules"', 1)
                            drawing = head + rest[rest.index('<g class="qr-mask"'):]
                            self.assertNotIn('class="modules"', drawing)
                        else:
                            drawing = drawing[:drawing.index('<g class="qr-mask"')] + "</svg>"
                            self.assertNotIn("qr-mask", drawing)
                        crops.append((drawing, address if state == "shown" else "NO READ",
                                      f"{secret} {mask} {kind} {state}"))
        cell = BOX + 2 * PAD
        body = "".join(f'<div style="position:absolute;left:{(i % 4) * cell}px;top:{(i // 4) * cell}px;'
                       f'padding:{PAD}px;background:{CARDS[0]}"><div style="width:{BOX}px;height:{BOX}px">'
                       f'{drawing.replace("<svg ", f"<svg width={BOX} height={BOX} ", 1)}</div></div>'
                       for i, (drawing, _, _) in enumerate(crops))
        width, height = 4 * cell, (len(crops) + 3) // 4 * cell
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as work:
            sheet = Path(work) / "masks.html"
            sheet.write_text('<!doctype html><html><head><meta charset="utf-8"><style>' + page._STYLE
                             + "html,body{margin:0;padding:0;background:#888}svg{display:block}</style></head><body>"
                             + body + "</body></html>", encoding="utf-8")
            png = edge_screenshot(work, sheet, width, height, 1)
            self.assertEqual(png_size(png), (width, height))
            status, decoded, errors = zxing_read_crops([(png, (i % 4) * cell, (i // 4) * cell, cell, cell, False)
                                                        for i in range(len(crops))])
        self.assertEqual(len(crops), 16)
        self.assertEqual(len(decoded), len(crops), errors[-1500:])
        self.assertEqual({where: read if wanted != "NO READ" else read[:7] for (_, wanted, where), read
                          in zip(crops, decoded)}, {where: wanted for _, wanted, where in crops})

    def test_the_plate_is_the_design_lane_webp_and_every_other_version_falls_back(self):
        # Mutation: scene_svg drawn for any size. Red: a version 4 symbol gets the version 3 plate.
        # The plate is pcpg-r4-plate-75-576.webp of the design lane pcpg round 4, redrawn for the 75 floor.
        prefix = "data:image/webp;base64,"
        self.assertTrue(plate_almena.PLATE_DATA_URI.startswith(prefix))
        plate = base64.b64decode(plate_almena.PLATE_DATA_URI[len(prefix):], validate=True)
        self.assertEqual((len(plate), plate[:4], plate[8:12]), (28132, b"RIFF", b"WEBP"))
        self.assertEqual(hashlib.sha256(plate).hexdigest(),
                         "0124c2124d66e79e35e8f4fc03ffed082263a22929397aef6c2957babf702329")
        texts = [("v1 " * 5)[:14], ("v2 " * 9)[:26]] + MULTI_BLOCK
        for text in texts:
            self.assertIsNone(qr.scene_svg(qr.encode(text.encode("ascii")).modules, plate_almena.PLATE_DATA_URI))


if __name__ == "__main__":
    unittest.main()
