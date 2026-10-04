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
JPEG_SIGNATURE = b"\xff\xd8\xff"
# Each style's media type: the three flat codes PNG, the two 3D stills JPEG (lane pfart, amendment 1).
MEDIA = {"amor": "image/png", "calma": "image/png", "otono": "image/png", "jardin": "image/jpeg",
         "bosque": "image/jpeg"}
# One manifest row of the module's docstring: style, media type, side, bytes, sha256.
MANIFEST_LINE = re.compile(r"^ *(\w+): (image/png|image/jpeg), (\d+) px, (\d+) bytes, sha256 ([0-9a-f]{64})\.$",
                           re.M)


class SponsorArtTest(unittest.TestCase):
    def setUp(self):
        self.art = support.module("sponsor_art")

    def manifest(self):
        """{style: (media type, side, bytes, sha256)} as the module's docstring writes the rows of MANIFEST.txt and
        MANIFEST-3d.txt of the design lane."""
        return {style: (media, int(side), int(size), digest)
                for style, media, side, size, digest in MANIFEST_LINE.findall(self.art.__doc__ or "")}

    def test_the_styles_are_the_five_the_owner_named_in_order(self):
        # Mutation: a style dropped or the order changed. Red: the tuple differs.
        self.assertEqual(self.art.STYLES, ("amor", "calma", "otono", "jardin", "bosque"))
        self.assertEqual(set(self.art.SHA256), set(self.art.STYLES))
        self.assertEqual(list(self.manifest()), list(self.art.STYLES))

    def test_each_picture_is_its_manifest_row_byte_for_byte(self):
        # Mutation: one picture re-encoded or cut. Red: its sha256, its size or its signature differs from the
        # manifest's. Mutation: two styles answering the same picture. Red: fewer than five digests. Amendment 1
        # (owner 2026-10-03 20:3x): jardin and bosque are the app's 3D stills, JPEG. Mutation: the flat jardin kept.
        # Red: its media type is image/png and its bytes are not the still's row.
        manifest = self.manifest()
        self.assertEqual({style: row[0] for style, row in manifest.items()}, MEDIA)
        self.assertEqual(self.art.MEDIA, MEDIA)
        seen = set()
        for style in self.art.STYLES:
            with self.subTest(style=style):
                media, side, size, digest = manifest[style]
                prefix = f"data:{media};base64,"
                uri = self.art.data_uri(style)
                self.assertTrue(uri.startswith(prefix), uri[:40])
                picture = base64.b64decode(uri[len(prefix):], validate=True)
                if media == "image/png":
                    self.assertEqual(picture[:8], PNG_SIGNATURE)
                    self.assertEqual(picture[12:16], b"IHDR")
                    width, height = struct.unpack(">II", picture[16:24])
                    self.assertEqual((width, height), (side, side))
                else:
                    self.assertEqual(picture[:3], JPEG_SIGNATURE)
                self.assertEqual(len(picture), size)
                self.assertEqual(hashlib.sha256(picture).hexdigest(), digest)
                self.assertEqual(self.art.SHA256[style], digest)
                seen.add(digest)
        self.assertEqual(len(seen), 5)

    def test_data_uri_refuses_an_unknown_style(self):
        # Mutation: an unknown style answered with the first picture. Red: no ValueError.
        for unknown in ("otoño", "jardín", "AMOR", "", "plain"):
            with self.subTest(style=unknown), self.assertRaises(ValueError):
                self.art.data_uri(unknown)

    def test_each_picture_is_held_once_after_import(self):
        # Mutation: the base64 table kept beside the data URIs, every picture held twice. Red: the module still
        # has an attribute named _BASE64.
        self.assertFalse(hasattr(self.art, "_BASE64"))
        for style in self.art.STYLES:
            with self.subTest(style=style):
                uri = self.art.data_uri(style)
                picture = base64.b64decode(uri.split(",", 1)[1], validate=True)
                self.assertEqual(hashlib.sha256(picture).hexdigest(), self.art.SHA256[style])


if __name__ == "__main__":
    unittest.main()
