"""Minimal, pure-Python QR Code SVG generator (ISO/IEC 18004, byte mode, EC level L).

Used by the STePS booth pass modal so the web Lambda needs no third-party QR package and the
browser makes no external request. Supports byte payloads up to 154 bytes (versions 1-7).
"""

# (total_codewords, ec_codewords_per_block, num_blocks, alignment_positions) for EC level L.
_VERSIONS = {
    1: (26, 7, 1, ()),
    2: (44, 10, 1, (6, 18)),
    3: (70, 15, 1, (6, 22)),
    4: (100, 20, 1, (6, 26)),
    5: (134, 26, 1, (6, 30)),
    6: (172, 18, 2, (6, 34)),
    7: (196, 20, 2, (6, 22, 38)),
}

# Pre-computed 15-bit format strings for EC level L (01), masks 0..7, after BCH + XOR 0x5412.
_FORMAT_BITS_L = (
    0x77C4,
    0x72F3,
    0x7DAA,
    0x789D,
    0x662F,
    0x6318,
    0x6C41,
    0x6976,
)

# 18-bit version info for version 7 (versions 1-6 carry no version info block).
_VERSION_7_BITS = 0x07C94


def _gf_tables():
    exp = [0] * 512
    log = [0] * 256
    x = 1
    for i in range(255):
        exp[i] = x
        log[x] = i
        x <<= 1
        if x & 0x100:
            x ^= 0x11D
    for i in range(255, 512):
        exp[i] = exp[i - 255]
    return exp, log


_GF_EXP, _GF_LOG = _gf_tables()


def _rs_generator(degree):
    poly = [1]
    for i in range(degree):
        nxt = [0] * (len(poly) + 1)
        root = _GF_EXP[i]
        for j, coeff in enumerate(poly):
            nxt[j] ^= coeff
            nxt[j + 1] ^= _GF_EXP[(_GF_LOG[coeff] + _GF_LOG[root]) % 255] if coeff else 0
        poly = nxt
    return poly


def _rs_remainder(data, ec_len):
    gen = _rs_generator(ec_len)
    rem = [0] * ec_len
    for byte in data:
        factor = byte ^ rem.pop(0)
        rem.append(0)
        if factor:
            log_f = _GF_LOG[factor]
            for i in range(ec_len):
                g = gen[i + 1]
                if g:
                    rem[i] ^= _GF_EXP[(log_f + _GF_LOG[g]) % 255]
    return rem


def _pick_version(byte_len):
    for ver, (total_cw, ec_cw, blocks, _) in _VERSIONS.items():
        data_cw = total_cw - ec_cw * blocks
        # Byte mode header: 4 bits mode (0100) + 8 bits length (versions 1-9) = 12 bits -> 2 bytes.
        if byte_len + 2 <= data_cw:
            return ver
    raise ValueError(f"QR payload too long ({byte_len} bytes; max 154)")


def _encode_codewords(payload, ver):
    total_cw, ec_cw, blocks, _ = _VERSIONS[ver]
    data_cw = total_cw - ec_cw * blocks
    bits = [0, 1, 0, 0]  # Byte mode (0100)
    for shift in range(7, -1, -1):
        bits.append((len(payload) >> shift) & 1)
    for byte in payload:
        for shift in range(7, -1, -1):
            bits.append((byte >> shift) & 1)
    bits.extend([0] * min(4, data_cw * 8 - len(bits)))
    while len(bits) % 8:
        bits.append(0)
    codewords = [sum(bits[i + b] << (7 - b) for b in range(8)) for i in range(0, len(bits), 8)]
    pad = (0xEC, 0x11)
    while len(codewords) < data_cw:
        codewords.append(pad[len(codewords) % 2])

    block_size = data_cw // blocks
    data_blocks = [codewords[i * block_size : (i + 1) * block_size] for i in range(blocks)]
    ec_blocks = [_rs_remainder(block, ec_cw) for block in data_blocks]
    interleaved = []
    for col in range(block_size):
        for block in data_blocks:
            interleaved.append(block[col])
    for col in range(ec_cw):
        for block in ec_blocks:
            interleaved.append(block[col])
    return interleaved


