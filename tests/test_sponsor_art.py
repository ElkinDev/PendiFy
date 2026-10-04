"""SponsorArtTest: the five pictures of the linked page's sponsored QR, carried as base64 text (lane pfart, OR-113).

Pure Python, so it runs on every machine; QrDecodeTest reads the same pictures back with ZXing where the jar is.
"""
import base64
import hashlib
import re
import struct
import unittest

import support

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
PREFIX = "data:image/png;base64,"
# One manifest line of the module's docstring: style, delivered side, bytes, sha256.
MANIFEST_LINE = re.compile(r"^ *(\w+): (\d+) px a side, (\d+) bytes, sha256 ([0-9a-f]{64})\.?$", re.M)


class SponsorArtTest(unittest.TestCase):
    def setUp(self):
        self.art = support.module("sponsor_art")

    def manifest(self):
        """{style: (side, bytes, sha256)} as the module's docstring writes MANIFEST.txt of the design lane."""
        return {style: (int(side), int(size), digest)
                for style, side, size, digest in MANIFEST_LINE.findall(self.art.__doc__ or "")}

    def test_the_styles_are_the_five_the_owner_named_in_order(self):
        # Mutation: a style dropped or the order changed. Red: the tuple differs.
        self.assertEqual(self.art.STYLES, ("amor", "calma", "otono", "jardin", "bosque"))
        self.assertEqual(set(self.art.SHA256), set(self.art.STYLES))
        self.assertEqual(list(self.manifest()), list(self.art.STYLES))

    def test_each_picture_is_the_manifest_png_byte_for_byte(self):
        # Mutation: one picture re-encoded or cut. Red: its sha256, its size or its signature differs from the
        # manifest's. Mutation: two styles answering the same picture. Red: fewer than five digests.
        manifest = self.manifest()
        seen = set()
        for style in self.art.STYLES:
            with self.subTest(style=style):
                uri = self.art.data_uri(style)
                self.assertTrue(uri.startswith(PREFIX), uri[:40])
                picture = base64.b64decode(uri[len(PREFIX):], validate=True)
                self.assertEqual(picture[:8], PNG_SIGNATURE)
                self.assertEqual(picture[12:16], b"IHDR")
                width, height = struct.unpack(">II", picture[16:24])
                side, size, digest = manifest[style]
                self.assertEqual((width, height, len(picture)), (side, side, size))
                self.assertEqual(hashlib.sha256(picture).hexdigest(), digest)
                self.assertEqual(self.art.SHA256[style], digest)
                seen.add(digest)
        self.assertEqual(len(seen), 5)

    def test_data_uri_refuses_an_unknown_style(self):
        # Mutation: an unknown style answered with the first picture. Red: no ValueError.
        for unknown in ("otoño", "jardín", "AMOR", "", "plain"):
            with self.subTest(style=unknown), self.assertRaises(ValueError):
                self.art.data_uri(unknown)


if __name__ == "__main__":
    unittest.main()
