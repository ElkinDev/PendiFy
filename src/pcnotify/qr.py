"""A QR encoder written here (ISO/IEC 18004), and its inline SVG.

Byte mode, error correction level M, versions 1 to 6, the mask chosen by the standard's four penalty
rules. The pairing address, 41 bytes, is version 3. Whether a phone's camera reads the result is proven
on the bench, never claimed from these tests alone (design section 4).
"""
from dataclasses import dataclass

from . import codes

PAIRING_ADDRESS = "https://pendiapp.com/link/pc#"
QUIET_ZONE = 4
# The drawn symbol's colours: ink on a white tile in both page themes, the finder rings in the app's violet.
_QR_INK, _QR_RING, _QR_TILE = "#1E1533", "#6D28D9", "#FFFFFF"

# Level M: version -> (EC codewords per block, blocks, data codewords per block). No short blocks up to 6.
_LEVEL_M = {1: (10, 1, 16), 2: (16, 1, 28), 3: (26, 1, 44), 4: (18, 2, 32), 5: (24, 2, 43), 6: (16, 4, 27)}
_ALIGNMENT = {1: (), 2: (6, 18), 3: (6, 22), 4: (6, 26), 5: (6, 30), 6: (6, 34)}
_LEVEL_M_BITS = 0b00
_FORMAT_GENERATOR = 0x537
_FORMAT_XOR = 0x5412
_MODE_BYTE = 0b0100
_PAD_BYTES = (0xEC, 0x11)
_FINDER_LIKE = (True, False, True, True, True, False, True)

# GF(256) over the polynomial 0x11D, as log and antilog tables.
_EXP = [0] * 512
_LOG = [0] * 256
_value = 1
for _i in range(255):
    _EXP[_i], _LOG[_value] = _value, _i
    _value <<= 1
    if _value & 0x100:
        _value ^= 0x11D
for _i in range(255, 512):
    _EXP[_i] = _EXP[_i - 255]


@dataclass(frozen=True)
class QrCode:
    version: int
    mask: int
    modules: tuple  # rows of booleans, True is dark


def pairing_address(secret):
    """The one address the QR draws: the constant and the normalized secret."""
    normal = codes.normalize(secret)
    if normal is None:
        raise ValueError("not a pairing value of twelve symbols")
    return PAIRING_ADDRESS + normal


def _gf_mul(a, b):
    return 0 if a == 0 or b == 0 else _EXP[_LOG[a] + _LOG[b]]


def reed_solomon_remainder(data, degree):
    """The EC codewords: data times x^degree modulo the generator whose roots are alpha^0 .. alpha^(degree-1)."""
    generator = [1]
    for power in range(degree):
        product = generator + [0]
        for j, coefficient in enumerate(generator):
            product[j + 1] ^= _gf_mul(coefficient, _EXP[power])
        generator = product
    remainder = [0] * degree
    for byte in data:
        factor = byte ^ remainder[0]
        remainder = remainder[1:] + [0]
        for j in range(degree):
            remainder[j] ^= _gf_mul(generator[j + 1], factor)
    return remainder


def _choose_version(length):
    for version, (_, blocks, per_block) in _LEVEL_M.items():
        if 4 + 8 + 8 * length <= blocks * per_block * 8:
            return version
    raise ValueError("the data does not fit a version 1 to 6 symbol at level M")


def _codewords(data, version):
    ec, blocks, per_block = _LEVEL_M[version]
    capacity = blocks * per_block * 8
    bits = []
    for value, width in [(_MODE_BYTE, 4), (len(data), 8)] + [(byte, 8) for byte in data]:
        bits.extend((value >> shift) & 1 for shift in range(width - 1, -1, -1))
    bits.extend([0] * min(4, capacity - len(bits)))
    bits.extend([0] * (-len(bits) % 8))
    words = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]
    words += [_PAD_BYTES[i % 2] for i in range(blocks * per_block - len(words))]
    data_blocks = [words[i * per_block:(i + 1) * per_block] for i in range(blocks)]
    ec_blocks = [reed_solomon_remainder(block, ec) for block in data_blocks]
    return ([block[i] for i in range(per_block) for block in data_blocks]
            + [block[i] for i in range(ec) for block in ec_blocks])


