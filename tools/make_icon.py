"""Writes the program's icon from tools/icon_grid.py, standard library only, the same bytes on every run.

    python tools/make_icon.py           writes src/pcnotify/pcnotify.ico and src/pcnotify/icon.py
    python tools/make_icon.py --check   exits 1 when a committed file differs from what this would write

pcnotify.ico holds five frames: 16 (the 16 grid), 32 (the 32 grid), and 48, 64 and 256 from the 32 grid by nearest
neighbour. At 64 and 256 each pixel takes the source cell floor((x + 0.5) * 32 / size); at 48 it takes the cell
SOURCE_48 names, the table of the reviewed 48 px render. The frames up to 64 are 32-bit BGRA bitmaps
with their AND mask, the 256 frame is a PNG: the layout Windows reads since Vista. icon.py holds the 32 and 16 px
frames as PNG in base64 for the page. The PNGs are compressed by the small deflate below, not by zlib.compress, so
their bytes do not change with the zlib build an interpreter carries.
"""
import base64
import struct
import sys
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import icon_grid  # noqa: E402  (the grid file beside this one)

ROOT = HERE.parent
ICO_PATH = ROOT / "src" / "pcnotify" / "pcnotify.ico"
MODULE_PATH = ROOT / "src" / "pcnotify" / "icon.py"
SIZES = (16, 32, 48, 64, 256)
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
TRANSPARENT = (0, 0, 0, 0)

# The source cell of each output row and column of the 48 frame, the same table for both axes: the nearest-neighbour
# table of the reviewed 48 px render, read from its pixels. It differs from floor((x + 0.5) * 32 / 48) at 4, 16, 19,
# 22 and 25, where the render took the cell before.
SOURCE_48 = (0, 1, 1, 2, 2, 3, 4, 5, 5, 6, 7, 7, 8, 9, 9, 10, 10, 11, 12, 12, 13, 14, 14, 15, 16, 16, 17, 18, 19, 19,
             20, 21, 21, 22, 23, 23, 24, 25, 25, 26, 27, 27, 28, 29, 29, 30, 31, 31)

# Deflate's fixed tables (RFC 1951, 3.2.5): length codes 257 to 285 and distance codes 0 to 29.
LENGTH_BASE = (3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 15, 17, 19, 23, 27, 31, 35, 43, 51, 59, 67, 83, 99, 115, 131, 163,
               195, 227, 258)
LENGTH_EXTRA = (0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 2, 2, 2, 2, 3, 3, 3, 3, 4, 4, 4, 4, 5, 5, 5, 5, 0)
DISTANCE_BASE = (1, 2, 3, 4, 5, 7, 9, 13, 17, 25, 33, 49, 65, 97, 129, 193, 257, 385, 513, 769, 1025, 1537, 2049,
                 3073, 4097, 6145, 8193, 12289, 16385, 24577)
DISTANCE_EXTRA = (0, 0, 0, 0, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 6, 7, 7, 8, 8, 9, 9, 10, 10, 11, 11, 12, 12, 13, 13)
WINDOW = 32768
LONGEST = 258


class _Bits:
    """Deflate's bit order: values least significant bit first, Huffman codes most significant bit first."""

    def __init__(self):
        self.out, self.held, self.count = bytearray(), 0, 0

    def put(self, value, width):
        self.held |= value << self.count
        self.count += width
        while self.count >= 8:
            self.out.append(self.held & 0xFF)
            self.held >>= 8
            self.count -= 8

    def code(self, value, width):
        flipped = 0
        for _ in range(width):
            flipped = flipped << 1 | value & 1
            value >>= 1
        self.put(flipped, width)

    def end(self):
        if self.count:
            self.out.append(self.held & 0xFF)
        return bytes(self.out)


def _symbol(bits, value):
    if value < 144:
        bits.code(0x30 + value, 8)
    elif value < 256:
        bits.code(0x190 + value - 144, 9)
    elif value < 280:
        bits.code(value - 256, 7)
    else:
        bits.code(0xC0 + value - 280, 8)


def _pair(bits, length, distance):
    index = max(i for i, base in enumerate(LENGTH_BASE) if base <= length)
    _symbol(bits, 257 + index)
    bits.put(length - LENGTH_BASE[index], LENGTH_EXTRA[index])
    index = max(i for i, base in enumerate(DISTANCE_BASE) if base <= distance)
    bits.code(index, 5)
    bits.put(distance - DISTANCE_BASE[index], DISTANCE_EXTRA[index])


