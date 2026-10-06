import hashlib
import re
import unittest

import support

PICTURE_URL = "https://raw.githubusercontent.com/ElkinDev/PendiFy/main/docs/demo.jpeg"
PICTURE_SHA256 = "57df643cbaf201406f119affc2a51f9b84c7463f128d61acf2f6f7581a90eb15"
PICTURE_LINE = re.compile(r"^!\[([^\]]*)\]\(" + re.escape(PICTURE_URL) + r"\)$")
# APP1 carries Exif and XMP (location, device), APP13 carries Photoshop IPTC, COM is free text.
METADATA_MARKERS = {0xE1: "APP1", 0xED: "APP13", 0xFE: "COM"}


def readme_sections():
    text = (support.ROOT / "README.md").read_text(encoding="utf-8")
    spanish = text.split("## Español", 1)[1].split("## English", 1)[0]
    english = text.split("## English", 1)[1].split("## Developer commands", 1)[0]
    return text, spanish, english


def jpeg_markers(data):
    markers = []
    i = 2
    while i < len(data):
        if data[i] != 0xFF:
            raise AssertionError(f"no marker at byte {i}")
        marker = data[i + 1]
        if marker == 0xD9 or 0xD0 <= marker <= 0xD7 or marker == 0x01:
            markers.append(marker)
            i += 2
            if marker == 0xD9:
                break
            continue
        length = int.from_bytes(data[i + 2:i + 4], "big")
        markers.append(marker)
        if marker == 0xDA:
            break
        i += 2 + length
    return markers


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

    def test_the_picture_sits_before_each_install_heading(self):
        # Mutation: the picture moved under the install heading. Red: its index is past the heading.
        _, spanish, english = readme_sections()
        self.assertLess(spanish.index(PICTURE_URL), spanish.index("### Instalar"))
        self.assertLess(english.index(PICTURE_URL), english.index("### Install"))

    def test_the_picture_is_a_jpeg_with_no_metadata_segment_and_its_sha_is_pinned(self):
        # Mutation: a copy carrying Exif. Red: an APP1 marker is found and the sha differs.
        path = support.ROOT / "docs" / "demo.jpeg"
        self.assertTrue(path.is_file(), "no docs/demo.jpeg")
        data = path.read_bytes()
        self.assertEqual(data[:3], b"\xff\xd8\xff")
        found = [METADATA_MARKERS[m] for m in jpeg_markers(data) if m in METADATA_MARKERS]
        self.assertEqual(found, [])
        self.assertEqual(hashlib.sha256(data).hexdigest(), PICTURE_SHA256)

    def test_the_picture_stays_out_of_the_package_tree_and_the_manifest(self):
        # Mutation: docs/ included in MANIFEST.in or the picture put under src/. Red: it would ship.
        manifest = (support.ROOT / "MANIFEST.in").read_text(encoding="utf-8")
        self.assertNotIn("docs", manifest)
        self.assertEqual(list((support.ROOT / "src").rglob("demo.jpeg")), [])


if __name__ == "__main__":
    unittest.main()
