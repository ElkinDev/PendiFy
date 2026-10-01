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


# The pairing QR as the scene Almena (design lane pcpg round 3, pcpg_scene_svg.py, ported as it is): the modules drawn
# as SVG polygons over one baked plate image (plate_almena.PLATE_DATA_URI, a 576 px WebP made for ELEVATION). The
# plate holds everything the scene has that no key changes: the shadow, the apron with the moat, battlements, banners,
# braziers, the two figures, the kerb, the air, and the code field lying flat with its picture, its timing, finders
# and alignment pattern and its quiet zone colours. scene_svg() draws what the key changes, through the renderer's own
# affine camera: every light module raised as a paver (its top shows the plate's own texture, lifted), the side faces
# of the pavers where a dark module lies in front, the dark modules' beds darkened, and one centre dot per data module
# in the module's colour (one projected octagon in defs, placed by <use> at each module's projected centre).
# The numbers are render_diorama.py's (Pendi-evidence Findings/05_qr/v4-2026-09-06) for ELEVATION and a version 3
# symbol, in plate units: 1 unit = 1 px of the 576 px plate = 1/2 CSS px of the page's 288 px QR box.

VIEW = 576          # plate width in px; render_diorama.py:43-45 renders OUT_PX 1600 from CANVAS 3200, the plate is that
                    # render resized to 576 (2x the page's .qr max-width of 288 px)
FIELD = 45          # render_diorama.py:554-555 field = span + 2 * RING, span = 29 modules + 2 * 4 quiet zone
RING = 4            # render_diorama.py:48 RING = APRON (3) + KERB (1), the tiles between the field edge and the code
QUIET = 4           # the quiet zone, qr.QUIET_ZONE; the renderer's code["border"] (themes_halftone.json halftone.border)
MODULES = 29        # the one symbol size the plate is drawn for: version 3, which every pairing address takes
ELEVATION = 75      # the camera's pitch in degrees, the "75" entry of pcpg-r3-camera.json (Pendi-evidence
                    # mockups/pcpg, 1135 bytes) and its plate. render_diorama.py:609 RULED_CAMERA is 45, but at 45
                    # zxing-cpp reads this composition at no box from 288 to 720 px (pcpg-r3-probe.txt), and at 60 the
                    # phone's engine (ZXing core 3.5.4, QRCodeReader, HybridBinarizer) reads 34 of 48 crops
                    # (SceneDecodeTest). 75 reads 48 of 48: 288 px on a 1x screen, and full and half size on a 2x one.
AX = 5.888          # render_diorama.py:123 x = cx + (u - v) * INV_SQRT2 * S, S from :113 (3200 * 0.92 / (45 * sqrt 2))
AY = 5.6874         # render_diorama.py:124 y term INV_SQRT2 * S * sin(ELEVATION), per unit of u + v
AH = 2.1552         # render_diorama.py:125 and :127-128 rise per unit of height, S * cos(ELEVATION)
X0 = 288.0          # render_diorama.py:114 cx, the canvas centre
Y0 = 37.0467        # render_diorama.py:115-118 cy after the centring pass, minus FIELD * AY (so y = Y0 + (u + v) * AY)
RELIEF = 0.23       # render_diorama.py:50 RELIEF = PAVER_H (:52), the height of a light module; a dark one sits at
                    # SOIL_H 0 (:51)
KERB_H = 0.62       # render_diorama.py:54, the kerb's top; its near inner edges hide the field's near rows
PAVER_INSET = 0.0   # render_diorama.py:239-240 a paver's top is its whole tile; the renderer draws no inset or gap
LIP = 0.38          # render_diorama.py:57 share of a side face drawn as its lit top edge (_face, :223-231)
LIP_LIGHT = 1.30    # render_diorama.py:229 the lip is the wall colour at 1.30
WALL_LIT = 0.70     # render_diorama.py:65 and :217 wall of a light paver, used on its +v face (:241, side 0)
WALL_DARK = 0.52    # render_diorama.py:66 and :217 the other wall, used on its +u face (:241, side 1)
BED_DARK = 0.86     # render_diorama.py:269 a dark module's bed is its texture at 0.86: here black at 0.14 opacity
DOT = 0.42          # PendiQrStudio themes_halftone.json halftone.dot, halftone.py:229: centre dot diameter in modules
GROUND = "#F6F3FA"  # the scene's code palette after halftone.palette_of (pcpg-r3-camera.json): pavers, light dots
INK = "#3A1D78"     # the ink, dark dots (darkened with their bed, as the renderer's texture is)


