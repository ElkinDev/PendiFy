"""IconFileTest: the program's icon, its grids, the tool that writes it and the two files it writes.

Every image here is read as bytes by the decoders below (struct and zlib), never opened as an image.
"""
import base64
import importlib.util
import math
import shutil
import struct
import subprocess
import sys
import unittest
import zlib
from pathlib import Path

import support

TOOLS = support.ROOT / "tools"
ICO = support.package_dir() / "pcnotify.ico"
ICON_PY = support.package_dir() / "icon.py"
SIZES = [16, 32, 48, 64, 256]
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
TRANSPARENT = (0, 0, 0, 0)


def load_tool(name):
    spec = importlib.util.spec_from_file_location("pcnotify_tools_" + name, TOOLS / (name + ".py"))
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def decode_png(data):
    """(width, height, rows) of an 8-bit RGBA or RGB PNG, each row a list of (r, g, b, a)."""
    if data[:8] != PNG_SIGNATURE:
        raise AssertionError("not a PNG signature")
    at, header, joined = 8, None, b""
    while at < len(data):
        length, kind = struct.unpack(">I4s", data[at:at + 8])
        body = data[at + 8:at + 8 + length]
        crc = struct.unpack(">I", data[at + 8 + length:at + 12 + length])[0]
        if zlib.crc32(kind + body) != crc:
            raise AssertionError("bad CRC in " + kind.decode("ascii"))
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", body)
        elif kind == b"IDAT":
            joined += body
        elif kind == b"IEND":
            break
        at += 12 + length
    width, height, depth, colour, _, _, interlace = header
    if depth != 8 or colour not in (2, 6) or interlace != 0:
        raise AssertionError(f"unsupported PNG form {header}")
    channels = 4 if colour == 6 else 3
    stride = width * channels
    raw = zlib.decompress(joined)
    if len(raw) != height * (stride + 1):
        raise AssertionError("IDAT holds the wrong number of bytes")
    rows, above = [], bytearray(stride)
    for y in range(height):
        kind = raw[y * (stride + 1)]
        line = bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
        for i in range(stride):
            left = line[i - channels] if i >= channels else 0
            up = above[i]
            corner = above[i - channels] if i >= channels else 0
            if kind == 1:
                line[i] = (line[i] + left) & 0xFF
            elif kind == 2:
                line[i] = (line[i] + up) & 0xFF
            elif kind == 3:
                line[i] = (line[i] + (left + up) // 2) & 0xFF
            elif kind == 4:
                guess = left + up - corner
                pa, pb, pc = abs(guess - left), abs(guess - up), abs(guess - corner)
                pick = left if pa <= pb and pa <= pc else (up if pb <= pc else corner)
                line[i] = (line[i] + pick) & 0xFF
            elif kind != 0:
                raise AssertionError(f"unknown filter {kind}")
        above = line
        pixels = [tuple(line[x * channels:(x + 1) * channels]) for x in range(width)]
        rows.append([p if channels == 4 else p + (255,) for p in pixels])
    return width, height, rows


def decode_bitmap(data, size):
    """The rows of a 32-bit icon bitmap (BGRA, bottom-up, then its AND mask) as (r, g, b, a), top row first."""
    fields = struct.unpack("<IiiHHIIiiII", data[:40])
    if fields[:5] != (40, size, size * 2, 1, 32) or fields[5] != 0:
        raise AssertionError(f"bitmap header {fields[:6]} for size {size}")
    pixels = data[40:40 + size * size * 4]
    mask_stride = (size + 31) // 32 * 4
    mask = data[40 + size * size * 4:]
    if len(mask) != mask_stride * size:
        raise AssertionError("AND mask of the wrong length")
    rows = []
    for y in range(size):
        bottom = size - 1 - y
        row = []
        for x in range(size):
            b, g, r, a = pixels[(bottom * size + x) * 4:(bottom * size + x) * 4 + 4]
            masked = mask[bottom * mask_stride + x // 8] >> (7 - x % 8) & 1
            if masked != (a == 0):
                raise AssertionError(f"AND bit {masked} against alpha {a} at {x},{y}")
            row.append((r, g, b, a))
        rows.append(row)
    return rows


def decode_ico(data):
    """[(size, kind, rows)] for every frame of an .ico, kind "png" or "bmp"."""
    reserved, kind, count = struct.unpack("<HHH", data[:6])
    if (reserved, kind) != (0, 1):
        raise AssertionError("not an icon header")
    frames = []
    for index in range(count):
        width, height, colours, zero, planes, bits, length, offset = struct.unpack(
            "<BBBBHHII", data[6 + index * 16:22 + index * 16])
        size = width or 256
        if (height or 256) != size or (colours, zero, planes, bits) != (0, 0, 1, 32):
            raise AssertionError(f"directory entry {index} is not a square 32-bit frame")
        body = data[offset:offset + length]
        if len(body) != length:
            raise AssertionError(f"frame {index} runs past the file")
        if body[:8] == PNG_SIGNATURE:
            w, h, rows = decode_png(body)
            if (w, h) != (size, size):
                raise AssertionError(f"PNG frame {w}x{h} under a {size} entry")
            frames.append((size, "png", rows))
        else:
            frames.append((size, "bmp", decode_bitmap(body, size)))
    return frames


# The 48 frame's source cell per row and column, written here on its own (never read from the tool): the
# nearest-neighbour table of the reviewed 48 px render, which the owner approved.
SOURCE_48 = [0, 1, 1, 2, 2, 3, 4, 5, 5, 6, 7, 7, 8, 9, 9, 10, 10, 11, 12, 12, 13, 14, 14, 15, 16, 16, 17, 18, 19, 19,
             20, 21, 21, 22, 23, 23, 24, 25, 25, 26, 27, 27, 28, 29, 29, 30, 31, 31]


def expected_rows(grid_module, size):
    """The frame of `size`: the 16 grid at 16, else the 32 grid; at 48 each pixel takes the source cell SOURCE_48
    names, at the other sizes the cell floor((x + 0.5) * n / size)."""
    grid = grid_module.GRIDS[16] if size == 16 else grid_module.GRIDS[32]
    n = len(grid)
    if size == 48:
        source = SOURCE_48
    else:
        source = [math.floor((i + 0.5) * n / size) for i in range(size)]
    rows = []
    for y in range(size):
        line = grid[source[y]]
        row = []
        for x in range(size):
            cell = line[source[x]]
            if cell == ".":
                row.append(TRANSPARENT)
            else:
                colour = grid_module.PALETTE[cell]
                row.append((int(colour[1:3], 16), int(colour[3:5], 16), int(colour[5:7], 16), 255))
        rows.append(row)
    return rows


def run_check(root):
    return subprocess.run([sys.executable, str(root / "tools" / "make_icon.py"), "--check"], cwd=str(root),
                          capture_output=True, text=True, timeout=120)


class IconFileTest(unittest.TestCase):
    def test_the_committed_icon_files_are_what_the_tool_writes(self):
        # Mutation: one byte of pcnotify.ico flipped. Red: --check exits 1.
        done = run_check(support.ROOT)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        with support.temp_dir() as work:
            copy = Path(work)
            shutil.copytree(TOOLS, copy / "tools", ignore=shutil.ignore_patterns("__pycache__"))
            target = copy / "src" / support.PACKAGE
            target.mkdir(parents=True)
            shutil.copy(ICON_PY, target)
            data = bytearray(ICO.read_bytes())
            data[len(data) // 2] ^= 0xFF
            (target / "pcnotify.ico").write_bytes(bytes(data))
            self.assertEqual(run_check(copy).returncode, 1)
            shutil.copy(ICO, target)
            self.assertEqual(run_check(copy).returncode, 0)
            (target / "icon.py").write_text("PNG_32 = ''\n", encoding="ascii")
            self.assertEqual(run_check(copy).returncode, 1)

    def test_the_tool_writes_the_same_bytes_twice(self):
        # Mutation: a timestamp chunk in the PNG. Red: two builds differ.
        tool = load_tool("make_icon")
        self.assertEqual(tool.build_ico(), tool.build_ico())
        self.assertEqual(tool.build_icon_module(), tool.build_icon_module())

    def test_the_icon_holds_five_frames_each_equal_to_its_grid(self):
        # Mutation: one SOURCE_48 entry moved by one (in the tool, the icon regenerated). Red: the 48 frame differs
        # from the test's own table. Mutation: the AND mask left all zero. Red: the decoder sees an opaque bit over
        # a transparent pixel.
        grid = load_tool("icon_grid")
        frames = decode_ico(ICO.read_bytes())
        self.assertEqual([size for size, _, _ in frames], SIZES)
        self.assertEqual([kind for _, kind, _ in frames], ["bmp", "bmp", "bmp", "bmp", "png"])
        for size, _, rows in frames:
            with self.subTest(size=size):
                self.assertEqual(rows, expected_rows(grid, size))
                corners = [rows[0][0], rows[0][-1], rows[-1][0], rows[-1][-1]]
                self.assertEqual(corners, [TRANSPARENT] * 4)

    def test_the_256_frame_is_a_png_of_256_by_256(self):
        # Mutation: the 256 frame written as a bitmap. Red: no PNG signature at its offset.
        data = ICO.read_bytes()
        offset = struct.unpack("<I", data[6 + 4 * 16 + 12:6 + 4 * 16 + 16])[0]
        self.assertEqual(data[offset:offset + 8], PNG_SIGNATURE)
        self.assertEqual(data[offset + 12:offset + 16], b"IHDR")
        self.assertEqual(struct.unpack(">II", data[offset + 16:offset + 24]), (256, 256))

    def test_the_page_constants_decode_to_the_32_and_16_grids(self):
        # Mutation: PNG_16 written from the 32 grid scaled down. Red: the 16 constant is not the 16 grid.
        grid = load_tool("icon_grid")
        icon = support.module("icon")
        for name, size in (("PNG_32", 32), ("PNG_16", 16)):
            with self.subTest(name=name):
                width, height, rows = decode_png(base64.b64decode(getattr(icon, name), validate=True))
                self.assertEqual((width, height), (size, size))
                self.assertEqual(rows, expected_rows(grid, size))
        self.assertIn("tools/make_icon.py", icon.__doc__)

    def test_the_grids_hold_only_palette_characters_and_every_edge_cell_is_the_outline(self):
        # Mutation: one outline cell of the 16 grid turned white. Red: an edge cell that is not "o".
        grid = load_tool("icon_grid")
        self.assertIn("own drawing", grid.__doc__)
        self.assertEqual(sorted(grid.GRIDS), [16, 32])
        for n, rows in grid.GRIDS.items():
            with self.subTest(n=n):
                self.assertEqual([len(row) for row in rows], [n] * n)
                self.assertEqual(set("".join(rows)) - set(grid.PALETTE) - {"."}, set())
                edges = []
                for y, row in enumerate(rows):
                    for x, cell in enumerate(row):
                        if cell == ".":
                            continue
                        around = [(x + dx, y + dy) for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))]
                        if any(not (0 <= ax < n and 0 <= ay < n) or rows[ay][ax] == "." for ax, ay in around):
                            edges.append((x, y, cell))
                self.assertTrue(edges)
                self.assertEqual([edge for edge in edges if edge[2] != grid.OUTLINE], [])
        self.assertEqual(grid.PALETTE[grid.OUTLINE], "#8A5CF0")


if __name__ == "__main__":
    unittest.main()
