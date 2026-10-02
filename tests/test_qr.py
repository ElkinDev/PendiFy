"""QrMaskTest: both drawings of the pairing QR hold the modules in their own group and, when the page names one of the
two masks, the bust or the chessboard over them with the instruction on it, the same for every key (owner 2026-10-02
12:4x, lane pfmaskimpl; the 7 by 7 mosaic of 10:3x is gone)."""
import re
import unittest

import support
from support import SECRET

qr = support.module("qr")
page = support.module("page")
plate_almena = support.module("plate_almena")

OTHER = SECRET[::-1]
WORDS = "Press Show the code"
MASK = re.compile(r'<g class="qr-mask".*?</g>(?=</svg>)', re.S)
BUST_MARK = '<path class="pl" d="M14 3h5v1h-5z'   # the bust's first run, the plume's outline on row 3
NUMBER = re.compile(r"-?(?:\d+\.?\d*|\.\d+)")


def drawings(secret, mask="bust", words=WORDS):
    modules = qr.encode(qr.pairing_address(secret).encode("ascii")).modules
    return modules, {"svg": qr.svg(modules, mask=mask, mask_words=words),
                     "scene": qr.scene_svg(modules, plate_almena.PLATE_DATA_URI, mask=mask, mask_words=words)}


def polygons(d):
    """The closed polygons of a path drawn by qr._poly: the first corner absolute, the rest relative."""
    out = []
    for polygon in re.findall(r"M[^z]*z", d):
        numbers = [float(n) for n in NUMBER.findall(polygon)]
        x, y = numbers[0], numbers[1]
        corners = [(x, y)]
        for dx, dy in zip(numbers[2::2], numbers[3::2]):
            x, y = x + dx, y + dy
            corners.append((x, y))
        out.append(corners)
    return out


def inside(polygon, point, slack=0.11):
    """A point inside a convex polygon or on its edge, within the 0.1 unit grid the path is rounded to."""
    sides = []
    for (x0, y0), (x1, y1) in zip(polygon, polygon[1:] + polygon[:1]):
        sides.append(((x1 - x0) * (point[1] - y0) - (y1 - y0) * (point[0] - x0)) / ((x1 - x0) ** 2 + (y1 - y0) ** 2)
                     ** .5)
    return all(side >= -slack for side in sides) or all(side <= slack for side in sides)


