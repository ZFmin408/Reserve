"""从已下载的 JS chunk 里提取完整的 API 路径表（纯标准库）。

webpack 打包后，所有接口路径都是字符串常量，形如 url:"/ic-web/xxx/yyy"。
本工具把它们全捞出来并按模块归类，用来确定正确端点，不再靠猜。

用法：
  python tools\\extract_api.py                     # 扫 logs\\js 全部
  python tools\\extract_api.py --grep seat         # 只看含 seat 的
  python tools\\extract_api.py --show-context      # 同时打印每条的上下文
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS_DIR = os.path.join(ROOT, "logs", "js")

# url:"/xxx"  /  url:'/xxx'  /  "/ic-web/xxx"
PATTERNS = [
    re.compile(r"""url\s*:\s*['"]([^'"]{2,120})['"]"""),
    re.compile(r"""['"](\/ic-web\/[A-Za-z0-9_\-/{}.$]{2,120})['"]"""),
    re.compile(r"""['"](\/login\/[A-Za-z0-9_\-/]{2,80})['"]"""),
    re.compile(r"""['"](/[a-zA-Z][A-Za-z0-9_\-]*(?:\/[A-Za-z0-9_\-{}.:$]+){1,5})['"]"""),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=JS_DIR)
    ap.add_argument("--grep", default="")
    ap.add_argument("--show-context", action="store_true")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()

    if not os.path.isdir(args.dir):
        print(f"目录不存在: {args.dir}  —— 先跑 tools\\crawl_js.py")
        return

    by_file: dict = {}
    by_path: dict = defaultdict(set)
    ctx_by_path: dict = {}

    files = [f for f in sorted(os.listdir(args.dir)) if f.endswith(".js")]
    for name in files:
        path = os.path.join(args.dir, name)
        text = open(path, encoding="utf-8", errors="replace").read()
        hits = {}
        for rx in PATTERNS:
            for m in rx.finditer(text):
                u = m.group(1)
                if u.startswith("data:") or u.startswith(".") or u.startswith("//"):
                    continue
                if any(u.endswith(ext) for ext in (".js", ".css", ".png", ".jpg", ".svg", ".woff", ".woff2", ".ttf", ".html", ".ico", ".json", ".gif", ".mp4")):
                    continue
                if len(u) < 4 or "/" not in u:
                    continue
                hits[u] = m.start()
                by_path[u].add(name)
                if u not in ctx_by_path:
                    a = max(0, m.start() - 80)
                    b = min(len(text), m.end() + 80)
                    ctx_by_path[u] = (name, text[a:b].replace("\n", " "))
        if hits:
            by_file[name] = sorted(hits)

    print("=" * 78)
    print(f"  扫描 {len(files)} 个 JS，发现 {len(by_path)} 个不同的路径")
    print("=" * 78)

    rows = []
    for u in sorted(by_path):
        if args.grep and args.grep.lower() not in u.lower():
            continue
        rows.append((u, sorted(by_path[u])))

    if args.json:
        print(json.dumps([{"path": u, "files": f} for u, f in rows], ensure_ascii=False, indent=2))
        return

    # 按前缀分组
    groups: dict = defaultdict(list)
    for u, fl in rows:
        parts = u.strip("/").split("/")
        key = "/" + "/".join(parts[:2]) if len(parts) >= 2 else u
        groups[key].append((u, fl))

    for key in sorted(groups):
        print(f"\n### {key}  ({len(groups[key])})")
        for u, fl in sorted(groups[key]):
            print(f"  {u:<52} {', '.join(f[:22] for f in fl)}")
            if args.show_context:
                n, c = ctx_by_path[u]
                print(f"        …{c}…")

    out = os.path.join(ROOT, "logs", "api-paths.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump([{"path": u, "files": fl,
                    "context": ctx_by_path.get(u, ("", ""))[1]} for u, fl in rows],
                  f, ensure_ascii=False, indent=2)
    print(f"\n  共 {len(rows)} 条 -> {out}")


if __name__ == "__main__":
    main()
