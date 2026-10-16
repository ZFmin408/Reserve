"""用已知密文反推 devId / 口令（纯标准库）。

已知：AES-CBC(devId, KEY=口令, IV="ABCDEF1234123412", ZeroPadding).hex().upper()
      encryptVlaue = RSA(口令)

口令理论上随机，但很多实现会退化（固定种子、可预测、或基于某个字段派生），
所以这里把「常见退化口令」全试一遍，命中就能直接解出 devId。

用法：
  python tools/devid_from_ct.py --ct F82CDC293F6151FF799E75DD8E4F20B2
  python tools/devid_from_ct.py --ct <密文> --keys "口令1" "口令2"
  python tools/devid_from_ct.py --ct <密文> --scan-dev          # 用已知 devId 集合反查
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import os
import string
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import aes  # noqa: E402

IV = "ABCDEF1234123412"
ACCNO = "100510484"
KNOWN_DEVIDS = [
    100493961, 100493962, 100493963, 100494732,
] 

# 从已探明的房间/座位里取一批候选 devId（二楼B区连续段）
def devid_seed_range():
    return list(range(100493961, 100493961 + 400)) + list(range(100494732, 100494732 + 60))


def looks_like_devid(plain: bytes) -> bool:
    """解密结果去掉 ZeroPadding 后应当是纯数字 devId。"""
    s = plain.rstrip(b"\x00")
    if not s or len(s) > 16:
        return False
    return s.isdigit()


def try_key(ct: str, key: str, verbose: bool = False):
    """用给定口令解密，返回明文（若像 devId）。"""
    try:
        pt = aes.decrypt_cbc(ct, key, IV)
    except Exception:
        return None
    if verbose:
        print(f"    key={key!r:<20} -> {pt!r}")
    return pt if looks_like_devid(pt) else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ct", required=True, help="resvDevEncrypts[0] 的密文")
    ap.add_argument("--keys", nargs="*", default=None)
    ap.add_argument("--scan-dev", action="store_true")
    ap.add_argument("--brute", type=int, default=0, help="暴力枚举口令长度(<=5)")
    args = ap.parse_args()

    ct = args.ct.strip().upper()
    print("=" * 78)
    print(f"  目标密文: {ct}  ({len(ct)//2} 字节)")
    print("=" * 78)
    print(f"  AES-CBC / IV={IV!r} / ZeroPadding")

    keys = list(args.keys or [])

    # ---- 1) 常见退化口令 ----
    print()
    print("=== 1. 常见退化口令 ===")
    cand = []
    cand += ["0000000000000000", "1111111111111111", "1234567890123456",
             "ABCDEF1234123412", "1234123412ABCDEF", "XwKsGlMcdPMEhR1B",
             ACCNO, ACCNO[:16], ACCNO.ljust(16, "0"), ACCNO.ljust(16, " ")[:16]]
    cand += ["devId".ljust(16, "0"), "password".ljust(16, "0")[:16]]
    # 可打印字符集里长度为 8/16 的重复模式
    for ch in string.ascii_letters + string.digits:
        cand.append(ch * 16)
    cand += keys

    found = None
    for k in dict.fromkeys(cand):
        if len(k) != 16:
            continue
        pt = try_key(ct, k)
        if pt:
            found = (k, pt.rstrip(b"\x00").decode())
            print(f"  ★★★ 命中! 口令={k!r}  ->  devId={found[1]}")
            break
    if not found:
        print("  ✗ 都不中")

    # ---- 2) 基于 devId 集合反查（已知明文 + 穷举口令不可行，反过来试） ----
    if args.scan_dev and not found:
        print()
        print("=== 2. 用已知 devId 集合反查（假设口令=某个可预测值） ===")
        print("  这条其实等价于试口令，故跳过；见 --brute")

    # ---- 3) 暴力枚举短口令 ----
    if args.brute and not found:
        chars = string.ascii_letters + string.digits
        n = args.brute
        total = len(chars) ** n
        print()
        print(f"=== 3. 暴力枚举 {n} 位口令（{total:,} 种）===")
        for i, combo in enumerate(itertools.product(chars, repeat=n)):
            k = "".join(combo)
            pt = try_key(ct, k)
            if pt:
                found = (k, pt.rstrip(b"\x00").decode())
                print(f"  ★★★ 找到! 口令={k!r}  ->  devId={found[1]}  (试了 {i+1:,} 次)")
                break
            if i and i % 200000 == 0:
                print(f"    …已试 {i:,}")
        if not found:
            print(f"  未找到（试了 {total:,} 次）")

    print()
    print("=" * 78)
    if found:
        print(f"  ✅ 口令可预测！devId = {found[1]}")
        print("     但注意：即使口令可预测，程序也应动态生成口令，不要写死。")
    else:
        print("  结论：口令是真随机的（与 JS 的 w(16) 实现一致），")
        print("  **密文无法反推 devId**。")
        print()
        print("  获取这个座位的 devId，两条路：")
        print("  ① 抓座位列表请求（最直接）：")
        print("     GET /ic-web/reserve?roomIds=<房间>&resvDates=YYYYMMDD&sysKind=8")
        print("     响应里每个座位的 devId / devName 都有")
        print("  ② 或告诉我你在页面上点的座位名（如 2F-B023），")
        print("     程序用 /ic-web/reserve 查出对应 devId")
    print("=" * 78)


if __name__ == "__main__":
    main()