def _shade(hex_colour, factor):
    """render_diorama.py:135-136 shade(), on a #RRGGBB string."""
    rgb = [int(hex_colour[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join("%02X" % max(0, min(255, int(round(c * factor)))) for c in rgb)


FACE_V = _shade(GROUND, WALL_LIT)
FACE_U = _shade(GROUND, WALL_DARK)
FACE_V_LIP = _shade(FACE_V, LIP_LIGHT)
FACE_U_LIP = _shade(FACE_U, LIP_LIGHT)
_OCTAGON = ((0.21, 0.087), (0.087, 0.21), (-0.087, 0.21), (-0.21, 0.087), (-0.21, -0.087), (-0.087, -0.21),
            (0.087, -0.21), (0.21, -0.087))  # a circle of diameter DOT (0.42 modules) as 8 corners, in module units


def _pt(u, v, h):
    """render_diorama.py:120-125 Camera.project, u the field column, v the field row, h the height."""
    return X0 + (u - v) * AX, Y0 + (u + v) * AY - h * AH


def _poly(points):
    """A closed polygon, first corner absolute, the rest relative, all on the 0.1 unit grid (no drift)."""
    q = [(round(x * 10), round(y * 10)) for x, y in points]
    out = "M%s %s" % (_num(q[0][0]), _num(q[0][1]))
    for (x0, y0), (x1, y1) in zip(q, q[1:]):
        out += "l%s %s" % (_num(x1 - x0), _num(y1 - y0))
    return out + "z"


def _num(tenths):
    """An integer count of tenths as the shortest SVG number: 12 -> 1.2, -3 -> -.3, 40 -> 4."""
    sign, t = ("-" if tenths < 0 else ""), abs(tenths)
    whole, frac = divmod(t, 10)
    return sign + ((str(whole) if whole else "") + ("." + str(frac) if frac else "") or "0")


def _function(r, c, n):
    """Finders with their separators, timing, the version 3 alignment pattern: drawn flat in the plate."""
    if (r < 8 and c < 8) or (r < 8 and c >= n - 8) or (r >= n - 8 and c < 8):
        return True
    if r == 6 or c == 6:
        return True
    return abs(r - 22) <= 2 and abs(c - 22) <= 2


def _runs(cells):
    """Sorted integers to (first, last) runs."""
    out = []
    for x in cells:
        if out and x == out[-1][1] + 1:
            out[-1][1] = x
        else:
            out.append([x, x])
    return out


def scene_svg(modules, plate_data_uri, labelledby=None, uid="q"):
    """The pairing QR as the scene: the plate image under a group of polygons. None when the symbol is not version 3,
    and the caller draws svg() instead.

    Drawing order, the renderer's painter order folded into layers (draw_floor, render_diorama.py:234-274): the ink
    centre dots of the dark data modules on the floor, the symbol's floor darkened to BED_DARK, the side faces of the
    light modules that have a dark module in front, then the light modules' tops (the plate lifted by the relief,
    clipped to the light modules) and their ground dots. A light module's floor is always covered by its top,
    its faces or its near neighbours' tops, so only the dark floors stay darkened and dotted, as in the renderer. The
    near kerb clips it all."""
    n = len(modules)
    if n != MODULES:
        return None
    lo, hi = RING, RING + n + 2 * QUIET          # the code field, quiet zone included, in field tiles
    s0, s1 = RING + QUIET, RING + QUIET + n      # the symbol

    def dark(u, v):
        r, c = v - s0, u - s0
        return 0 <= r < n and 0 <= c < n and bool(modules[r][c])

    def area(c0, r0, c1, r1, h):                 # a block of whole modules, symbol coordinates, at height h
        return _poly([_pt(s0 + c0, s0 + r0, h), _pt(s0 + c1, s0 + r0, h), _pt(s0 + c1, s0 + r1, h),
                      _pt(s0 + c0, s0 + r1, h)])

    def data_region(h):                          # the symbol minus finders, separators, timing and alignment
        holes = [(0, 0, 8, 8), (n - 8, 0, n, 8), (0, n - 8, 8, n), (20, 20, 25, 25), (8, 6, n - 8, 7), (6, 8, 7, n - 8)]
        return area(0, 0, n, n, h) + "".join(area(*box, h) for box in holes)

    tops, face_u, face_v, lips_u, lips_v = [], [], [], [], []
    drop = RELIEF * AH
    cut = drop * LIP
    for v in range(lo, hi):
        for u0, u1 in _runs([u for u in range(lo, hi) if not dark(u, v)]):
            tops.append(_poly([_pt(u0 + PAVER_INSET, v, RELIEF), _pt(u1 + 1, v, RELIEF), _pt(u1 + 1, v + 1, RELIEF),
                               _pt(u0, v + 1, RELIEF)]))
        for u0, u1 in _runs([u for u in range(lo, hi) if not dark(u, v) and dark(u, v + 1)]):
            p0, p1 = _pt(u0, v + 1, RELIEF), _pt(u1 + 1, v + 1, RELIEF)
            lips_v.append(_poly([p0, p1, (p1[0], p1[1] + cut), (p0[0], p0[1] + cut)]))
            face_v.append(_poly([(p0[0], p0[1] + cut), (p1[0], p1[1] + cut), (p1[0], p1[1] + drop),
                                 (p0[0], p0[1] + drop)]))
    for u in range(lo, hi):
        for v0, v1 in _runs([v for v in range(lo, hi) if not dark(u, v) and dark(u + 1, v)]):
            p0, p1 = _pt(u + 1, v0, RELIEF), _pt(u + 1, v1 + 1, RELIEF)
            lips_u.append(_poly([p0, p1, (p1[0], p1[1] + cut), (p0[0], p0[1] + cut)]))
            face_u.append(_poly([(p0[0], p0[1] + cut), (p1[0], p1[1] + cut), (p1[0], p1[1] + drop),
                                 (p0[0], p0[1] + drop)]))
    edge = hi                                    # the near kerb's inner edges, u = edge and v = edge, at KERB_H
    a, b, c_ = _pt(edge, lo - 40, KERB_H), _pt(edge, edge, KERB_H), _pt(lo - 40, edge, KERB_H)
    kerb = _poly([a, b, c_, (c_[0], c_[1] - 4 * VIEW), (a[0], a[1] - 4 * VIEW)])
    label = f' aria-labelledby="{labelledby}"' if labelledby else ""
    faces = "".join(f'<path fill="{fill}" d="{"".join(d)}"/>' for fill, d in
                    ((FACE_V_LIP, lips_v), (FACE_V, face_v), (FACE_U_LIP, lips_u), (FACE_U, face_u)) if d)

    dot = _poly([((du - dv) * AX, (du + dv) * AY) for du, dv in _OCTAGON])   # one dot, centred on 0 0
    dots = {True: [], False: []}
    for r in range(n):
        for c in range(n):
            if not _function(r, c, n):
                x, y = _pt(s0 + c + 0.5, s0 + r + 0.5, 0.0 if modules[r][c] else RELIEF)
                dots[bool(modules[r][c])].append(f'<use href="#{uid}d" x="{x:.1f}" y="{y:.1f}"/>')
    return (f'<svg class="qr" viewBox="0 0 {VIEW} {VIEW}" role="img"{label}>'
            f'<defs><image id="{uid}p" width="{VIEW}" height="{VIEW}" href="{plate_data_uri}"/>'
            f'<clipPath id="{uid}t"><path d="{"".join(tops)}"/></clipPath>'
            f'<clipPath id="{uid}k"><path d="{kerb}"/></clipPath><path id="{uid}d" d="{dot}"/></defs>'
            f'<use href="#{uid}p"/><g clip-path="url(#{uid}k)">'
            f'<g fill="{INK}">{"".join(dots[True])}</g>'
            f'<path fill-opacity="{1 - BED_DARK:.2f}" d="{area(0, 0, n, n, 0.0)}"/>{faces}'
            f'<g clip-path="url(#{uid}t)"><use href="#{uid}p" y="{-drop:.2f}"/></g>'
            f'<g fill="{GROUND}">{"".join(dots[False])}</g></g></svg>')
