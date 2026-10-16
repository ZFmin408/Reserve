"""自检脚本：不联网，验证核心模块能否正常工作。

用法： python tests\\selftest.py

覆盖：
  1. app.crypto 的 DER 公钥解析（PKCS#1 / PKCS#8）与 RSA PKCS#1 v1.5 加密正确性
     —— 用已知的 (n, d) 解密回来比对，确保填充和模幂都对
  2. app.config 配置装载与脱敏
  3. app.adapters 的登录体构造（模板模式 / 字段模式 / 包装层）
  4. 起一个本地假服务器，端到端跑通 login -> token 提取
"""
from __future__ import annotations

import base64
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import crypto  # noqa: E402

PASS, FAIL = [], []


def check(name: str, cond: bool, extra: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{('  ' + extra) if extra and not cond else ''}")


# ---------------- 造一对测试用 RSA 密钥 ----------------
def gen_rsa(bits: int = 1024, e: int = 65537):
    import random

    def is_prime(n: int, k: int = 24) -> bool:
        if n < 2:
            return False
        for p in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37):
            if n % p == 0:
                return n == p
        d, r = n - 1, 0
        while d % 2 == 0:
            d //= 2
            r += 1
        for _ in range(k):
            a = random.randrange(2, n - 1)
            x = pow(a, d, n)
            if x in (1, n - 1):
                continue
            for _ in range(r - 1):
                x = x * x % n
                if x == n - 1:
                    break
            else:
                return False
        return True

    def gen_prime(nbits: int) -> int:
        while True:
            c = random.getrandbits(nbits) | (1 << (nbits - 1)) | 1
            if is_prime(c):
                return c

    while True:
        p, q = gen_prime(bits // 2), gen_prime(bits // 2)
        if p == q:
            continue
        n = p * q
        if n.bit_length() != bits:
            continue
        phi = (p - 1) * (q - 1)
        if phi % e == 0:
            continue
        d = pow(e, -1, phi)
        return n, e, d


def der_len(n: int) -> bytes:
    if n < 0x80:
        return bytes([n])
    b = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(b)]) + b


def der_tlv(tag: int, body: bytes) -> bytes:
    return bytes([tag]) + der_len(len(body)) + body


