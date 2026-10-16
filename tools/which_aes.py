"""定位 SeatForm 模块里的 AES 加密函数究竟是哪个实现（纯标准库）。

背景：下单 payload 里
    resvDevEncrypts[0] = Object(d["a"])(devId, a)   # a = Object(d["e"])(16) 随机口令
chunk-common 里有两个 AES 封装：
    v(t)      = AES-CBC( key=Utf8(t), iv="ABCDEF1234123412", Pkcs7 )   -> 大写hex
    b(t, e)   = AES-CBC( key=Utf8(e), iv="ABCDEF1234123412", ZeroPad ) -> 大写hex
需要确定 d["a"] 是 v 还是 b。

用法：
  python tools\\which_aes.py
  python tools\\which_aes.py --chunk logs\\js\\chunk-03e59dfa_xxx.js
"""
from __future__ import annotations

import argparse
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def module_boundaries(src: str):
    """返回 [(模块id, 起始偏移)]。"""
    out = []
    for m in re.finditer(r'"([0-9a-f]{4})"\s*:\s*function\s*\(', src):
        out.append((m.group(1), m.start()))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--chunk", default=os.path.join(ROOT, "logs", "js",
                                                   "chunk-03e59dfa_1777260490266.js"))
    args = ap.parse_args()

    src = open(args.chunk, encoding="utf-8", errors="replace").read()
    i = src.find('name:"SeatForm"')
    if i < 0:
        print("没找到 SeatForm 模块")
        return
    print(f"SeatForm @ {i}")

    # 找包含 SeatForm 的模块边界
    bounds = [b for b in module_boundaries(src) if b[1] < i]
    if not bounds:
        print("定位不到模块边界")
        return
    mod_id, start = bounds[-1]
    end = i + 200000
    nxt = [b for b in module_boundaries(src) if b[1] > i]
    if nxt:
        end = nxt[0][1]
    seg = src[start:end]
    print(f"所在模块: {mod_id}  ({start} ~ {end}, {len(seg)} 字节)")
    print()

    # 该模块里的 import 别名映射
    print("=== 模块内 import 别名 ===")
    alias = {}
    for m in re.finditer(r'([a-zA-Z_\$]{1,3})\s*=\s*[a-z]\([\'"]([0-9a-f]{4})[\'"]\)', seg):
        alias[m.group(1)] = m.group(2)
        print(f"  {m.group(1)} -> 模块 {m.group(2)}")
    print()

    # 找 d["a"] / d["e"] / d["j"] / d["r"] 的用法
    for key in ["a", "e", "j", "r", "m", "n", "c"]:
        n = len(re.findall(r'Object\(d\["' + key + r'"\]\)', seg))
        if n:
            print(f"  Object(d[\"{key}\"]) 出现 {n} 次  -> 模块 {alias.get('d', '?')}")

    print()
    print("=== 模块内 d 的定义 ===")
    for m in re.finditer(r'\bd\s*=\s*([a-zA-Z])\([\'"]([0-9a-f]{4})[\'"]\)', seg):
        print(f"  d = {m.group(1)}(\"{m.group(2)}\")")
    print()

    # 直接搜函数定义 v/b 是否在本模块
    for name in ["v", "b"]:
        pat = r'function ' + name + r'\([^)]*\)\s*\{[^}]{0,400}?AES\.(?:encrypt|decrypt)'
        m = re.search(pat, seg)
        print(f"  本模块是否定义 {name}() 且用 AES: {'是' if m else '否'}")

    print()
    print("=== 提示 ===")
    print("  想要最终确认，需要一对「明文 devId + 对应密文」：")
    print("  让用户在浏览器点某个座位（记住座位名，如 2F-B001），提交后")
    print("  把该次抓包的 resvDevEncrypts 值给出来。")
    print("  有这对值，python tools/crack_aes.py --dev <devId> --ct <密文> --keys <口令候选>")
    print("  但口令是随机的，所以更实际的办法是离线枚举 v/b 两种实现：")
    print("  python tools/crack_aes.py --pair <devId> <密文>   （见该工具 --help）")


if __name__ == "__main__":
    main()