def _base_grid(ver):
    size = 17 + 4 * ver
    grid = [[None] * size for _ in range(size)]
    reserved = [[False] * size for _ in range(size)]

    def set_mod(r, c, val):
        grid[r][c] = bool(val)
        reserved[r][c] = True

    def finder(top, left):
        for dr in range(-1, 8):
            for dc in range(-1, 8):
                r, c = top + dr, left + dc
                if 0 <= r < size and 0 <= c < size:
                    in_outer = 0 <= dr <= 6 and 0 <= dc <= 6 and (dr in (0, 6) or dc in (0, 6))
                    in_inner = 2 <= dr <= 4 and 2 <= dc <= 4
                    set_mod(r, c, in_outer or in_inner)

    finder(0, 0)
    finder(0, size - 7)
    finder(size - 7, 0)

    align = _VERSIONS[ver][3]
    for r in align:
        for c in align:
            if (r < 9 and c < 9) or (r < 9 and c > size - 10) or (r > size - 10 and c < 9):
                continue
            for dr in range(-2, 3):
                for dc in range(-2, 3):
                    set_mod(r + dr, c + dc, max(abs(dr), abs(dc)) != 1)

    for i in range(8, size - 8):
        if not reserved[6][i]:
            set_mod(6, i, i % 2 == 0)
        if not reserved[i][6]:
            set_mod(i, 6, i % 2 == 0)

    # Dark module + format reservation areas.
    set_mod(size - 8, 8, True)
    for i in range(9):
        if i != 6:
            reserved[8][i] = True
            reserved[i][8] = True
    for i in range(8):
        reserved[8][size - 1 - i] = True
        reserved[size - 1 - i][8] = True

    if ver == 7:
        for i in range(18):
            bit = bool((_VERSION_7_BITS >> i) & 1)
            r, c = divmod(i, 3)
            set_mod(r, size - 11 + c, bit)
            set_mod(size - 11 + c, r, bit)

    return grid, reserved


def _write_format(grid, mask):
    size = len(grid)
    fmt = _FORMAT_BITS_L[mask]
    bits = [bool((fmt >> (14 - i)) & 1) for i in range(15)]
    coords_tl = [
        (8, 0),
        (8, 1),
        (8, 2),
        (8, 3),
        (8, 4),
        (8, 5),
        (8, 7),
        (8, 8),
        (7, 8),
        (5, 8),
        (4, 8),
        (3, 8),
        (2, 8),
        (1, 8),
        (0, 8),
    ]
    for (r, c), bit in zip(coords_tl, bits, strict=True):
        grid[r][c] = bit
    for i in range(7):
        grid[size - 1 - i][8] = bits[i]
    for i in range(7, 15):
        grid[8][size - 15 + i] = bits[i]


def matrix(text):
    """Return a 2D list of booleans (True = dark module) for `text`."""
    payload = text.encode("utf-8")
    ver = _pick_version(len(payload))
    codewords = _encode_codewords(payload, ver)
    bits = [bool((byte >> (7 - b)) & 1) for byte in codewords for b in range(8)]

    grid, reserved = _base_grid(ver)
    size = len(grid)
    bit_idx = 0
    col = size - 1
    upward = True
    while col > 0:
        if col == 6:
            col -= 1
        rows = range(size - 1, -1, -1) if upward else range(size)
        for r in rows:
            for dc in (0, -1):
                c = col + dc
                if not reserved[r][c]:
                    val = bits[bit_idx] if bit_idx < len(bits) else False
                    bit_idx += 1
                    if (r + c) % 2 == 0:  # mask 0
                        val = not val
                    grid[r][c] = val
        upward = not upward
        col -= 2

    _write_format(grid, 0)
    return grid


def svg(text, *, border=4):
    """Return an inline `<svg>` string encoding `text` as a QR code."""
    grid = matrix(text)
    size = len(grid)
    total = size + 2 * border
    parts = []
    for r, row in enumerate(grid):
        c = 0
        while c < size:
            if row[c]:
                start = c
                while c < size and row[c]:
                    c += 1
                parts.append(f"M{start + border} {r + border}.5h{c - start}")
            else:
                c += 1
    path = "".join(parts)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {total} {total}" '
        f'shape-rendering="crispEdges" aria-label="QR code">'
        f'<rect width="{total}" height="{total}" fill="#FFFFFF"/>'
        f'<path stroke="#0B0C0E" d="{path}"/></svg>'
    )
