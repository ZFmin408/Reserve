"""密码预处理与 RSA 加密（纯标准库，无第三方依赖）。

为什么需要这个：
从抓包看，登录请求体长度 259 字节，而纯明文账号密码只有 ~50 字节。
倒推结构 `{"param":{"logName":"<12位>","pwd":"<128位hex>"}}` 正好 259 字节
-> 密码是 128 位 hex = 64 字节 = RSA-512 加密结果，前端做了 RSA。

本模块提供：
  pwd_mode = plain / md5 / sha1 / sha256 / md5_upper / base64 /
             rsa_hex / rsa_b64 / rsa_upper
  RSA 公钥从 JS 里抓（setPublicKey 里那串 base64），填进 config.login.rsa_public_key

关于 RSA 的两种常见打包方式：
  PKCS#1 v1.5  —— 标准。用同余定理构造 EM = 0x00 || 0x02 || PS || 0x00 || M 再 modpow。
  JSEncrypt    —— 也是 v1.5。
  裸加密        —— 直接把消息当整数 modpow（部分自研前端这么干）。
本模块两种都试：默认 PKCS#1 v1.5，失败时用 raw 模式，由调用方按返回码判断。
"""
from __future__ import annotations

import base64
import hashlib
import os
import re
from typing import Optional

# ---------------- 哈希 ----------------
def _hex(b: bytes, upper: bool = False) -> str:
    s = b.hex()
    return s.upper() if upper else s


def hash_password(pwd: str, mode: str) -> str:
    m = (mode or "plain").lower()
    raw = pwd.encode("utf-8")
    if m in ("plain", "none", ""):
        return pwd
    if m == "md5":
        return _hex(hashlib.md5(raw).digest())
    if m == "md5_upper":
        return _hex(hashlib.md5(raw).digest(), True)
    if m == "sha1":
        return _hex(hashlib.sha1(raw).digest())
    if m == "sha256":
        return _hex(hashlib.sha256(raw).digest())
    if m == "sha256_upper":
        return _hex(hashlib.sha256(raw).digest(), True)
    if m == "base64":
        return base64.b64encode(raw).decode()
    return pwd


# ---------------- RSA 公钥解析 ----------------
def _b64_to_int(s: str) -> int:
    s = re.sub(r"\s+", "", s)
    s = s.replace("-", "+").replace("_", "/")
    pad = (-len(s)) % 4
    return int.from_bytes(base64.b64decode(s + "=" * pad), "big")


def parse_public_key(key: str) -> Optional[tuple]:
    """支持 PEM(PKCS#1/PKCS#8) 或纯 base64 的 DER。返回 (n, e)。"""
    key = (key or "").strip()
    if not key:
        return None
    if "-----BEGIN" in key:
        b64 = re.sub(r"-----[^-]+-----", "", key)
        try:
            der = base64.b64decode(re.sub(r"\s+", "", b64))
        except Exception:
            return None
    else:
        try:
            der = base64.b64decode(re.sub(r"\s+", "", key))
        except Exception:
            return None
    return _parse_der(der)


def _read_tlv(buf: bytes, i: int):
    """读取一个 TLV，返回 ((tag, value), next_offset)。"""
    if i >= len(buf):
        return None, i
    tag = buf[i]
    i += 1
    if i >= len(buf):
        return None, i
    ln = buf[i]
    i += 1
    if ln & 0x80:
        n = ln & 0x7F
        ln = int.from_bytes(buf[i:i + n], "big")
        i += n
    return (tag, buf[i:i + ln]), i + ln


def _collect_ints(body: bytes, want: int = 2) -> list:
    ints = []
    i = 0
    while i < len(body) and len(ints) < want:
        tlv, i = _read_tlv(body, i)
        if not tlv:
            break
        if tlv[0] == 0x02:
            ints.append(int.from_bytes(tlv[1], "big"))
    return ints


def _parse_der(der: bytes) -> Optional[tuple]:
    try:
        top, _ = _read_tlv(der, 0)
        if not top:
            return None
        body = top[1]

        # PKCS#1: SEQUENCE { INTEGER n, INTEGER e }
        ints = _collect_ints(body)
        if len(ints) == 2:
            return ints[0], ints[1]

        # PKCS#8: SEQUENCE { SEQUENCE { OID, NULL }, BIT STRING { SEQUENCE { n, e } } }
        i = 0
        while i < len(body):
            tlv, i = _read_tlv(body, i)
            if not tlv:
                break
            if tlv[0] == 0x03:            # BIT STRING
                sub = tlv[1][1:]          # 去掉 unused-bits 字节
                nested, _ = _read_tlv(sub, 0)
                if nested:
                    ints = _collect_ints(nested[1])
                    if len(ints) == 2:
                        return ints[0], ints[1]
    except Exception:
        pass
    return None


# ---------------- RSA 加密 ----------------
def rsa_encrypt(msg: str, n: int, e: int, mode: str = "pkcs1", block_bytes: Optional[int] = None) -> bytes:
    m = msg.encode("utf-8")
    k = block_bytes or ((n.bit_length() + 7) // 8)
    if mode == "pkcs1":
        ps_len = k - 3 - len(m)
        if ps_len < 8:
            raise ValueError(f"RSA 块太小: k={k} msg={len(m)}")
        ps = bytearray()
        while len(ps) < ps_len:
            b = os.urandom(1)[0]
            if b != 0:
                ps.append(b)
        em = b"\x00\x02" + bytes(ps) + b"\x00" + m
    else:
        # raw（自研前端常见）：直接对消息整数做模幂，结果左填充 0 到密钥长度。
        # 少这一步密文会短于 128/256 字节，服务端解码必然出错。
        if len(m) > k:
            raise ValueError(f"raw 模式消息超长: {len(m)} > {k}")
        em = m
    c = pow(int.from_bytes(em, "big"), e, n)
    return c.to_bytes(k, "big")


# 常见前端会写死的小公钥（部分高校系统用它，n 只有 64 字节 = 512 位）
KNOWN_KEYS = {}


def encrypt_password(pwd: str, mode: str, public_key: str = "", rsa_mode: str = "pkcs1") -> str:
    """按 mode 产出要放进请求体的密码字符串。mode 见模块 docstring。"""
    m = (mode or "plain").lower()
    if not m.startswith("rsa"):
        return hash_password(pwd, m)

    parsed = parse_public_key(public_key)
    if not parsed:
        raise ValueError(
            "RSA 公钥无法解析。请把前端 JS 里 setPublicKey(...) 那串公钥填进 "
            "config.login.rsa_public_key（base64 或 PEM 都行）。"
        )
    n, e = parsed
    cipher = rsa_encrypt(pwd, n, e, rsa_mode)
    if m == "rsa_b64":
        return base64.b64encode(cipher).decode()
    if m == "rsa_upper":
        return cipher.hex().upper()
    return cipher.hex()
