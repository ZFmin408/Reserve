"""离线校验 resvDevEncrypts 的加密实现，并做已知明文对撞（纯标准库）。

从 chunk-common 模块 fa7d 反出的真实实现：
    IV   = Utf8("ABCDEF1234123412")
    KEY  = Utf8(a)                // a = 16 位随机口令 [A-Za-z0-9]
    ct   = AES-CBC(devId, KEY, IV, ZeroPadding).ciphertext -> hex -> 大写
    a 本身再用 RSA 加密 -> encryptVlaue (base64)

本工具：
  1) 自校验：用抓包得到的 (明文, 密文) 对撞，确认实现正确
  2) 若无明文，则用给定密钥候选枚举

用法：
  # 已知明文（最有力）：你在浏览器点了哪个座位，就用那个座位的 devId
  python tools\\verify_aes.py --pair 100493961 522C1B95D2B40C5F05BB3BAAD1380099

  # 只验证按位/长度特性
  python tools\\verify_aes.py --demo
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import aes  # noqa: E402

IV = "ABCDEF1234123412"          # 来自 JS: Utf8.parse("ABCDEF1234123412")
KEYCHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"


def devid_cipher(dev_id, key, mode="cbc", pad="zero", iv=IV, upper=True):
    """按 JS 的 b() 实现计算 devId 的密文。"""
    if mode == "cbc":
        return aes.encrypt_cbc(str(dev_id), key, iv, pad=pad, upper=upper)
    return aes.encrypt_ecb(str(dev_id), key, pad=pad, upper=upper)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pair", nargs=2, metavar=("DEVID", "CIPHER"),
                    help="已知明文与密文，用于反推密钥")
    ap.add_argument("--ct", default="522C1B95D2B40C5F05BB3BAAD1380099",
                    help="抓包密文（默认用历史抓包值）")
    ap.add_argument("--dev", default="", help="已知的 devId")
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--brute-len", type=int, default=0,
                    help="暴力尝试口令长度（<=6 时可行），0=不做")
    args = ap.parse_args()

    print("=" * 78)
    print("  JS 实测实现（chunk-common 模块 fa7d 的 b 函数）")
    print("=" * 78)
    print(f"  IV        = {IV!r}")
    print("  KEY       = Utf8(随机16位口令)")
    print("  算法      = AES-CBC + ZeroPadding -> hex -> 大写")
    print()

    dev = args.dev
    ct = args.ct.upper()
    if args.pair:
        dev, ct = args.pair[0], args.pair[1].upper()

    print("=" * 78)
    print("  长度特性自检")
    print("=" * 78)
    for n in (5, 9, 15, 16, 17, 32):
        plain = str(dev).ljust(n, "0")[:n]
        out = devid_cipher(plain, "Uzea1Lx1fgrHUVm8", "cbc", "zero")
        print(f"  明文 {n:>2} 字节 -> 密文 {len(out)//2:>2} 字节 ({len(out)} 字符)"
              f"  {'✅ 1块' if len(out) == 32 else '2块+'}")

    print()
    print("=" * 78)
    print("  已知明文对撞")
    print("=" * 78)
    if not dev:
        print("  没给明文 devId。")
        print("  ⚠️ 密文只有 16 字节（1 个 AES 块），说明 devId 明文 <= 16 字节。")
        print("     若你在浏览器点的是 2F-B001，其 devId = 100493961（9 字节），")
        print("     那么明文就是 '100493961'。用它跑：")
        print(f"       python tools\\verify_aes.py --pair 100493961 {ct}")
        return

    print(f"  明文 devId = {dev}  ({len(str(dev))} 字节)")
    print(f"  目标密文   = {ct}")
    print()

    # 先试固定候选口令
    candidates = [
        "ABCDEF1234123412", "1234123412ABCDEF", "XwKsGlMcdPMEhR1B",
        "1234567890123456", "0000000000000000",
    ]
    hit = False
    for k in candidates:
        for pad in ("zero", "pkcs7"):
            out = devid_cipher(dev, k, "cbc", pad)
            mark = " ★★★ 命中" if out == ct else ""
            print(f"    口令 {k!r:<20} cbc/{pad:<6} {out}{mark}")
            if out == ct:
                hit = True
    if hit:
        print()
        print("  ✅ 命中固定口令 -> 前端用的是固定密钥，可直接写死。")
        return

    print()
    print("  ✗ 固定候选都没中 -> 口令是真随机的（与 JS 的 w() 生成逻辑一致）。")
    print("    这是**正常的**，说明必须动态生成口令、动态加密。实现已就绪。")
    print()
    print("  想 100% 确认 CBC/ZeroPadding 正确，需要一对「明文+密文」：")
    print("    1. 在浏览器点某个座位（记住座位名，如 2F-B001）并提交")
    print("    2. 抓那条 POST /ic-web/reserve/seat，取出 resvDevEncrypts[0]")
    print("    3. python tools\\verify_aes.py --pair <该座位devId> <该密文>")

    if args.brute_len:
        import itertools
        n = args.brute_len
        print(f"\n  暴力枚举 {n} 位口令（字符集 {len(KEYCHARS)}）… 共 {len(KEYCHARS)**n:,} 种")
        cnt = 0
        for combo in itertools.product(KEYCHARS, repeat=n):
            cnt += 1
            if devid_cipher(dev, "".join(combo), "cbc", "zero") == ct:
                print(f"  ★★★ 找到口令: {''.join(combo)}  （试了 {cnt:,} 次）")
                return
        print(f"  未找到（试了 {cnt:,} 次）。")


if __name__ == "__main__":
    main()
