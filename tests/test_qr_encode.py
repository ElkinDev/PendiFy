"""QrEncodeTest: the encoder written here (byte mode, level M, versions 1 to 6) and its SVG."""
import re
import unittest

import support
from support import SECRET

qr = support.module("qr")

ADDRESS = "https://pendiapp.com/link/pc#" + SECRET
FORMAT_XOR = 0x5412
FORMAT_GENERATOR = 0x537


def format_copies(m):
    """The fifteen format bits of both copies, bit i as ISO/IEC 18004 places it (8.9)."""
    size = len(m)
    first = [m[i][8] for i in range(6)] + [m[7][8], m[8][8], m[8][7]] + [m[8][14 - i] for i in range(9, 15)]
    second = [m[8][size - 1 - i] for i in range(8)] + [m[size - 15 + i][8] for i in range(8, 15)]
    as_int = lambda bits: sum(int(bit) << i for i, bit in enumerate(bits))
    return as_int(first), as_int(second)


def bch_remainder(value):
    for shift in range(4, -1, -1):
        if value & (1 << (shift + 10)):
            value ^= FORMAT_GENERATOR << shift
    return value


class QrEncodeTest(unittest.TestCase):
    def setUp(self):
        self.code = qr.encode(qr.pairing_address(SECRET).encode("ascii"))
        self.m = self.code.modules

    def test_reed_solomon_remainder_of_the_standards_worked_example(self):
        # Mutation: the generator's roots start at alpha^1. Red: a different remainder.
        data = [32, 91, 11, 120, 209, 114, 220, 77, 67, 64, 236, 17, 236, 17, 236, 17]
        self.assertEqual(qr.reed_solomon_remainder(data, 10), [196, 35, 39, 119, 235, 215, 231, 226, 93, 23])

    def test_the_pairing_address_is_one_constant_and_the_normalized_secret(self):
        # Mutation: the address keeps the dashed display form. Red: 43 bytes and a dash in the fragment.
        self.assertEqual(qr.PAIRING_ADDRESS, "https://pendiapp.com/link/pc#")
        self.assertEqual(qr.pairing_address("abcd-2345-efgh"), ADDRESS)
        self.assertEqual(len(ADDRESS.encode("ascii")), 41)
        with self.assertRaises(ValueError):
            qr.pairing_address("ABCD2345EFG")

    def test_the_pairing_address_is_version_3_a_29_by_29_matrix_of_booleans(self):
        # Mutation: the version chosen by level L capacities. Red: version 2, 25 by 25.
        self.assertEqual(self.code.version, 3)
        self.assertEqual(len(self.m), 29)
        self.assertEqual({len(row) for row in self.m}, {29})
        self.assertEqual({type(module) for row in self.m for module in row}, {bool})

    def test_the_three_finder_patterns_and_their_separators_are_in_place(self):
        # Mutation: the mask applied to a function module. Red: a finder module flipped.
        def finder(r, c):
            return max(abs(r - 3), abs(c - 3)) != 2
        for top, left in ((0, 0), (0, 22), (22, 0)):
            for r in range(7):
                for c in range(7):
                    self.assertEqual(self.m[top + r][left + c], finder(r, c), (top, left, r, c))
        for i in range(8):
            self.assertEqual([self.m[7][i], self.m[i][7], self.m[7][28 - i], self.m[i][21],
                              self.m[21][i], self.m[28 - i][7]], [False] * 6, i)

    def test_the_timing_rows_the_alignment_pattern_and_the_dark_module_are_in_place(self):
        # Mutation: the mask applied to a function module. Red: a timing module flipped.
        for i in range(8, 21):
            self.assertEqual(self.m[6][i], i % 2 == 0, ("row 6", i))
            self.assertEqual(self.m[i][6], i % 2 == 0, ("column 6", i))
        for r in range(5):
            for c in range(5):
                self.assertEqual(self.m[20 + r][20 + c], max(abs(r - 2), abs(c - 2)) != 1, (r, c))
        self.assertTrue(self.m[21][8])  # 4 * version + 9

    def test_the_format_bits_are_a_valid_bch_word_naming_level_m_and_the_chosen_mask(self):
        # Mutation: the mask applied to a function module. Red: the two copies disagree or fail the BCH check.
        first, second = format_copies(self.m)
        self.assertEqual(first, second)
        word = first ^ FORMAT_XOR
        self.assertEqual(bch_remainder(word), 0)
        self.assertEqual(word >> 13, 0b00)  # level M
        self.assertEqual((word >> 10) & 0b111, self.code.mask)

    def test_the_chosen_mask_is_the_first_with_the_lowest_penalty(self):
        # Mutation: mask 0 always. Red: mask 0 is not the lowest penalty for this address.
        data = qr.pairing_address(SECRET).encode("ascii")
        penalties = [qr.penalty(qr.encode(data, mask=k).modules) for k in range(8)]
        self.assertEqual(self.code.mask, penalties.index(min(penalties)))
        self.assertEqual(qr.encode(data, mask=self.code.mask).modules, self.m)
        for k in range(8):
            self.assertEqual((format_copies(qr.encode(data, mask=k).modules)[0] ^ FORMAT_XOR) >> 10 & 7, k)

    def test_the_penalty_rules_on_hand_computed_matrices(self):
        # Mutation: rule 2 counts nothing. Red: the all-dark total drops by 2352.
        dark = [[True] * 29 for _ in range(29)]
        # Rule 1: 58 lines of 29, 3 + 24 each; rule 2: 28 * 28 blocks of 3; rule 4: 10 steps of 5 pct, 10 each.
        self.assertEqual(qr.penalty(dark), 58 * 27 + 784 * 3 + 100)
        checker = [[(r + c) % 2 == 0 for c in range(29)] for r in range(29)]
        self.assertEqual(qr.penalty(checker), 0)
        # Rule 3: one 1:1:3:1:1 run with four light modules each side (40); rule 4: 5 of 15 dark, 3 steps (30).
        row = [bit == "1" for bit in "000010111010000"]
        self.assertEqual(qr.penalty([row]), 40 + 30)

    def test_versions_one_to_six_and_nothing_larger(self):
        # Mutation: the version table stops at 5. Red: 106 bytes raise.
        for length, version in ((14, 1), (15, 2), (26, 2), (27, 3), (42, 3), (43, 4), (62, 4), (63, 5), (84, 5),
                                (85, 6), (106, 6)):
            with self.subTest(length=length):
                code = qr.encode(b"a" * length)
                self.assertEqual((code.version, len(code.modules)), (version, 17 + 4 * version))
        with self.assertRaises(ValueError):
            qr.encode(b"a" * 107)

    def test_the_svg_carries_a_four_module_quiet_zone_and_every_dark_module(self):
        # Mutation: the quiet zone set to 0. Red: a 29 viewBox and a module drawn at 0.
        svg = qr.svg(self.m)
        self.assertTrue(svg.startswith("<svg "))
        self.assertIn('viewBox="0 0 37 37"', svg)
        self.assertIn('<rect width="37" height="37" fill="#ffffff"/>', svg)
        cells = {(int(x), int(y)) for x, y in re.findall(r"M(\d+) (\d+)h1v1h-1z", svg)}
        expected = {(c + 4, r + 4) for r in range(29) for c in range(29) if self.m[r][c]}
        self.assertEqual(cells, expected)
        self.assertEqual(min(x for x, _ in cells), 4)
        self.assertEqual(max(y for _, y in cells), 32)


if __name__ == "__main__":
    unittest.main()