def deflate(data):
    """One final block with the fixed codes; a greedy match against the last place each three bytes were seen."""
    bits = _Bits()
    bits.put(1, 1)
    bits.put(1, 2)
    seen, at, end = {}, 0, len(data)
    while at < end:
        length = distance = 0
        if at + 3 <= end:
            key = data[at:at + 3]
            before = seen.get(key)
            seen[key] = at
            if before is not None and at - before <= WINDOW:
                limit = min(LONGEST, end - at)
                while length < limit and data[before + length] == data[at + length]:
                    length += 1
                distance = at - before
        if length >= 3:
            _pair(bits, length, distance)
            at += length
        else:
            _symbol(bits, data[at])
            at += 1
    _symbol(bits, 256)
    return bits.end()


def zlib_stream(data):
    return b"\x78\x01" + deflate(data) + struct.pack(">I", zlib.adler32(data))


def _chunk(kind, body):
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))


def png(rows):
    """An 8-bit RGBA PNG of `rows`, filter 0 on every row, no chunk beyond IHDR, IDAT and IEND."""
    raw = b"".join(b"\x00" + bytes(channel for pixel in row for channel in pixel) for row in rows)
    header = struct.pack(">IIBBBBB", len(rows[0]), len(rows), 8, 6, 0, 0, 0)
    return PNG_SIGNATURE + _chunk(b"IHDR", header) + _chunk(b"IDAT", zlib_stream(raw)) + _chunk(b"IEND", b"")


def _colour(cell):
    if cell == ".":
        return TRANSPARENT
    value = icon_grid.PALETTE[cell]
    return int(value[1:3], 16), int(value[3:5], 16), int(value[5:7], 16), 255


def frame(size):
    """The frame of `size` as (r, g, b, a) rows, top row first."""
    grid = icon_grid.GRIDS[16] if size == 16 else icon_grid.GRIDS[32]
    n = len(grid)
    source = SOURCE_48 if size == 48 else [(2 * i + 1) * n // (2 * size) for i in range(size)]
    rows = []
    for y in range(size):
        line = grid[source[y]]
        rows.append([_colour(line[source[x]]) for x in range(size)])
    return rows


def bitmap(rows):
    """A 32-bit icon bitmap: the header with the doubled height, the BGRA rows bottom-up, then the AND mask."""
    size = len(rows)
    stride = (size + 31) // 32 * 4
    header = struct.pack("<IiiHHIIiiII", 40, size, size * 2, 1, 32, 0, size * size * 4 + stride * size, 0, 0, 0, 0)
    pixels, mask = bytearray(), bytearray()
    for row in reversed(rows):
        line = bytearray(stride)
        for x, (red, green, blue, alpha) in enumerate(row):
            pixels += bytes((blue, green, red, alpha))
            if alpha == 0:
                line[x // 8] |= 0x80 >> x % 8
        mask += line
    return header + bytes(pixels) + bytes(mask)


def build_ico():
    images = [png(frame(size)) if size == 256 else bitmap(frame(size)) for size in SIZES]
    out = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    for size, image in zip(SIZES, images):
        out += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(image), offset)
        offset += len(image)
    return out + b"".join(images)


def build_icon_module():
    lines = ['"""The program\'s icon for the page: the 32 and 16 px frames of pcnotify.ico as PNG in base64.',
             "",
             "Written by tools/make_icon.py from tools/icon_grid.py: run the tool, never edit this file by hand.",
             '"""']
    for name, size in (("PNG_32", 32), ("PNG_16", 16)):
        text = base64.b64encode(png(frame(size))).decode("ascii")
        lines += ["", f"{name} = ("] + [f'    "{text[i:i + 96]}"' for i in range(0, len(text), 96)] + [")"]
    return ("\n".join(lines) + "\n").encode("ascii")


def main(argv):
    outputs = ((ICO_PATH, build_ico()), (MODULE_PATH, build_icon_module()))
    if argv == ["--check"]:
        stale = [path for path, data in outputs if not path.is_file() or path.read_bytes() != data]
        for path in stale:
            print(f"differs from what tools/make_icon.py writes: {path.relative_to(ROOT).as_posix()}", file=sys.stderr)
        return 1 if stale else 0
    if argv:
        print("usage: python tools/make_icon.py [--check]", file=sys.stderr)
        return 2
    for path, data in outputs:
        path.write_bytes(data)
        print(f"wrote {path.relative_to(ROOT).as_posix()} ({len(data)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
