"""CodesTest: the pairing value's alphabet, its mint, its normalization and its 4-4-4 display.

W is the Worker at C:/Repo/FollowApp-lnk2/proxy (tip 5f2e44334); every vector names its source line.
"""
import ast
import inspect
import unittest

import support

codes = support.module("codes")

# (raw, expected, source)
VECTORS = [
    ("ABCD2345EFGH", "ABCD2345EFGH", "W test/link.test.ts:874, the Worker's SECRET"),
    ("ABCD-2345-EFGH", "ABCD2345EFGH", "W test/link.test.ts:937, the 4-4-4 dashes"),
    ("abcd-2345-efgh", "ABCD2345EFGH", "W test/link.test.ts:1066 and :1160, lower case"),
    ("  ABCD 2345 EFGH  ", "ABCD2345EFGH", "W src/link-registry.ts:99, spaces are outside the alphabet"),
    ("ABCD0-2345-EFGH", "ABCD2345EFGH", "W src/link-registry.ts:99, a 0 is dropped, twelve remain"),
    ("ABCD2345EFG", None, "W test/link.test.ts:1102, eleven symbols"),
    ("ABCD2345EFGHJ", None, "W src/link-registry.ts:100, thirteen symbols"),
    ("ABCD-2345-EF0H", None, "W test/link.test.ts:1102, the 0 dropped leaves eleven"),
    ("ABCD2345EFOH", None, "W src/link-registry.ts:30-31, an O is outside the alphabet"),
    ("ABCD2345EF1H", None, "W src/link-registry.ts:30-31, a 1 is outside the alphabet"),
    ("ABCD2345EFIH", None, "W src/link-registry.ts:30-31, an I is outside the alphabet"),
    ("ABCD2345EFGH" + " " * 20, "ABCD2345EFGH", "W src/link-registry.ts:45 and :98, 32 characters pass the bound"),
    ("ABCD2345EFGH" + " " * 21, None, "W src/link-registry.ts:45 and :98, 33 characters fail the bound"),
    (12, None, "W test/link.test.ts:1102, a number"),
    (None, None, "W test/link.test.ts:1102, a null"),
    (b"ABCD2345EFGH", None, "W src/link-registry.ts:98, bytes are not a string"),
    (["ABCD2345EFGH"], None, "W src/link-registry.ts:98, a list is not a string"),
]


class CodesTest(unittest.TestCase):
    def test_alphabet_length_and_bound_are_the_workers(self):
        # Mutation: an O added to the alphabet. Red: the constant differs from W src/link-registry.ts:31.
        self.assertEqual(codes.ALPHABET, "ABCDEFGHJKLMNPQRSTUVWXYZ23456789")  # W src/link-registry.ts:31
        self.assertEqual(codes.LENGTH, 12)  # W src/link-registry.ts:32
        self.assertEqual(codes.MAX_RAW_CHARS, 32)  # W src/link-registry.ts:45

    def test_a_mint_is_twelve_symbols_of_the_alphabet(self):
        # Mutation: mint draws eleven symbols. Red: a length of 11.
        minted = [codes.mint() for _ in range(200)]
        for value in minted:
            self.assertEqual(len(value), 12)
            self.assertEqual(set(value) - set(codes.ALPHABET), set())
            self.assertEqual(codes.normalize(value), value)
        self.assertEqual(len(set(minted)), 200)
        # 2400 draws miss one given symbol with probability (31/32)^2400, about e^-76.
        self.assertEqual(set("".join(minted)), set(codes.ALPHABET))

    def test_a_mint_comes_from_the_secrets_module(self):
        # Mutation: mint draws with random.choice. Red: random is imported and mint names no secrets call.
        tree = ast.parse(inspect.getsource(codes))
        imported = {alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import)
                    for alias in node.names}
        imported |= {node.module.split(".")[0] for node in ast.walk(tree)
                     if isinstance(node, ast.ImportFrom) and node.module}
        self.assertIn("secrets", imported)
        self.assertNotIn("random", imported)
        mint = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "mint")
        calls = {(n.func.value.id, n.func.attr) for n in ast.walk(mint)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                 and isinstance(n.func.value, ast.Name)}
        self.assertEqual(calls, {("secrets", "choice")})

    def test_normalize_answers_what_the_workers_normalize_link_code_answers(self):
        # Mutation: the filter dropped, only upper-casing kept. Red: the dashed and spaced vectors answer None.
        for raw, expected, source in VECTORS:
            with self.subTest(source=source):
                self.assertEqual(codes.normalize(raw), expected)

    def test_the_raw_bound_counts_utf_16_units_as_the_workers_raw_length_does(self):
        # Mutation: len() restored in normalize. Red: 17 astral characters and twelve symbols normalize here.
        astral = "\U0001F600"  # outside the Basic Multilingual Plane: two UTF-16 units, one code point
        self.assertIsNone(codes.normalize(astral * 17 + "ABCD2345EFGH"))  # 46 units, null at W src/link-registry.ts:98
        self.assertIsNone(codes.normalize(astral * 10 + "ABCD2345EFGH "))  # 33 units
        self.assertEqual(codes.normalize(astral * 10 + "ABCD2345EFGH"), "ABCD2345EFGH")  # 32 units pass
        self.assertEqual(codes.normalize("--ABCD-2345-EFGH" + "-" * 16), "ABCD2345EFGH")  # 32 plain characters
        # A lone surrogate is one unit in a JavaScript string, and never an encoding error here.
        self.assertEqual(codes.normalize("\ud800" * 20 + "ABCD2345EFGH"), "ABCD2345EFGH")
        self.assertIsNone(codes.normalize("\ud800" * 21 + "ABCD2345EFGH"))

    def test_display_is_four_four_four(self):
        # Mutation: display groups by three. Red: ABC-D23-45E-FGH.
        self.assertEqual(codes.display("ABCD2345EFGH"), "ABCD-2345-EFGH")
        self.assertEqual(codes.display("abcd-2345-efgh"), "ABCD-2345-EFGH")

    def test_display_refuses_a_malformed_value_without_echoing_it(self):
        # Mutation: display formats whatever it gets. Red: no ValueError for eleven symbols.
        with self.assertRaises(ValueError) as caught:
            codes.display("ABCD2345EFG")
        self.assertNotIn("ABCD", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
