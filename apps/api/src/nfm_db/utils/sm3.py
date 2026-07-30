"""Pure-Python SM3 hash (GB/T 32905-2016).

Implements the Chinese national standard cryptographic hash
without external dependencies.
"""

from __future__ import annotations

import struct

_IV = (
    0x7380166F, 0x4914B2B9, 0x172442D7, 0xDA8A0600,
    0xA96F30BC, 0x163138AA, 0xE38DEE4D, 0xB0FB0E4E,
)

_TJ_0_15 = 0x79CC4519
_TJ_16_63 = 0x7A879D8A


def _rotl(x: int, n: int) -> int:
    n &= 0x1F
    return ((x << n) | (x >> (32 - n))) & 0xFFFFFFFF


def _p0(x: int) -> int:
    return (x ^ _rotl(x, 9) ^ _rotl(x, 17)) & 0xFFFFFFFF


def _p1(x: int) -> int:
    return (x ^ _rotl(x, 15) ^ _rotl(x, 23)) & 0xFFFFFFFF


def _ff(j: int, x: int, y: int, z: int) -> int:
    if j < 16:
        return x ^ y ^ z
    return (x & y) | (x & z) | (y & z)


def _gg(j: int, x: int, y: int, z: int) -> int:
    if j < 16:
        return x ^ y ^ z
    return (x & y) | (((~x) & 0xFFFFFFFF) & z)


def _tj(j: int) -> int:
    return _TJ_0_15 if j < 16 else _TJ_16_63


def sm3(message: bytes) -> str:
    """Compute SM3 hash and return 64-char lowercase hex string.

    Reference test vectors:
        SM3("abc") = 66c7f0f462eeedd9d1f2d46bdc10e4e24167c4875cf2f7a2297da02b8f4ba8e0
    """
    msg = bytearray(message)
    length_bits = len(msg) * 8
    msg.append(0x80)
    while len(msg) % 64 != 56:
        msg.append(0x00)
    msg += struct.pack(">Q", length_bits)

    v = list(_IV)

    for offset in range(0, len(msg), 64):
        block = msg[offset : offset + 64]

        # Message expansion: W[0..67], W'[0..63]
        w = [0] * 68
        for i in range(16):
            w[i] = struct.unpack(">I", bytes(block[i * 4 : (i + 1) * 4]))[0]
        for i in range(16, 68):
            w[i] = (
                _p1(w[i - 16] ^ w[i - 9] ^ _rotl(w[i - 3], 15))
                ^ _rotl(w[i - 13], 7)
                ^ w[i - 6]
            ) & 0xFFFFFFFF

        wp = [(w[i] ^ w[i + 4]) & 0xFFFFFFFF for i in range(64)]

        # Compression
        a, b, c, d, e, f, g, h = v

        for j in range(64):
            tj = _tj(j)
            ss1 = (_rotl((_rotl(a, 12) + e + _rotl(tj, j % 32)) & 0xFFFFFFFF, 7)) & 0xFFFFFFFF
            ss2 = ss1 ^ _rotl(a, 12)
            tt1 = (_ff(j, a, b, c) + d + ss2 + wp[j]) & 0xFFFFFFFF
            tt2 = (_gg(j, e, f, g) + h + ss1 + w[j]) & 0xFFFFFFFF

            d = c
            c = _rotl(b, 9)
            b = a
            a = tt1
            h = g
            g = _rotl(f, 19)
            f = e
            e = _p0(tt2)

        v[0] ^= a
        v[1] ^= b
        v[2] ^= c
        v[3] ^= d
        v[4] ^= e
        v[5] ^= f
        v[6] ^= g
        v[7] ^= h
        v = [x & 0xFFFFFFFF for x in v]

    return "".join(f"{x:08x}" for x in v)


__all__ = ["sm3"]
