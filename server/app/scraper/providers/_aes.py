"""Minimal pure-Python AES-128-CBC decryption (no extra dependency).

Only what a provider needs to read a small encrypted player API answer (a few kB): ``cbc_decrypt(key, iv, data)``
with PKCS#7 padding removed. Not constant time and not fast; never use it for secrets of our own.
"""
from __future__ import annotations


def _xtime(a: int) -> int:
    a <<= 1
    return (a ^ 0x11B) & 0xFF if a & 0x100 else a


def _mul(a: int, b: int) -> int:
    out = 0
    while b:
        if b & 1:
            out ^= a
        a = _xtime(a)
        b >>= 1
    return out


def _sbox() -> list:
    sbox = [0] * 256
    p = q = 1
    sbox[0] = 0x63
    while True:
        p = p ^ ((p << 1) & 0xFF) ^ (0x1B if p & 0x80 else 0)          # p * 3
        q ^= q << 1
        q ^= q << 2
        q ^= q << 4
        q &= 0xFF
        if q & 0x80:
            q ^= 0x09                                                    # q / 3
        x = q ^ ((q << 1) | (q >> 7)) ^ ((q << 2) | (q >> 6)) ^ ((q << 3) | (q >> 5)) ^ ((q << 4) | (q >> 4))
        sbox[p] = (x ^ 0x63) & 0xFF
        if p == 1:
            break
    return sbox


SBOX = _sbox()
INV_SBOX = [0] * 256
for _i, _v in enumerate(SBOX):
    INV_SBOX[_v] = _i
_RCON = (0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1B, 0x36)


def _expand(key: bytes) -> list:
    if len(key) != 16:
        raise ValueError("AES-128 key must be 16 bytes")
    words = [list(key[i:i + 4]) for i in range(0, 16, 4)]
    for i in range(4, 44):
        t = list(words[i - 1])
        if i % 4 == 0:
            t = t[1:] + t[:1]
            t = [SBOX[b] for b in t]
            t[0] ^= _RCON[i // 4 - 1]
        words.append([a ^ b for a, b in zip(words[i - 4], t)])
    return [sum(words[r * 4:r * 4 + 4], []) for r in range(11)]   # 11 round keys of 16 bytes (column-major state)


def _decrypt_block(block: bytes, rounds: list) -> bytes:
    s = [b ^ k for b, k in zip(block, rounds[10])]
    for r in range(9, -1, -1):
        # inverse ShiftRows (state is column-major: s[c*4 + row])
        s = [s[((c - row) % 4) * 4 + row] for c in range(4) for row in range(4)]
        s = [INV_SBOX[b] for b in s]
        s = [b ^ k for b, k in zip(s, rounds[r])]
        if r:
            out = []
            for c in range(4):
                a0, a1, a2, a3 = s[c * 4:c * 4 + 4]
                out += [_mul(a0, 14) ^ _mul(a1, 11) ^ _mul(a2, 13) ^ _mul(a3, 9),
                        _mul(a0, 9) ^ _mul(a1, 14) ^ _mul(a2, 11) ^ _mul(a3, 13),
                        _mul(a0, 13) ^ _mul(a1, 9) ^ _mul(a2, 14) ^ _mul(a3, 11),
                        _mul(a0, 11) ^ _mul(a1, 13) ^ _mul(a2, 9) ^ _mul(a3, 14)]
            s = out
    return bytes(s)


def cbc_decrypt(key: bytes, iv: bytes, data: bytes) -> bytes:
    """AES-128-CBC plaintext of ``data`` (PKCS#7 padding removed). ``ValueError`` on a bad length or padding."""
    if len(iv) != 16 or not data or len(data) % 16:
        raise ValueError("ciphertext/iv length is not a multiple of the AES block size")
    rounds = _expand(key)
    out = bytearray()
    prev = iv
    for i in range(0, len(data), 16):
        block = data[i:i + 16]
        out += bytes(a ^ b for a, b in zip(_decrypt_block(block, rounds), prev))
        prev = block
    pad = out[-1]
    if not 1 <= pad <= 16 or out[-pad:] != bytes([pad]) * pad:
        raise ValueError("bad padding")
    return bytes(out[:-pad])
