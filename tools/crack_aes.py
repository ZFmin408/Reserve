"""反推 resvDevEncrypts 的 AES 参数（纯标准库）。

背景：这套 IC 系统下单时
    a = 随机16位口令
    encryptVlaue      = RSA(a)                -> base64（128字节）
    resvDevEncrypts[] = AES(devId, a)         -> 大写 hex（16字节）
但前端用的是哪个 AES 封装（ECB/CBC、PKCS7/ZeroPadding、密钥是否直接取 a）需要确认。

做法：已知密文 + 猜测的明文(devId) + 猜测的密钥，暴力对撞。
  - 若命中，说明参数正确，且顺便确认了 devId
  - 若都不中，说明密钥不是候选之一（就是真随机 a，属正常情况）——
    此时改用「动态加密」即可，程序自己生成 a、自己算。

用法：
  python tools\\crack_aes.py
  python tools\\crack_aes.py --dev 100492994 --ct 522C1B95D2B40C5F05BB3BAAD1380099
  python tools\\crack_aes.py --keys "mypass" "1234123412ABCDEF"
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import aes  # noqa: E402

# 抓包实测到的密文
KNOWN_CT = "522C1B95D2B40C5F05BB3BAAD1380099"

# devId 候选（来自已探明的场馆 roomId 等）
DEV_CANDIDATES = [
    "100492994", "100492996", "100493000", "100494732", "100494733", "100494734",
    "100494735", "100494736", "100494737", "100494738", "100494740", "100494741",
    "100510484", "100510404",
    "0", "1", "801", "802", "805", "808", "809", "811", "814",
]

MODE_IVS = {
    "ECB": None,
    "CBC_iv_ABCDEF1234123412": "ABCDEF1234123412",
    "CBC_iv_0000000000000000": "0000000000000000",
}
PADDINGS = ["pkcs7", "zero"]


def try_all(dev: str, key: str, verbose: bool = False) -> list:
    hits = []
    for mode, iv in MODE_IVS.items():
        for pad in PADDINGS:
            try:
                if iv is None:
                    out = aes.encrypt_ecb(dev, key, pad=pad, upper=True)
                else:
                    out = aes.encrypt_cbc(dev, key, iv, pad=pad, upper=True)
            except Exception:
                continue
            if verbose:
                print(f"      {mode:<28} {pad:<6} {out}")
            hits.append((mode, pad, out))
    return hits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ct", default=KNOWN_CT)
    ap.add_argument("--dev", default="")
    ap.add_argument("--keys", nargs="*", default=None)
    args = ap.parse_args()

    target = args.ct.upper()
    print("=" * 78)
    print(f"  目标密文: {target}")
    print(f"  长度: {len(target)} 字符 = {len(target)//2} 字节")
    print("=" * 78)

    keys = args.keys if args.keys else []
    # 固定候选密钥
    fixed_keys = [
        "XwKsGlMcdPMEhR1B",                 # chunk-4fc8d707 里的默认密钥
        "1234123412ABCDEF",                 # chunk-common 的 AES 密钥
        "ABCDEF1234123412",                 # chunk-common 的 IV
        "", "0", "123456", "1234567890123456",
    ]
    keys = list(dict.fromkeys(keys + fixed_keys))

    devs = [args.dev] if args.dev else DEV_CANDIDATES
    devs = [d for d in devs if d]
    # 若给了密钥，顺便试 key 跟 密文 的组合
    for extra in (target,):
        keys_hashed = [hashlib.md5(k.encode()).hexdigest()[:16] for k in keys if k]

    print(f"  明文(devId)候选 {len(devs)} 个, 固定密钥候选 {len(keys)} 个")
    print()

    found = False
    for dev in devs:
        for k in keys + keys_hashed:
            if not k or len(k) != 16:
                continue
            for mode, pad, out in try_all(dev, k):
                if out == target:
                    print(f"  ★★★ 命中!  devId={dev}  密钥={k!r}  {mode}  {pad}")
                    found = True
        # 单独打印一个 dev 的完整组合，便于人工比对
        if dev == devs[0]:
            print(f"  [参考] devId={dev} 在各密钥下的输出：")
            for k in keys[:3]:
                if k and len(k) == 16:
                    print(f"    密钥 {k!r}:")
                    try_all(dev, k, verbose=True)

    print()
    if found:
        print("  结论：参数已确定，可写死进程序。")
    else:
        print("  未命中。这属于**正常情况**——前端用的是真随机 16 位口令 a，")
        print("  不可能在候选里出现。结论是采用「动态加密」：")
        print("    1. 程序自己生成 a = 16 位随机 [A-Za-z0-9]")
        print("    2. encryptVlaue      = RSA(a)            -> base64")
        print("    3. resvDevEncrypts   = AES(devId, a)     -> 大写 hex")
        print("  唯一还需确认的是 AES 的 mode/padding —— 见下。")
        print()
        print("  判定依据：密文只有 16 字节。")
        print("    · PKCS7 对 16 字节的整数倍明文会再多补一整块 -> 32 字节，不符合")
        print("    · 所以要么明文 < 16 字节（如 devId=100492994 共 9 字符）+ PKCS7")
        print("    · 要么 ZeroPadding（16 字节以内都只输出 1 块）")
        print("  两者对 9 字符的 devId 结果相同，程序会两种都实现，默认 ECZero。")


if __name__ == "__main__":
    main()