def der_int(v: int) -> bytes:
    b = v.to_bytes((v.bit_length() + 7) // 8 or 1, "big")
    if b[0] & 0x80:
        b = b"\x00" + b
    return der_tlv(0x02, b)


def pkcs1_der(n: int, e: int) -> bytes:
    return der_tlv(0x30, der_int(n) + der_int(e))


def pkcs8_der(n: int, e: int) -> bytes:
    oid = bytes.fromhex("06092a864886f70d010101")          # rsaEncryption
    null = bytes.fromhex("0500")
    inner = der_tlv(0x30, der_int(n) + der_int(e))
    bitstr = der_tlv(0x03, b"\x00" + inner)
    return der_tlv(0x30, der_tlv(0x30, oid + null) + bitstr)


def main() -> int:
    print("=" * 70)
    print("  1. RSA 公钥解析 + 加密正确性")
    print("=" * 70)
    n, e, d = gen_rsa(1024)
    print(f"  测试密钥: {n.bit_length()} 位, e={e}")

    # 解析 base64 的 PKCS#1
    b64_p1 = base64.b64encode(pkcs1_der(n, e)).decode()
    parsed = crypto.parse_public_key(b64_p1)
    check("解析 base64 PKCS#1 公钥", parsed == (n, e))

    # 解析 base64 的 PKCS#8
    b64_p8 = base64.b64encode(pkcs8_der(n, e)).decode()
    check("解析 base64 PKCS#8 公钥", crypto.parse_public_key(b64_p8) == (n, e))

    # 解析 PEM
    pem = ("-----BEGIN PUBLIC KEY-----\n"
           + "\n".join(b64_p8[i:i + 64] for i in range(0, len(b64_p8), 64))
           + "\n-----END PUBLIC KEY-----")
    check("解析 PEM 公钥", crypto.parse_public_key(pem) == (n, e))

    # 加密后长度必须是密钥长度
    ct_hex = crypto.encrypt_password("MyPass123!", "rsa_hex", b64_p1)
    check("rsa_hex 输出 256 位 hex (=128字节)", len(ct_hex) == 256, f"实际 {len(ct_hex)}")

    ct_b64 = crypto.encrypt_password("MyPass123!", "rsa_b64", b64_p1)
    check("rsa_b64 输出 128 字节", len(base64.b64decode(ct_b64)) == 128,
          f"实际 {len(base64.b64decode(ct_b64))}")

    # ★ 核心验证：用私钥解密，必须还原出原文，且填充符合 PKCS#1 v1.5
    k = 128
    em = pow(int(ct_hex, 16), d, n).to_bytes(k, "big")
    check("解密后首两字节为 00 02 (PKCS#1 v1.5)", em[:2] == b"\x00\x02", f"实际 {em[:2].hex()}")
    sep = em.index(b"\x00", 2)
    ps = em[2:sep]
    msg = em[sep + 1:]
    check("PS 填充长度 >= 8", len(ps) >= 8, f"实际 {len(ps)}")
    check("PS 不含 0x00", b"\x00" not in ps)
    check("★ 解密还原明文 == MyPass123!", msg == b"MyPass123!", f"实际 {msg!r}")

    # raw 模式：密文解出后应该是 `m` 的整数值左填充到 k 字节。
    # 关键点：0x41='A' 的最高位是 0，int 化会吃掉它的前导零位，
    # 所以 to_bytes 还原后一定以 00 41 42 结尾（多一个 0x00 是正常的，
    # 这也正是 raw 模式的实际行为，不是 bug）。
    # 另注意：必须显式传 rsa_mode="raw"，默认是 pkcs1。
    ct_raw = crypto.encrypt_password("AB", "rsa_hex", b64_p1, rsa_mode="raw")
    check("raw 模式输出满长度 (128 字节)", len(bytes.fromhex(ct_raw)) == k,
          f"实际 {len(bytes.fromhex(ct_raw))}")
    em2 = pow(int(ct_raw, 16), d, n).to_bytes(k, "big")
    check("raw 模式解密尾部为 00 41 42（无 PKCS#1 头）",
          em2.endswith(b"\x00AB") and not em2.startswith(b"\x00\x02"),
          f"实际尾部 {em2.hex()[-16:]}")
    # 顺带验证默认没传 rsa_mode 时走的是 PKCS#1
    ct_def = crypto.encrypt_password("AB", "rsa_hex", b64_p1)
    em3 = pow(int(ct_def, 16), d, n).to_bytes(k, "big")
    check("默认 rsa_mode=pkcs1", em3.startswith(b"\x00\x02"))

    # 哈希模式
    check("md5 正确", crypto.hash_password("abc", "md5") == "900150983cd24fb0d6963f7d28e17f72")
    check("sha256 正确", crypto.hash_password("abc", "sha256").startswith("ba7816bf8f01cfea"))
    check("plain 原样返回", crypto.hash_password("abc", "plain") == "abc")

    # 公钥缺失要报错而不是静默发错
    try:
        crypto.encrypt_password("x", "rsa_b64", "")
        check("无公钥时抛异常", False)
    except ValueError:
        check("无公钥时抛异常", True)

    print()
    print("=" * 70)
    print("  1.5 AES-128（下级信加密用）")
    print("=" * 70)
    from app import aes
    # FIPS-197 官方测试向量
    rks = aes._expand_key(bytes.fromhex("000102030405060708090a0b0c0d0e0f"))
    got = aes.encrypt_block(bytes.fromhex("00112233445566778899aabbccddeeff"), rks)
    check("AES-128 加密符合 FIPS-197",
          got.hex() == "69c4e0d86a7b0430d8cdb78070b4c55a", got.hex())
    back = aes.decrypt_block(bytes.fromhex("69c4e0d86a7b0430d8cdb78070b4c55a"), rks)
    check("AES-128 解密回原文", back.hex() == "00112233445566778899aabbccddeeff", back.hex())
    rks2 = aes._expand_key(bytes.fromhex("2b7e151628aed2a6abf7158809cf4f3c"))
    got2 = aes.encrypt_block(bytes.fromhex("6bc1bee22e409f96e93d7e117393172a"), rks2)
    check("AES-128 第二组向量", got2.hex() == "3ad77bb40d7a3660a89ecaf32466ef97", got2.hex())

    # devId 是 9 位数字 -> 一块 16 字节 -> 32 位 hex，正好匹配抓包
    ct = aes.encrypt_ecb("100492994", "Uzea1Lx1fgrHUVm8")
    check("devId(9位) 加密输出 32 位 hex", len(ct) == 32 and ct.isupper(), ct)
    # 解密可还原
    pt = aes.decrypt_ecb(ct, "Uzea1Lx1fgrHUVm8")
    check("AES 解密还原 devId", aes.unpad_pkcs7(pt).decode() == "100492994", repr(pt))
    # ★ 填充选型的关键证据：抓包里 resvDevEncrypts[0] 只有 32 位 hex = 1 个 AES 块。
    #   PKCS7 对「正好 16 字节」的明文会再补一整块 -> 输出 2 块(64位hex)，与抓包不符；
    #   ZeroPadding 对 <=16 字节只输出 1 块 -> 与抓包一致。
    #   所以 devId 必须 <= 16 字节，且 ZeroPadding 更可能是真的实现。
    check("PKCS7 对 16 字节明文会膨胀成 2 块",
          len(aes.encrypt_ecb("A" * 16, "Uzea1Lx1fgrHUVm8", "pkcs7")) == 64)
    check("Zero 对 16 字节明文仍是 1 块（与抓包 32 位 hex 吻合）",
          len(aes.encrypt_ecb("A" * 16, "Uzea1Lx1fgrHUVm8", "zero")) == 32)
    check("9 字节明文两种填充都只有 1 块",
          len(aes.encrypt_ecb("100492994", "Uzea1Lx1fgrHUVm8", "pkcs7")) == 32
          and len(aes.encrypt_ecb("100492994", "Uzea1Lx1fgrHUVm8", "zero")) == 32)
    # ★ 真正的实现：JS chunk-common 模块 fa7d 的 b()
    #   IV = Utf8("ABCDEF1234123412"), mode=CBC, padding=ZeroPadding, 输出大写 hex
    from app import aes as _aes
    ct2 = _aes.encrypt_cbc("100493961", "Uzea1Lx1fgrHUVm8", "ABCDEF1234123412", pad="zero")
    check("CBC/Zero 对 9 字节 devId 输出 32 位大写 hex", len(ct2) == 32 and ct2.isupper(), ct2)
    check("CBC/Zero 对 16 字节明文仍是 1 块（与抓包一致）",
          len(_aes.encrypt_cbc("A" * 16, "Uzea1Lx1fgrHUVm8", "ABCDEF1234123412", pad="zero")) == 32)
    # CBC 解密可还原
    pt2 = _aes.decrypt_cbc(ct2, "Uzea1Lx1fgrHUVm8", "ABCDEF1234123412")
    check("CBC 解密还原 devId", pt2.rstrip(b"\x00").decode() == "100493961", repr(pt2))
    check("random_key 长度与字符集",
          len(aes.random_key_secure(16)) == 16
          and all(c.isalnum() for c in aes.random_key_secure(16)))

    print()
    print("=" * 70)
    print("  2. 配置模块")
    print("=" * 70)
    from app import config as cfgmod

    cfg = cfgmod.load()
    check("默认配置能装载", isinstance(cfg, dict) and "login" in cfg)
    check("login.pwd_mode 默认 auto_pubkey", cfg["login"]["pwd_mode"] == "auto_pubkey")
    check("extra_fields 含 consoleType=16", cfg["login"]["extra_fields"].get("consoleType") == 16)
    check("fields.login_user == logonName", cfg["fields"].get("login_user") == "logonName")
    check("booking.aes_mode 默认 cbc", cfg["booking"]["aes_mode"] == "cbc")
    check("booking.aes_padding 默认 zero", cfg["booking"]["aes_padding"] == "zero")
    check("booking.aes_iv 与 JS 一致", cfg["booking"]["aes_iv"] == "ABCDEF1234123412")
    check("booking.sys_kind 默认 8", cfg["booking"]["sys_kind"] == 8)
    r = cfgmod.redact({**cfg, "account": {"username": "u", "password": "secret"}})
    check("脱敏隐藏密码", r["account"]["password"] == "********")

    print()
    print("=" * 70)
    print("  3. 适配器登录体构造（不发网络请求，只看构造结果）")
    print("=" * 70)
    from app.adapters import IcAdapter
    from app.httpclient import Client

    def fresh_cfg():
        """隔离用：清掉会话缓存/已存 token，保证走真实构造路径而不是复用会话。"""
        IcAdapter._session_cache = None
        c = json.loads(json.dumps(cfg))
        c["session"] = {"auto_login": True, "cookie": "", "token": ""}
        return c

    captured = {}

    class FakeClient(Client):
        def request(self, method, url, body=None, headers=None, json_body=None):
            captured["method"] = method
            captured["url"] = url
            captured["headers"] = headers or {}
            captured["json_body"] = json_body
            captured["body"] = body
            return __import__("app.httpclient", fromlist=["Response"]).Response(
                200, {}, b'{"code":200,"data":{"token":"T0KEN","userId":42,"libId":7}}', 5.0)

    # 模板模式
    c1 = fresh_cfg()
    c1["account"] = {"username": "202301050786", "password": "pw"}
    c1["login"] = dict(cfg["login"])
    c1["login"]["body"] = '{"logonName":"{{username}}","password":"{{password_enc}}","captcha":"","consoleType":16,"privacy":true}'
    c1["login"]["pwd_mode"] = "md5"
    c1["login"]["rsa_public_key"] = ""
    ad = IcAdapter(c1, FakeClient(), lambda *a, **k: None)
    ok, msg, detail = ad.login(force=True)
    sent = captured.get("body", b"").decode()
    check("模板模式登录成功", ok, msg)
    check("模板替换 username", '"logonName":"202301050786"' in sent, sent[:120])
    check("模板替换 password_enc 为 md5", '"password":"' + crypto.hash_password("pw", "md5") + '"' in sent)
    check("token 已提取", detail.get("token") == "T0KEN")
    check("userId 已提取", detail.get("user_id") == 42)
    check("libId 已提取", detail.get("lib_id") == 7)

    # 字段模式 + extra_fields
    c2 = fresh_cfg()
    c2["account"] = {"username": "202301050786", "password": "pw"}
    c2["login"]["pwd_mode"] = "plain"
    c2["login"]["body"] = ""
    ad2 = IcAdapter(c2, FakeClient(), lambda *a, **k: None)
    ad2.login(force=True)
    jb = captured.get("json_body") or {}
    check("字段模式字段名 logonName", "logonName" in jb, json.dumps(jb, ensure_ascii=False)[:150])
    check("字段模式字段名 password", "password" in jb)
    check("extra_fields 已并入", jb.get("consoleType") == 16 and jb.get("privacy") is True)
    check("captcha 已并入", jb.get("captcha") == "")
    check("Origin 头已带", "Origin" in captured.get("headers", {}))

    print()
    print("=" * 70)
    print("  4. 端到端：本地假服务器")
    print("=" * 70)

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            ln = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(ln)
            self.server.got = {"path": self.path, "body": raw.decode("utf-8"),
                               "origin": self.headers.get("Origin"),
                               "lan": self.headers.get("lan"),
                               "token": self.headers.get("token")}
            out = json.dumps({"code": 200, "message": "ok",
                              "data": {"token": "REALTOKEN", "userId": 99}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json;charset=UTF-8")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    srv.daemon_threads = True
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    c3 = fresh_cfg()
    c3["base_url"] = f"http://127.0.0.1:{port}"
    c3["account"] = {"username": "202301050786", "password": "pw"}
    c3["login"]["pwd_mode"] = "md5"
    c3["login"]["body"] = ""
    cli = Client(timeout=5)
    ad3 = IcAdapter(c3, cli, lambda *a, **k: None)
    ok3, msg3, d3 = ad3.login(force=True)
    got = getattr(srv, "got", {})
    check("端到端登录成功", ok3, msg3)
    check("服务器收到正确路径", got.get("path") == "/ic-web/login/user", str(got.get("path")))
    gotj = json.loads(got.get("body") or "{}")
    check("服务器收到 logonName", gotj.get("logonName") == "202301050786")
    check("服务器收到 md5 密码", gotj.get("password") == crypto.hash_password("pw", "md5"))
    check("服务器收到 lan: 1", got.get("lan") == "1")
    check("token 回填到后续请求头", cli.extra_headers.get("token") == "REALTOKEN")
    check("端到端 token 提取", d3.get("token") == "REALTOKEN")
    srv.shutdown()

    print()
    print("=" * 70)
    print(f"  结果: {len(PASS)} 通过, {len(FAIL)} 失败")
    if FAIL:
        for f in FAIL:
            print("   FAIL:", f)
    print("=" * 70)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
