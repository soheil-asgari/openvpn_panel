"""Minimal dependency-free QR code generator (byte mode, versions 1-10).

Implements enough of ISO/IEC 18004 to render a scannable QR code as SVG. Only
byte-mode (UTF-8) payloads with error-correction level M and mask 0 are
supported, which covers the short ``.ovpn`` import URLs used by the
subscription page. The implementation is intentionally self-contained so the
panel keeps its "no third-party runtime dependency" property.
"""

from __future__ import annotations

import re

# --- Galois field tables for Reed-Solomon (GF(256), primitive 0x11D) -------- #
_EXP = [0] * 512
_LOG = [0] * 256


def _init_tables() -> None:
    x = 1
    for i in range(255):
        _EXP[i] = x
        _LOG[x] = i
        x <<= 1
        if x & 0x100:
            x ^= 0x11D
    for i in range(255, 512):
        _EXP[i] = _EXP[i - 255]


_init_tables()


def _gf_mul(a: int, b: int) -> int:
    if a == 0 or b == 0:
        return 0
    return _EXP[_LOG[a] + _LOG[b]]


def _poly_mul(a: list[int], b: list[int]) -> list[int]:
    result = [0] * (len(a) + len(b) - 1)
    for i, av in enumerate(a):
        if av == 0:
            continue
        for j, bv in enumerate(b):
            result[i + j] ^= _gf_mul(av, bv)
    return result


def _rs_generator(degree: int) -> list[int]:
    poly = [1]
    for i in range(degree):
        poly = _poly_mul(poly, [1, _EXP[i]])
    return poly


def _rs_remainder(data: list[int], degree: int) -> list[int]:
    generator = _rs_generator(degree)
    remainder = [0] * degree
    for byte in data:
        factor = byte ^ remainder[0]
        remainder = remainder[1:] + [0]
        if factor:
            for i in range(degree):
                remainder[i] ^= _gf_mul(generator[i + 1], factor)
    return remainder


# --- Version tables (EC level M) -------------------------------------------- #
# version: (ec_per_block, [(block_count, data_per_block), ...])
_EC_M = {
    1: (10, [(1, 16)]),
    2: (16, [(1, 28)]),
    3: (26, [(1, 44)]),
    4: (18, [(2, 32)]),
    5: (24, [(2, 43)]),
    6: (16, [(4, 27)]),
    7: (18, [(4, 31)]),
    8: (22, [(2, 38), (2, 39)]),
    9: (22, [(3, 36), (2, 37)]),
    10: (26, [(4, 43), (1, 44)]),
}

_ALIGNMENT = {
    1: [],
    2: [6, 18],
    3: [6, 22],
    4: [6, 26],
    5: [6, 30],
    6: [6, 34],
    7: [6, 22, 38],
    8: [6, 24, 42],
    9: [6, 26, 46],
    10: [6, 28, 50],
}

# Format information (EC level M = 00, mask 0) -> 15-bit sequence.
_FORMAT_M_MASK0 = 0b101010000010010


def _data_codewords(version: int) -> int:
    _ec, blocks = _EC_M[version]
    return sum(count * per_block for count, per_block in blocks)


def _char_count_bits(version: int) -> int:
    return 8 if version <= 9 else 16


def _select_version(payload: bytes) -> int:
    for version in range(1, 11):
        capacity = _data_codewords(version) * 8
        needed = 4 + _char_count_bits(version) + len(payload) * 8
        if needed <= capacity:
            return version
    raise ValueError("payload too large for supported QR versions")


def _encode_data(payload: bytes, version: int) -> list[int]:
    bits: list[int] = []

    def push(value: int, length: int) -> None:
        for shift in range(length - 1, -1, -1):
            bits.append((value >> shift) & 1)

    push(0b0100, 4)  # byte mode
    push(len(payload), _char_count_bits(version))
    for byte in payload:
        push(byte, 8)

    capacity = _data_codewords(version) * 8
    push(0, min(4, capacity - len(bits)))
    while len(bits) % 8:
        bits.append(0)
    pad = 0
    while len(bits) < capacity:
        push(0xEC if pad % 2 == 0 else 0x11, 8)
        pad += 1

    return [int("".join(str(b) for b in bits[i : i + 8]), 2) for i in range(0, len(bits), 8)]


def _interleave(codewords: list[int], version: int) -> list[int]:
    ec_per_block, blocks = _EC_M[version]
    data_blocks: list[list[int]] = []
    offset = 0
    for count, per_block in blocks:
        for _ in range(count):
            data_blocks.append(codewords[offset : offset + per_block])
            offset += per_block
    ec_blocks = [_rs_remainder(block, ec_per_block) for block in data_blocks]

    result: list[int] = []
    max_data = max(len(b) for b in data_blocks)
    for i in range(max_data):
        for block in data_blocks:
            if i < len(block):
                result.append(block[i])
    for i in range(ec_per_block):
        for block in ec_blocks:
            result.append(block[i])
    return result


