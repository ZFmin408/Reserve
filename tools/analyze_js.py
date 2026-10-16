"""从已下载的 JS chunk 里定位登录/加密逻辑（纯标准库）。

配合 tools\\crawl_js.py 使用：先把 JS 下到 logs\\js，再用本工具挖上下文。

用法：
  python tools\\analyze_js.py logs\\js\\chunk-7d830362_xxx.js --grep "$encrypt" "nonceStr" login
  python tools\\analyze_js.py logs\\js --grep secrtInfo          # 目录模式，全目录搜
  python tools\\analyze_js.py logs\\js\\a.js --dump 57000 59000   # 直接看字节区间
"""
from __future__ import annotations

import argparse
import os
import re
import sys

DEFAULT_KEYWORDS = [
    "$encrypt", "setPublicKey", "nonceStr", "secrtInfo", "getSecrt",
    "logonName", "consoleType", "privacy", "captcha",
    "login/publicKey", "publicKey",
]


def iter_files(target: str):
    if os.path.isdir(target):
        for name in sorted(os.listdir(target)):
            if name.endswith(".js"):
                yield os.path.join(target, name)
    else:
        yield target


def show(text: str, pos: int, before: int, after: int, label: str, maxlen: int = 900) -> None:
    a = max(0, pos - before)
    b = min(len(text), pos + after)
    seg = text[a:b].replace("\n", " ")
    if len(seg) > maxlen:
        seg = seg[:maxlen] + " …"
    print(f"--- {label} @{pos} ---")
    print(f"    {seg}")
    print()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("target", help="JS 文件或目录")
    ap.add_argument("--grep", nargs="*", default=None, help="要搜的关键词")
    ap.add_argument("--before", type=int, default=280)
    ap.add_argument("--after", type=int, default=420)
    ap.add_argument("--max-hits", type=int, default=4, help="每个关键词最多打印几处")
    ap.add_argument("--dump", nargs=2, type=int, metavar=("START", "END"), help="打印字节区间")
    args = ap.parse_args()

    keywords = args.grep if args.grep else DEFAULT_KEYWORDS

    for path in iter_files(args.target):
        if not os.path.exists(path):
            print(f"[skip] {path} 不存在")
            continue
        text = open(path, encoding="utf-8", errors="replace").read()
        print("=" * 78)
        print(f"## {os.path.basename(path)}  ({len(text)} bytes)")
        print("=" * 78)

        if args.dump:
            s, e = args.dump
            print(text[max(0, s):e])
            print()
            continue

        for kw in keywords:
            hits = list(re.finditer(re.escape(kw), text))
            if not hits:
                continue
            print(f"[{kw}] 命中 {len(hits)} 处")
            for m in hits[:args.max_hits]:
                show(text, m.start(), args.before, args.after, kw)
        print()


if __name__ == "__main__":
    main()
