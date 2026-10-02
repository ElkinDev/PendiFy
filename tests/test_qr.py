"""QrMaskTest: both drawings of the pairing QR hold the modules in their own group and, when the page names it, a
constant pixelated mask over them that no key changes (owner 2026-10-02 10:3x)."""
import re
import unittest

import support
from support import SECRET

qr = support.module("qr")
plate_almena = support.module("plate_almena")

OTHER = SECRET[::-1]
NAME = "Hidden code"
MASK = re.compile(r'<g class="qr-mask"[^>]*>.*?</g>', re.S)


def drawings(secret, mask=NAME):
    modules = qr.encode(qr.pairing_address(secret).encode("ascii")).modules
    return modules, {"svg": qr.svg(modules, mask=mask),
                     "scene": qr.scene_svg(modules, plate_almena.PLATE_DATA_URI, mask=mask)}


class QrMaskTest(unittest.TestCase):
    def test_both_drawings_hold_the_modules_group_and_the_mask_group_after_it(self):
        # Mutation: the mask drawn inside the modules group. Red: the modules group closes after the mask opens.
        # Mutation: the plate drawn inside the modules group. Red: the plate comes after the group opens.
        for name, drawn in drawings(SECRET)[1].items():
            with self.subTest(drawing=name):
                self.assertEqual(drawn.count('<g class="modules"'), 1)
                self.assertEqual(drawn.count(f'<g class="qr-mask" role="img" aria-label="{NAME}"'), 1)
                self.assertLess(drawn.index('<g class="modules"'), drawn.index('<g class="qr-mask"'))
                self.assertTrue(drawn.endswith(MASK.search(drawn).group(0) + "</svg>"))
        scene = drawings(SECRET)[1]["scene"]
        self.assertLess(scene.index('<use href="#qp"/>'), scene.index('<g class="modules"'))

    def test_the_mask_is_constant_across_two_payloads_and_drawn_from_no_module(self):
        # Mutation: a mask block per dark module. Red: the two payloads give two masks, and neither is the mask over
        # a symbol with no dark module.
        first, drawn = drawings(SECRET)
        second, other = drawings(OTHER)
        self.assertNotEqual(first, second)
        blank = [[False] * len(first) for _ in first]
        for name in ("svg", "scene"):
            with self.subTest(drawing=name):
                mask = MASK.findall(drawn[name])
                self.assertEqual(len(mask), 1)
                self.assertEqual(MASK.findall(other[name]), mask)
                bare = (qr.svg(blank, mask=NAME) if name == "svg" else
                        qr.scene_svg(blank, plate_almena.PLATE_DATA_URI, mask=NAME))
                self.assertEqual(MASK.findall(bare), mask)
                self.assertNotEqual(drawn[name].split('<g class="qr-mask"')[0],
                                    other[name].split('<g class="qr-mask"')[0])

    def test_the_mask_is_a_coarse_mosaic_with_the_three_finders(self):
        # Mutation: a mask one block per module. Red: more than seven columns of blocks.
        mask = MASK.search(drawings(SECRET)[1]["svg"]).group(0)
        blocks = re.findall(r'<rect x="([\d.]+)" y="([\d.]+)" width="([\d.]+)" height="\3" fill="#[0-9A-F]{6}"/>',
                            mask)
        self.assertEqual(len({x for x, _, _ in blocks}), 7)
        self.assertEqual(len({y for _, y, _ in blocks}), 7)
        self.assertEqual({width for _, _, width in blocks}, {f"{29 / 7:.2f}"})
        self.assertEqual(mask.count('width="7" height="7"'), 3)

    def test_without_a_name_no_mask_is_drawn(self):
        # The decode tests read the bare drawings: a mask there would cover the code. Mutation: the mask drawn by
        # default. Red: qr-mask in the bare drawings.
        for name, drawn in drawings(SECRET, mask=None)[1].items():
            with self.subTest(drawing=name):
                self.assertNotIn("qr-mask", drawn)
                self.assertEqual(drawn.count('<g class="modules"'), 1)


if __name__ == "__main__":
    unittest.main()
