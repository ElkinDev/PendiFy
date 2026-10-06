import re
import struct
import unittest

import support

GIF_URL = "https://raw.githubusercontent.com/ElkinDev/PendiFy/main/docs/alert-flow.gif"
PICTURE_URL = "https://raw.githubusercontent.com/ElkinDev/PendiFy/main/docs/pendify-page.png"
GIF_LINE = re.compile(r"^!\[([^\]]*)\]\(" + re.escape(GIF_URL) + r"\)$")
SENTENCES = (
    ("Spanish", "Así llega un aviso: el programa espera al cliente del juego, detecta la partida y envía el aviso a tu "
                "cuenta."),
    ("English", "How an alert arrives: the program waits for the game client, detects the match and sends the alert to "
                "your account."),
)
# The arrival order: the frames of the animation follow it.
STILLS = ("alert-1-waiting.png", "alert-2-match-found.png", "alert-3-match-started.png")
STILL_SIZE = (1920, 945)
GIF_WIDTH = 960
GIF_LIMIT = 2 * 1024 * 1024
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
# Text chunks carry the capture tool and free comments, tIME the moment, iCCP the screen's profile: only the chunks a
# plain picture needs may stay.
ALLOWED_CHUNKS = {b"IHDR", b"pHYs", b"sRGB", b"IDAT", b"IEND"}
SCRIPT = support.ROOT / "tools" / "render_alert_stills.py"
# What a real value looks like: a pairing code (twelve of codes.ALPHABET, plain or in its 4-4-4 display), a hex run,
# a UUID, and a base64 or URL-safe run that mixes digits and both cases (a token_urlsafe value).
CODE_RUN = re.compile(r"[A-HJ-NP-Z2-9]{12,}|[A-HJ-NP-Z2-9]{4}-[A-HJ-NP-Z2-9]{4}-[A-HJ-NP-Z2-9]{4}")
HEX_RUN = re.compile(r"[0-9a-fA-F]{16,}|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
BASE64_RUN = re.compile(r"[A-Za-z0-9_\-+/]{20,}")


def readme_sections():
    text = (support.ROOT / "README.md").read_text(encoding="utf-8")
    spanish = text.split("## Español", 1)[1].split("## English", 1)[0]
    english = text.split("## English", 1)[1].split("## Developer commands", 1)[0]
    return text, (("Spanish", spanish), ("English", english))


def png_chunks(data):
    """The chunk types of a PNG in order, and where the walk stopped."""
    types, i = [], len(PNG_SIGNATURE)
    while i + 8 <= len(data):
        length = struct.unpack(">I", data[i:i + 4])[0]
        kind = data[i + 4:i + 8]
        types.append(kind)
        i += 12 + length
        if kind == b"IEND":
            break
    return types, i


def _skip_sub_blocks(data, i):
    while data[i]:
        i += data[i] + 1
    return i + 1


def gif_walk(data):
    """The logical width, the frame count, the extension labels and the application ids of a GIF, by its blocks."""
    width = struct.unpack("<H", data[6:8])[0]
    flags = data[10]
    i = 13 + (3 * (2 << (flags & 7)) if flags & 0x80 else 0)
    frames, labels, apps = 0, [], []
    while data[i] != 0x3B:
        if data[i] == 0x21:
            labels.append(data[i + 1])
            if data[i + 1] == 0xFF:
                apps.append(data[i + 3:i + 3 + data[i + 2]])
            i = _skip_sub_blocks(data, i + 2)
        elif data[i] == 0x2C:
            frames += 1
            local = data[i + 9]
            i += 10 + (3 * (2 << (local & 7)) if local & 0x80 else 0) + 1
            i = _skip_sub_blocks(data, i)
        else:
            raise AssertionError(f"unknown GIF block {data[i]:#x} at {i}")
    return width, frames, labels, apps, i + 1


class ReadmeAlertSeriesTest(unittest.TestCase):

    def test_each_section_holds_its_sentence_then_the_gif_line_once_after_its_picture(self):
        # Mutation: the GIF line dropped from a section, or a relative path. Red: the count is not one.
        # Mutation: the sentence line moved under the GIF. Red: the line after the picture's blank line differs.
        _, sections = readme_sections()
        for (name, section), (_, sentence) in zip(sections, SENTENCES):
            lines = section.splitlines()
            found = [i for i, line in enumerate(lines) if GIF_LINE.match(line)]
            self.assertEqual(len(found), 1, f"{name} section: GIF lines {found}")
            self.assertTrue(GIF_LINE.match(lines[found[0]]).group(1).strip(), f"{name} section: empty alt text")
            picture = [i for i, line in enumerate(lines) if PICTURE_URL in line]
            self.assertEqual(len(picture), 1, f"{name} section: picture lines {picture}")
            p = picture[0]
            self.assertEqual(lines[p + 1:p + 5], ["", sentence, "", lines[found[0]]],
                             f"{name} section: the picture, its blank line, the sentence, a blank line, the GIF")

    def test_the_readme_holds_four_pictures_two_pages_and_two_animations(self):
        # Mutation: a third copy of the GIF line, or any other picture. Red: the count is not four.
        text, _ = readme_sections()
        self.assertEqual(text.count("!["), 4, "the README holds pictures beyond the two pages and two animations")
        self.assertEqual(text.count(GIF_URL), 2)

    def test_each_still_is_a_1920_by_945_png_with_only_its_image_chunks(self):
        # Mutation: Edge's own file committed unstripped, or a still at another size. Red: a tEXt type or the size.
        for name in STILLS:
            path = support.ROOT / "docs" / name
            self.assertTrue(path.is_file(), f"no docs/{name}")
            data = path.read_bytes()
            self.assertEqual(data[:8], PNG_SIGNATURE, f"{name}: not a PNG")
            self.assertEqual(struct.unpack(">II", data[16:24]), STILL_SIZE, f"{name}: size")
            types, end = png_chunks(data)
            self.assertEqual(types[0], b"IHDR", f"{name}: first chunk")
            self.assertEqual(types[-1], b"IEND", f"{name}: last chunk")
            self.assertEqual(end, len(data), f"{name}: bytes after IEND")
            self.assertEqual(sorted(set(types) - ALLOWED_CHUNKS), [], f"{name}: chunks beyond the image's")

    def test_the_gif_is_under_2_mb_with_three_looping_frames_and_no_comment(self):
        # Mutation: a comment extension written, a fourth frame, or no loop. Red: label 0xFE, the count, no NETSCAPE.
        path = support.ROOT / "docs" / "alert-flow.gif"
        self.assertTrue(path.is_file(), "no docs/alert-flow.gif")
        data = path.read_bytes()
        self.assertIn(data[:6], (b"GIF87a", b"GIF89a"))
        self.assertLess(len(data), GIF_LIMIT)
        width, frames, labels, apps, end = gif_walk(data)
        self.assertEqual(width, GIF_WIDTH)
        self.assertEqual(frames, len(STILLS))
        self.assertNotIn(0xFE, labels, "a comment extension")
        self.assertIn(b"NETSCAPE2.0", apps, "no loop")
        self.assertEqual(end, len(data), "bytes after the trailer")

    def test_the_render_script_exists_and_holds_no_code_link_id_or_token(self):
        # Mutation: a real pairing code, a hex link id or a token_urlsafe value in the demonstration. Red: a match.
        self.assertTrue(SCRIPT.is_file(), "no tools/render_alert_stills.py")
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertEqual(CODE_RUN.findall(text), [])
        self.assertEqual(HEX_RUN.findall(text), [])
        mixed = [run for run in BASE64_RUN.findall(text)
                 if re.search(r"[0-9]", run) and re.search(r"[A-Z]", run) and re.search(r"[a-z]", run)]
        self.assertEqual(mixed, [])


if __name__ == "__main__":
    unittest.main()
