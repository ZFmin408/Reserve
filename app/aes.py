"""纯标准库 AES-128 / ECB / CBC 实现 + PKCS7 / ZeroPadding。

为什么自己写：整个项目坚持零依赖（不引入 pycryptodome）。
这套 IC 系统的下单 payload 里有 `resvDevEncrypts`，实测是
    AES( 随机16字符口令, devId ) -> 大写 hex
`encryptVlaue` 则是 RSA( 同一个16字符口令 ) -> base64（信封加密）。

本模块提供：
  encrypt_ecb_pkcs7 / encrypt_ecb_zero / encrypt_cbc_pkcs7 / encrypt_cbc_zero
  decrypt_*            对应解密（用于验证）
  random_key(n)        生成 n 位随机口令（对应前端 Object(d["e"])(16)）
"""

from __future__ import annotations

import os
import random
import string

# ---------------- S-box / 逆 S-box ----------------
_SBOX = [
    0x63, 0x7C, 0x77, 0x7B, 0xF2, 0x6B, 0x6F, 0xC5, 0x30, 0x01, 0x67, 0x2B, 0xFE, 0xD7, 0xAB, 0x76,
    0xCA, 0x82, 0xC9, 0x7D, 0xFA, 0x59, 0x47, 0xF0, 0xAD, 0xD4, 0xA2, 0xAF, 0x9C, 0xA4, 0x72, 0xC0,
    0xB7, 0xFD, 0x93, 0x26, 0x36, 0x3F, 0xF7, 0xCC, 0x34, 0xA5, 0xE5, 0xF1, 0x71, 0xD8, 0x31, 0x15,
    0x04, 0xC7, 0x23, 0xC3, 0x18, 0x96, 0x05, 0x9A, 0x07, 0x12, 0x80, 0xE2, 0xEB, 0x27, 0xB2, 0x75,
    0x09, 0x83, 0x2C, 0x1A, 0x1B, 0x6E, 0x5A, 0xA0, 0x52, 0x3B, 0xD6, 0xB3, 0x29, 0xE3, 0x2F, 0x84,
    0x53, 0xD1, 0x00, 0xED, 0x20, 0xFC, 0xB1, 0x5B, 0x6A, 0xCB, 0xBE, 0x39, 0x4A, 0x4C, 0x58, 0xCF,
    0xD0, 0xEF, 0xAA, 0xFB, 0x43, 0x4D, 0x33, 0x85, 0x45, 0xF9, 0x02, 0x7F, 0x50, 0x3C, 0x9F, 0xA8,
    0x51, 0xA3, 0x40, 0x8F, 0x92, 0x9D, 0x38, 0xF5, 0xBC, 0xB6, 0xDA, 0x21, 0x10, 0xFF, 0xF3, 0xD2,
    0xCD, 0x0C, 0x13, 0xEC, 0x5F, 0x97, 0x44, 0x17, 0xC4, 0xA7, 0x7E, 0x3D, 0x64, 0x5D, 0x19, 0x73,
    0x60, 0x81, 0x4F, 0xDC, 0x22, 0x2A, 0x90, 0x88, 0x46, 0xEE, 0xB8, 0x14, 0xDE, 0x5E, 0x0B, 0xDB,
    0xE0, 0x32, 0x3A, 0x0A, 0x49, 0x06, 0x24, 0x5C, 0xC2, 0xD3, 0xAC, 0x62, 0x91, 0x95, 0xE4, 0x79,
    0xE7, 0xC8, 0x37, 0x6D, 0x8D, 0xD5, 0x4E, 0xA9, 0x6C, 0x56, 0xF4, 0xEA, 0x65, 0x7A, 0xAE, 0x08,
    0xBA, 0x78, 0x25, 0x2E, 0x1C, 0xA6, 0xB4, 0xC6, 0xE8, 0xDD, 0x74, 0x1F, 0x4B, 0xBD, 0x8B, 0x8A,
    0x70, 0x3E, 0xB5, 0x66, 0x48, 0x03, 0xF6, 0x0E, 0x61, 0x35, 0x57, 0xB9, 0x86, 0xC1, 0x1D, 0x9E,
    0xE1, 0xF8, 0x98, 0x11, 0x69, 0xD9, 0x8E, 0x94, 0x9B, 0x1E, 0x87, 0xE9, 0xCE, 0x55, 0x28, 0xDF,
    0x8C, 0xA1, 0x89, 0x0D, 0xBF, 0xE6, 0x42, 0x68, 0x41, 0x99, 0x2D, 0x0F, 0xB0, 0x54, 0xBB, 0x16,
]