def _draw_format(dark, function, mask):
    size = len(dark)
    data = (_LEVEL_M_BITS << 3) | mask
    remainder = data
    for _ in range(10):
        remainder = (remainder << 1) ^ ((remainder >> 9) * _FORMAT_GENERATOR)
    bits = ((data << 10) | remainder) ^ _FORMAT_XOR
    places = ([(i, 8) for i in range(6)] + [(7, 8), (8, 8), (8, 7)] + [(8, 14 - i) for i in range(9, 15)],
              [(8, size - 1 - i) for i in range(8)] + [(size - 15 + i, 8) for i in range(8, 15)])
    for copy in places:
        for i, (r, c) in enumerate(copy):
            dark[r][c] = bool((bits >> i) & 1)
            if function is not None:
                function[r][c] = True
    dark[size - 8][8] = True
    if function is not None:
        function[size - 8][8] = True


def _function_grid(version):
    size = 17 + 4 * version
    dark = [[False] * size for _ in range(size)]
    function = [[False] * size for _ in range(size)]

    def put(r, c, value):
        dark[r][c], function[r][c] = value, True

    for i in range(size):
        put(6, i, i % 2 == 0)
        put(i, 6, i % 2 == 0)
    for top, left in ((0, 0), (0, size - 7), (size - 7, 0)):
        for dr in range(-1, 8):
            for dc in range(-1, 8):
                if 0 <= top + dr < size and 0 <= left + dc < size:
                    distance = max(abs(dr - 3), abs(dc - 3))
                    put(top + dr, left + dc, distance not in (2, 4))
    centers = _ALIGNMENT[version]
    for cr in centers:
        for cc in centers:
            if function[cr][cc]:  # the three corners a finder already holds
                continue
            for dr in range(-2, 3):
                for dc in range(-2, 3):
                    put(cr + dr, cc + dc, max(abs(dr), abs(dc)) != 1)
    _draw_format(dark, function, 0)  # reserves both format areas and the dark module
    return dark, function


def _place(dark, function, words):
    size, i, total = len(dark), 0, len(words) * 8
    right = size - 1
    while right >= 1:
        if right == 6:
            right = 5
        upward = ((right + 1) & 2) == 0
        for vertical in range(size):
            r = size - 1 - vertical if upward else vertical
            for c in (right, right - 1):
                if not function[r][c] and i < total:
                    dark[r][c] = bool((words[i >> 3] >> (7 - (i & 7))) & 1)
                    i += 1
        right -= 2


