import hashlib
import re
import struct
import unittest

import support

PICTURE_URL = "https://raw.githubusercontent.com/ElkinDev/PendiFy/main/docs/pendify-page.png"
PICTURE_SHA256 = "94635597a442c5e8d7088722a2a042a65d6bd60d44ef2519c5c43bb8fe8a066f"
PICTURE_LINE = re.compile(r"^!\[([^\]]*)\]\(" + re.escape(PICTURE_URL) + r"\)$")
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
# Text chunks (tEXt, iTXt, zTXt) carry the capture tool and free comments, tIME the moment, eXIf a camera's fields:
# only the chunks a plain picture needs may stay.
ALLOWED_CHUNKS = {b"IHDR", b"pHYs", b"IDAT", b"IEND"}


def readme_sections():
    text = (support.ROOT / "README.md").read_text(encoding="utf-8")
    spanish = text.split("## Español", 1)[1].split("## English", 1)[0]
    english = text.split("## English", 1)[1].split("## Developer commands", 1)[0]
    return text, spanish, english


def png_chunk_types(data):
    types = []
    i = len(PNG_SIGNATURE)
    while i < len(data):
        length = struct.unpack(">I", data[i:i + 4])[0]
        kind = data[i + 4:i + 8]
        types.append(kind)
        i += 12 + length
        if kind == b"IEND":
            break
    return types, i


class ReadmePictureTest(unittest.TestCase):

    def test_each_language_section_holds_the_picture_line_once_with_its_alt_text(self):
        # Mutation: the picture line removed from a section, or a relative path. Red: the count is not one.
        text, spanish, english = readme_sections()
        for name, section in (("Spanish", spanish), ("English", english)):
            lines = [line for line in section.splitlines() if PICTURE_LINE.match(line)]
            self.assertEqual(len(lines), 1, f"{name} section: picture lines {lines}")
            alt = PICTURE_LINE.match(lines[0]).group(1).strip()
            self.assertTrue(alt, f"{name} section: empty alt text")
        self.assertEqual(text.count("!["), 2, "the README holds pictures beyond the two sections")
        self.assertNotIn("docs/demo.jpeg", text)

    def test_the_picture_sits_before_each_install_heading(self):
        # Mutation: the picture moved under the install heading. Red: its index is past the heading.
        _, spanish, english = readme_sections()
        self.assertLess(spanish.index(PICTURE_URL), spanish.index("### Instalar"))
        self.assertLess(english.index(PICTURE_URL), english.index("### Install"))

    def test_the_picture_is_a_png_with_only_its_image_chunks_and_its_sha_is_pinned(self):
        # Mutation: a copy keeping the capture tool's tEXt chunks. Red: a tEXt type is found and the sha differs.
        path = support.ROOT / "docs" / "pendify-page.png"
        self.assertTrue(path.is_file(), "no docs/pendify-page.png")
        data = path.read_bytes()
        self.assertEqual(data[:8], PNG_SIGNATURE)
        types, end = png_chunk_types(data)
        self.assertEqual(types[0], b"IHDR")
        self.assertEqual(types[-1], b"IEND")
        self.assertEqual(end, len(data), "bytes after IEND")
        self.assertEqual(sorted(set(types) - ALLOWED_CHUNKS), [])
        self.assertEqual(hashlib.sha256(data).hexdigest(), PICTURE_SHA256)

    def test_the_first_picture_is_gone(self):
        # Mutation: docs/demo.jpeg kept beside the new picture. Red: the file exists.
        self.assertFalse((support.ROOT / "docs" / "demo.jpeg").exists())

    def test_the_manifest_names_no_docs_and_no_copy_of_the_picture_sits_under_src(self):
        # Mutation: docs/ included in MANIFEST.in or the picture put under src/. Red: the word or the copy is found.
        # This checks the manifest's text and the source tree only, not the members of a built archive.
        manifest = (support.ROOT / "MANIFEST.in").read_text(encoding="utf-8")
        self.assertNotIn("docs", manifest)
        self.assertEqual(list((support.ROOT / "src").rglob("pendify-page.png")), [])
        self.assertEqual(list((support.ROOT / "src").rglob("demo.jpeg")), [])


if __name__ == "__main__":
    unittest.main()