class QrMaskTest(unittest.TestCase):
    def test_both_drawings_hold_the_modules_group_and_the_mask_group_after_it(self):
        # Mutation: the mask drawn inside the modules group. Red: the modules group closes after the mask opens.
        # Mutation: the plate drawn inside the modules group. Red: the plate comes after the group opens.
        for mask in qr.MASKS:
            for name, drawn in drawings(SECRET, mask)[1].items():
                with self.subTest(mask=mask, drawing=name):
                    self.assertEqual(drawn.count('<g class="modules"'), 1)
                    self.assertEqual(drawn.count(f'<g class="qr-mask" role="img" aria-label="{WORDS}">'), 1)
                    self.assertLess(drawn.index('<g class="modules"'), drawn.index('<g class="qr-mask"'))
                    self.assertTrue(drawn.endswith(MASK.search(drawn).group(0) + "</svg>"))
        scene = drawings(SECRET)[1]["scene"]
        self.assertLess(scene.index('<use href="#qp"/>'), scene.index('<g class="modules"'))

    def test_the_mask_is_constant_across_two_payloads_and_drawn_from_no_module(self):
        # Mutation: a mask block per dark module. Red: the two payloads give two masks, and neither is the mask over
        # a symbol with no dark module.
        for mask in qr.MASKS:
            first, drawn = drawings(SECRET, mask)
            second, other = drawings(OTHER, mask)
            self.assertNotEqual(first, second)
            blank = [[False] * len(first) for _ in first]
            for name in ("svg", "scene"):
                with self.subTest(mask=mask, drawing=name):
                    found = MASK.findall(drawn[name])
                    self.assertEqual(len(found), 1)
                    self.assertEqual(MASK.findall(other[name]), found)
                    bare = (qr.svg(blank, mask=mask, mask_words=WORDS) if name == "svg" else
                            qr.scene_svg(blank, plate_almena.PLATE_DATA_URI, mask=mask, mask_words=WORDS))
                    self.assertEqual(MASK.findall(bare), found)
                    self.assertNotEqual(drawn[name].split('<g class="qr-mask"')[0],
                                        other[name].split('<g class="qr-mask"')[0])

    def test_the_masks_are_the_bust_and_the_board_each_with_the_instruction(self):
        # Mutation: the 7 by 7 mosaic kept. Red: _MASK_ART is still a name of qr. Mutation: the words left off the
        # board. Red: the board's mask holds no text. Mutation: a plate under the words. Red: a rect in the mask.
        self.assertEqual(qr.MASKS, ("bust", "board"))
        for gone in ("_MASK_ART", "_mask_blocks"):
            self.assertFalse(hasattr(qr, gone), gone)
        for mask in qr.MASKS:
            for name, drawn in drawings(SECRET, mask)[1].items():
                with self.subTest(mask=mask, drawing=name):
                    found = MASK.search(drawn).group(0)
                    self.assertEqual(re.findall(r"<text [^>]*>([^<]*)</text>", found), [WORDS])
                    self.assertEqual((BUST_MARK in found, found.count('class="qm-1"'), found.count('class="qm-2"')),
                                     (True, 0, 0) if mask == "bust" else (False, 1, 1))
                    for gone in ("<rect", "rx=", "<image", "<use"):
                        self.assertNotIn(gone, found)

    def test_the_bust_is_the_page_s_own_pixel_drawing_at_the_mockup_s_place(self):
        # The mockup's bust: page._paths of the 32 rows, in the party's colours and the outline token, translated to
        # the symbol's centre less 16 blocks and scaled to 10.5 plate units a block. Mutation: a colour off the party's
        # palette. Red: the paths differ from page._paths. Mutation: the bust scaled to 10. Red: the group differs.
        self.assertEqual((len(qr._BUST), {len(row) for row in qr._BUST}), (32, {32}))
        self.assertEqual({key for row in qr._BUST for key in row} - {".", "o"}, set("vdlyahs"))
        s0 = qr.RING + qr.QUIET
        cx, cy = qr._pt(s0 + 14.5, s0 + 14.5, 0.0)
        found = MASK.search(drawings(SECRET, "bust")[1]["scene"]).group(0)
        self.assertIn(f'<g transform="translate({cx - 168:.2f} {cy - 168:.2f}) scale(10.5)" shape-rendering="crispEdges">'
                      + page._paths(qr._BUST) + "</g>", found)
        self.assertIn(f'<text class="qm-say" x="{cx:.1f}" y="{cy - 168 + 23.6 * 10.5:.1f}">{WORDS}</text>', found)
        board = MASK.search(drawings(SECRET, "board")[1]["scene"]).group(0)
        self.assertIn(f'<text class="qm-say qm-board" x="{cx:.1f}" y="{cy:.1f}">{WORDS}</text>', board)

    def test_no_finder_or_module_grid_shows_through_either_mask(self):
        # The review's verified point: one GROUND path over the whole symbol, finders included, first in the mask.
        # Mutation: the ground path drawn over the data region only. Red: the finders' corners fall outside it.
        n, q, s0 = 29, qr.QUIET_ZONE, qr.RING + qr.QUIET
        finders = ((0, 0), (0, n - 7), (n - 7, 0))
        for mask in qr.MASKS:
            drawn = drawings(SECRET, mask)[1]
            with self.subTest(mask=mask, drawing="scene"):
                found = MASK.search(drawn["scene"]).group(0)
                ground = re.match(rf'<g class="qr-mask" role="img" aria-label="{WORDS}"><g clip-path="url\(#qk\)">'
                                  rf'<path fill="{qr.GROUND}" d="([^"]*)"/>', found)
                self.assertIsNotNone(ground, found[:200])
                [polygon] = polygons(ground.group(1))
                for top, left in finders:
                    for dc, dr in ((0, 0), (7, 0), (7, 7), (0, 7), (3.5, 3.5)):
                        self.assertTrue(inside(polygon, qr._pt(s0 + left + dc, s0 + top + dr, 0.0)),
                                        (top, left, dc, dr))
            with self.subTest(mask=mask, drawing="svg"):
                found = MASK.search(drawn["svg"]).group(0)
                ground = re.match(rf'<g class="qr-mask" role="img" aria-label="{WORDS}">'
                                  rf'<path fill="{qr.GROUND}" d="M(\d+) (\d+)h(\d+)v(\d+)h-(\d+)z"/>', found)
                self.assertIsNotNone(ground, found[:200])
                x, y, w, h, back = (int(v) for v in ground.groups())
                for top, left in finders:
                    self.assertTrue(x <= q + left and q + left + 7 <= x + w and y <= q + top and q + top + 7 <= y + h
                                    and w == back, (top, left, ground.group(0)))

    def test_a_mask_needs_its_words_and_one_of_the_two_names(self):
        # Mutation: an unknown name drawn as the bust. Red: no ValueError. Mutation: a mask with no words drawn
        # unnamed. Red: no ValueError.
        modules = drawings(SECRET)[0]
        for call in (lambda: qr.svg(modules, mask="bust"),
                     lambda: qr.scene_svg(modules, plate_almena.PLATE_DATA_URI, mask="board"),
                     lambda: qr.svg(modules, mask="mosaic", mask_words=WORDS),
                     lambda: qr.scene_svg(modules, plate_almena.PLATE_DATA_URI, mask="Hidden code", mask_words=WORDS)):
            with self.assertRaises(ValueError):
                call()

    def test_without_a_name_no_mask_is_drawn(self):
        # The decode tests read the bare drawings: a mask there would cover the code. Mutation: the mask drawn by
        # default. Red: qr-mask in the bare drawings.
        for name, drawn in drawings(SECRET, mask=None, words=None)[1].items():
            with self.subTest(drawing=name):
                self.assertNotIn("qr-mask", drawn)
                self.assertEqual(drawn.count('<g class="modules"'), 1)


if __name__ == "__main__":
    unittest.main()