_INV_SBOX = [0] * 256
for _i, _v in enumerate(_SBOX):
    _INV_SBOX[_v] = _i

_RCON = [0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1B, 0x36]


def _xtime(a: int) -> int:
    a <<= 1
    return (a ^ 0x1B) & 0xFF if a & 0x100 else a


def _mul(a: int, b: int) -> int:
    r = 0
    for _ in range(8):
        if b & 1:
            r ^= a
        b >>= 1
        a = _xtime(a)
    return r


def _expand_key(key: bytes) -> list:
    """AES-128 密钥扩展，返回 11 个 16 字节轮密钥。"""
    if len(key) != 16:
        raise ValueError(f"AES-128 需要 16 字节密钥，收到 {len(key)}")
    w = [list(key[i * 4:i * 4 + 4]) for i in range(4)]
    for i in range(4, 44):
        t = list(w[i - 1])
        if i % 4 == 0:
            t = t[1:] + t[:1]                      # RotWord
            t = [_SBOX[b] for b in t]              # SubWord
            t[0] ^= _RCON[i // 4 - 1]
        w.append([w[i - 4][j] ^ t[j] for j in range(4)])
    # w 是 44 个 4 字节字；每 4 个连续字拼成 16 字节轮密钥
    return [bytes(b for c in range(4) for b in w[r * 4 + c]) for r in range(11)]


def _add_round_key(state: list, rk: bytes) -> None:
    for i in range(16):
        state[i] ^= rk[i]


def _sub_bytes(state: list, box: list) -> None:
    for i in range(16):
        state[i] = box[state[i]]


def _shift_rows(state: list) -> None:
    # state 按列优先：state[c*4+r]
    for r in range(1, 4):
        row = [state[c * 4 + r] for c in range(4)]
        row = row[r:] + row[:r]
        for c in range(4):
            state[c * 4 + r] = row[c]


def _inv_shift_rows(state: list) -> None:
    for r in range(1, 4):
        row = [state[c * 4 + r] for c in range(4)]
        row = row[-r:] + row[:-r]
        for c in range(4):
            state[c * 4 + r] = row[c]


def _mix_columns(state: list) -> None:
    for c in range(4):
        a = state[c * 4:c * 4 + 4]
        state[c * 4 + 0] = _mul(a[0], 2) ^ _mul(a[1], 3) ^ a[2] ^ a[3]
        state[c * 4 + 1] = a[0] ^ _mul(a[1], 2) ^ _mul(a[2], 3) ^ a[3]
        state[c * 4 + 2] = a[0] ^ a[1] ^ _mul(a[2], 2) ^ _mul(a[3], 3)
        state[c * 4 + 3] = _mul(a[0], 3) ^ a[1] ^ a[2] ^ _mul(a[3], 2)


def _inv_mix_columns(state: list) -> None:
    for c in range(4):
        a = state[c * 4:c * 4 + 4]
        state[c * 4 + 0] = _mul(a[0], 14) ^ _mul(a[1], 11) ^ _mul(a[2], 13) ^ _mul(a[3], 9)
        state[c * 4 + 1] = _mul(a[0], 9) ^ _mul(a[1], 14) ^ _mul(a[2], 11) ^ _mul(a[3], 13)
        state[c * 4 + 2] = _mul(a[0], 13) ^ _mul(a[1], 9) ^ _mul(a[2], 14) ^ _mul(a[3], 11)
        state[c * 4 + 3] = _mul(a[0], 11) ^ _mul(a[1], 13) ^ _mul(a[2], 9) ^ _mul(a[3], 14)


def encrypt_block(block: bytes, rks: list) -> bytes:
    state = list(block)
    _add_round_key(state, rks[0])
    for r in range(1, 10):
        _sub_bytes(state, _SBOX)
        _shift_rows(state)
        _mix_columns(state)
        _add_round_key(state, rks[r])
    _sub_bytes(state, _SBOX)
    _shift_rows(state)
    _add_round_key(state, rks[10])
    return bytes(state)


def decrypt_block(block: bytes, rks: list) -> bytes:
    state = list(block)
    _add_round_key(state, rks[10])
    for r in range(9, 0, -1):
        _inv_shift_rows(state)
        _sub_bytes(state, _INV_SBOX)
        _add_round_key(state, rks[r])
        _inv_mix_columns(state)
    _inv_shift_rows(state)
    _sub_bytes(state, _INV_SBOX)
    _add_round_key(state, rks[0])
    return bytes(state)


# ---------------- 填充 ----------------
def pad_pkcs7(data: bytes) -> bytes:
    n = 16 - (len(data) % 16)
    return data + bytes([n]) * n


def unpad_pkcs7(data: bytes) -> bytes:
    if not data:
        raise ValueError("空数据")
    n = data[-1]
    if n < 1 or n > 16 or data[-n:] != bytes([n]) * n:
        raise ValueError("PKCS7 填充非法")
    return data[:-n]


def pad_zero(data: bytes) -> bytes:
    if len(data) % 16 == 0:
        return data
    return data + b"\x00" * (16 - len(data) % 16)


def unpad_zero(data: bytes) -> bytes:
    return data.rstrip(b"\x00")


# ---------------- 对外接口 ----------------
def _key_bytes(key) -> bytes:
    if isinstance(key, bytes):
        return key
    return str(key).encode("utf-8")


def encrypt_ecb(data, key, pad: str = "pkcs7", upper: bool = True) -> str:
    kb = _key_bytes(key)
    rks = _expand_key(kb)
    raw = pad_pkcs7(data.encode()) if pad == "pkcs7" else pad_zero(data.encode() if isinstance(data, str) else data)
    out = b"".join(encrypt_block(raw[i:i + 16], rks) for i in range(0, len(raw), 16))
    h = out.hex()
    return h.upper() if upper else h


def decrypt_ecb(cipher_hex: str, key) -> bytes:
    kb = _key_bytes(key)
    rks = _expand_key(kb)
    raw = bytes.fromhex(cipher_hex)
    return b"".join(decrypt_block(raw[i:i + 16], rks) for i in range(0, len(raw), 16))


def encrypt_cbc(data, key, iv, pad: str = "pkcs7", upper: bool = True) -> str:
    kb = _key_bytes(key)
    ib = _key_bytes(iv)
    rks = _expand_key(kb)
    src = data.encode() if isinstance(data, str) else data
    raw = pad_pkcs7(src) if pad == "pkcs7" else pad_zero(src)
    prev = ib
    out = b""
    for i in range(0, len(raw), 16):
        blk = bytes(a ^ b for a, b in zip(raw[i:i + 16], prev))
        prev = encrypt_block(blk, rks)
        out += prev
    h = out.hex()
    return h.upper() if upper else h


def decrypt_cbc(cipher_hex: str, key, iv) -> bytes:
    kb = _key_bytes(key)
    ib = _key_bytes(iv)
    rks = _expand_key(kb)
    raw = bytes.fromhex(cipher_hex)
    prev = ib
    out = b""
    for i in range(0, len(raw), 16):
        blk = raw[i:i + 16]
        out += bytes(a ^ b for a, b in zip(decrypt_block(blk, rks), prev))
        prev = blk
    return out


_KEY_CHARS = string.ascii_letters + string.digits


def random_key(n: int = 16, chars: str = _KEY_CHARS) -> str:
    """对应前端 Object(d["e"])(16)：从 [A-Za-z0-9] 取 n 位随机串。"""
    return "".join(random.choice(chars) for _ in range(n))


def random_key_secure(n: int = 16) -> str:
    return "".join(_KEY_CHARS[b % len(_KEY_CHARS)] for b in os.urandom(n))