def _matrix_size(version: int) -> int:
    return 17 + version * 4


def _build_matrix(version: int, codewords: list[int]) -> list[list[bool]]:
    size = _matrix_size(version)
    matrix = [[False] * size for _ in range(size)]
    reserved = [[False] * size for _ in range(size)]

    def set_reserved(row: int, col: int, value: bool | None = None) -> None:
        if not (0 <= row < size and 0 <= col < size):
            return
        reserved[row][col] = True
        if value is not None:
            matrix[row][col] = value

    def place_finder(row: int, col: int) -> None:
        # 7x7 finder plus a one-module light separator on all outer sides.
        for dr in range(-1, 8):
            for dc in range(-1, 8):
                value = (
                    0 <= dr <= 6
                    and 0 <= dc <= 6
                    and (dr in (0, 6) or dc in (0, 6) or (2 <= dr <= 4 and 2 <= dc <= 4))
                )
                set_reserved(row + dr, col + dc, value)

    place_finder(0, 0)
    place_finder(0, size - 7)
    place_finder(size - 7, 0)

    # Timing patterns (row/column 6).
    for i in range(8, size - 8):
        set_reserved(i, 6, i % 2 == 0)
        set_reserved(6, i, i % 2 == 0)

    # Alignment patterns.
    centers = _ALIGNMENT[version]
    last = len(centers) - 1
    for ri, r in enumerate(centers):
        for ci, c in enumerate(centers):
            if (ri, ci) in {(0, 0), (0, last), (last, 0)}:
                continue
            for dr in range(-2, 3):
                for dc in range(-2, 3):
                    set_reserved(r + dr, c + dc, max(abs(dr), abs(dc)) != 1)

    # Format information areas (filled after data placement).
    for i in range(9):
        set_reserved(8, i)
        set_reserved(i, 8)
    for i in range(8):
        set_reserved(8, size - 1 - i)
        set_reserved(size - 1 - i, 8)
    set_reserved(size - 8, 8, True)  # dark module

    def place_data() -> None:
        bit_index = 0
        total_bits = len(codewords) * 8
        column = size - 1
        upward = True
        while column > 0:
            if column == 6:
                column -= 1
            rows = range(size - 1, -1, -1) if upward else range(size)
            for row in rows:
                for c in (column, column - 1):
                    if reserved[row][c]:
                        continue
                    bit = 0
                    if bit_index < total_bits:
                        bit = (codewords[bit_index // 8] >> (7 - bit_index % 8)) & 1
                    bit_index += 1
                    matrix[row][c] = bool(bit)
            upward = not upward
            column -= 2

    place_data()

    # Format information bits (mask 0, EC level M).
    for i in range(15):
        bit = bool((_FORMAT_M_MASK0 >> i) & 1)
        if i < 6:
            matrix[8][i] = bit
        elif i == 6:
            matrix[8][7] = bit
        elif i == 7:
            matrix[8][8] = bit
        elif i == 8:
            matrix[7][8] = bit
        else:
            matrix[14 - i][8] = bit
        if i < 8:
            matrix[size - 1 - i][8] = bit
        else:
            matrix[8][size - 15 + i] = bit
    return matrix


def build_qr_matrix(data: str) -> list[list[bool]]:
    """Return a boolean matrix (``True`` = dark module) for ``data``."""

    payload = data.encode("utf-8")
    version = _select_version(payload)
    codewords = _encode_data(payload, version)
    interleaved = _interleave(codewords, version)
    return _build_matrix(version, interleaved)


def to_svg(data: str, *, module: int = 4, border: int = 4, dark: str = "#000000") -> str:
    """Render ``data`` as an SVG string with a quiet-zone border."""

    matrix = build_qr_matrix(data)
    size = len(matrix)
    dimension = (size + 2 * border) * module
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{dimension}" height="{dimension}" '
        f'viewBox="0 0 {dimension} {dimension}" shape-rendering="crispEdges">',
        f'<rect width="{dimension}" height="{dimension}" fill="#ffffff"/>',
        f'<path fill="{dark}" d="',
    ]
    for row in range(size):
        for col in range(size):
            if matrix[row][col]:
                x = (col + border) * module
                y = (row + border) * module
                parts.append(f"M{x} {y}h{module}v{module}h-{module}z")
    parts.append('"/></svg>')
    return "".join(parts)


def to_data_uri(data: str) -> str:
    """Return an inline ``data:`` URI embedding the SVG QR code."""

    from urllib.parse import quote

    return f"data:image/svg+xml,{quote(to_svg(data), safe='')}"


def is_probably_url(value: str) -> bool:
    return bool(re.match(r"^[a-z][a-z0-9+.-]*://", value, re.IGNORECASE))