def _mask_bit(mask, r, c):
    return (
        (r + c) % 2 == 0, r % 2 == 0, c % 3 == 0, (r + c) % 3 == 0, (r // 2 + c // 3) % 2 == 0,
        (r * c) % 2 + (r * c) % 3 == 0, ((r * c) % 2 + (r * c) % 3) % 2 == 0,
        ((r + c) % 2 + (r * c) % 3) % 2 == 0,
    )[mask]


def encode(data, mask=None):
    """The symbol for `data` (bytes). The mask is the first with the lowest penalty unless one is forced."""
    data = bytes(data)
    if mask is not None and mask not in range(8):
        raise ValueError("a mask is 0 to 7")
    version = _choose_version(len(data))
    dark, function = _function_grid(version)
    _place(dark, function, _codewords(data, version))
    best = None
    for k in range(8) if mask is None else (mask,):
        candidate = [[module ^ (not function[r][c] and _mask_bit(k, r, c)) for c, module in enumerate(row)]
                     for r, row in enumerate(dark)]
        _draw_format(candidate, None, k)
        score = penalty(candidate)
        if best is None or score < best[0]:
            best = (score, k, candidate)
    return QrCode(version, best[1], tuple(tuple(row) for row in best[2]))


def _runs_penalty(lines):
    score = 0
    for line in lines:
        run, previous = 0, None
        for module in list(line) + [None]:
            if module == previous:
                run += 1
                continue
            if run >= 5:
                score += 3 + run - 5
            run, previous = 1, module
    return score


def _finder_like_penalty(lines):
    count = 0
    for line in lines:
        line = list(line)
        for x in range(len(line) - 6):
            if tuple(line[x:x + 7]) == _FINDER_LIKE and (not any(line[max(x - 4, 0):x]) or not any(line[x + 7:x + 11])):
                count += 1
    return 40 * count


def penalty(modules):
    """The four penalty rules (runs, 2 by 2 blocks, finder-like runs, dark balance); outside is light."""
    rows = [[bool(module) for module in row] for row in modules]
    columns = [list(column) for column in zip(*rows)]
    blocks = sum(1 for r in range(len(rows) - 1) for c in range(len(rows[0]) - 1)
                 if rows[r][c] == rows[r][c + 1] == rows[r + 1][c] == rows[r + 1][c + 1])
    total = sum(len(row) for row in rows)
    dark = sum(sum(row) for row in rows)
    return (_runs_penalty(rows) + _runs_penalty(columns) + 3 * blocks
            + _finder_like_penalty(rows) + _finder_like_penalty(columns)
            + 10 * (abs(dark * 2 - total) * 10 // total))


def svg(modules, labelledby=None):
    """An inline SVG of the symbol on a white tile that keeps a four-module quiet zone, one unit per module.

    The grid is the encoder's; only how a module is painted changes. A dark data module is a 0.88 dot with
    0.3 corners centred on its cell; each finder is a violet 7 by 7 ring, a white 5 by 5 separator and a 3 by 3
    eye; each alignment pattern a 5 by 5 ring, its 3 by 3 gap and its centre. Every rounded corner is small
    enough that each module's centre, where a reader samples, keeps the module's colour: a 7 by 7 ring keeps its
    corner centre inside the arc only while its radius is under 0.5*sqrt(2)/(sqrt(2)-1), about 1.707.
    """
    n, q = len(modules), QUIET_ZONE
    side = n + 2 * q
    finders = ((0, 0), (0, n - 7), (n - 7, 0))

    def in_finder(r, c):
        return any(top <= r < top + 7 and left <= c < left + 7 for top, left in finders)

    centres = _ALIGNMENT[(n - 17) // 4]
    alignments = [(r, c) for r in centres for c in centres if not in_finder(r, c)]

    def drawn_apart(r, c):
        return in_finder(r, c) or any(abs(r - a) <= 2 and abs(c - b) <= 2 for a, b in alignments)

    dots = "".join(f'<rect x="{c + q}.06" y="{r + q}.06" width=".88" height=".88" rx=".3"/>'
                   for r, row in enumerate(modules) for c, module in enumerate(row)
                   if module and not drawn_apart(r, c))
    label = f' aria-labelledby="{labelledby}"' if labelledby else ""
    out = [f'<svg class="qr" viewBox="0 0 {side} {side}" role="img"{label}>',
           f'<rect width="{side}" height="{side}" rx="1.5" fill="{_QR_TILE}"/>',
           f'<g fill="{_QR_INK}">{dots}</g>']
    for top, left in finders:
        x, y = left + q, top + q
        out.append(f'<rect x="{x}" y="{y}" width="7" height="7" rx="1.5" fill="{_QR_RING}"/>'
                   f'<rect x="{x + 1}" y="{y + 1}" width="5" height="5" rx="1.4" fill="{_QR_TILE}"/>'
                   f'<rect x="{x + 2}" y="{y + 2}" width="3" height="3" rx="1" fill="{_QR_INK}"/>')
    for a, b in alignments:
        x, y = b - 2 + q, a - 2 + q
        out.append(f'<rect x="{x}" y="{y}" width="5" height="5" rx="1.5" fill="{_QR_INK}"/>'
                   f'<rect x="{x + 1}" y="{y + 1}" width="3" height="3" rx=".9" fill="{_QR_TILE}"/>'
                   f'<rect x="{x + 2}" y="{y + 2}" width="1" height="1" rx=".3" fill="{_QR_INK}"/>')
    out.append("</svg>")
    return "".join(out)
