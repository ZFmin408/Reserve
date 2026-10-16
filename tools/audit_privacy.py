"""打包前隐私审计：列出会被打包进去的个人/校内信息（纯标准库）。

用法：
  python tools\\audit_privacy.py            # 审计当前目录
  python tools\\audit_privacy.py --json     # 输出 JSON，便于接入流水线

为什么需要：这个程序天然会持有登录凭据（账号密码、会话 token、accNo）。
打包分发给别人之前，必须先把「你自己那份」清掉，否则等于把凭据一起发出去。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 扫描特征。键是说明，值是正则。
PATTERNS = {
    "服务器地址": r"\b10\.20\.15\.27\b",
    "学号": r"\b2023010507\d{2}\b|\b2023010508\d{2}\b",
    "accNo": r"\b100510[34]\d{2}\b",
    "会话token字面量": r'["\']token["\']\s*:\s*["\'][0-9a-f]{32}["\']',
    "ic-cookie": r"ic-cookie=[0-9a-f\-]{36}",
    "密码明文": r'"password"\s*:\s*"(?!pw|secret|\{\{)[^"]{3,}"',
    "座位名": r"\b[2-7]F-[A-Z]\d{3}\b",
    "真实姓名": r"高希旻|王月敬",
}

# A 级：一旦打包进去就等于泄露凭据
LEVEL_A = ["config.json", "test.txt", "user.txt"]
# B 级：运行期产生
LEVEL_B_DIRS = ["logs"]
# 源码后缀
SRC_EXTS = (".py", ".js", ".html", ".css", ".json", ".md", ".bat")
SKIP_DIRS = {".git", ".venv", "__pycache__", "logs", ".idea", "dist", "build"}
SKIP_FILES = {"sanitize.py", "audit_privacy.py"}   # 含规则表，跳过


def scan_text(text: str) -> dict:
    out = {}
    for name, rx in PATTERNS.items():
        n = len(re.findall(rx, text))
        if n:
            out[name] = n
    return out


def read(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    report = {"level_a": [], "level_b": [], "level_c": [], "notes": []}

    # ---- A 级 ----
    for rel in LEVEL_A:
        p = os.path.join(ROOT, rel)
        if os.path.isfile(p):
            hits = scan_text(read(p))
            report["level_a"].append({"file": rel, "size": os.path.getsize(p), "hits": hits})

    # ---- B 级 ----
    for d in LEVEL_B_DIRS:
        base = os.path.join(ROOT, d)
        if not os.path.isdir(base):
            continue
        total = 0
        files = 0
        aggregated = {}
        for dirpath, _, fns in os.walk(base):
            for fn in fns:
                fp = os.path.join(dirpath, fn)
                total += os.path.getsize(fp)
                files += 1
                if fn.endswith(".log") or fn.endswith(".txt"):
                    for k, v in scan_text(read(fp)).items():
                        aggregated[k] = aggregated.get(k, 0) + v
        report["level_b"].append({"dir": d, "files": files, "bytes": total,
                                  "hits_in_logs": aggregated})

    # ---- C 级 ----
    for dirpath, dirs, fns in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for fn in fns:
            if fn in SKIP_FILES or not fn.endswith(SRC_EXTS):
                continue
            fp = os.path.join(dirpath, fn)
            hits = scan_text(read(fp))
            # 代码里出现 "token"/"ic-cookie" 字样是正常的，不算泄露
            hits.pop("ic-cookie", None)
            if hits:
                report["level_c"].append({"file": os.path.relpath(fp, ROOT).replace("\\", "/"),
                                          "hits": hits})

    report["notes"] = [
        "A 级：必须从打包产物中排除（凭据本体）",
        "B 级：运行期产生，含真实业务数据与抓包结果，需排除",
        "C 级：源码中的硬编码/示例值，建议替换成中性占位值",
        "排除后程序仍可运行：首次启动让用户在界面上自行填写账号密码",
    ]

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1 if (report["level_a"] or report["level_c"]) else 0

    print("=" * 78)
    print("  A 级 · 直接泄露凭据 —— 打包必须排除")
    print("=" * 78)
    for x in report["level_a"]:
        print(f"  {x['file']:<16} {x['size']:>7} B   " + ", ".join(f"{k}×{v}" for k, v in x["hits"].items()))
    if not report["level_a"]:
        print("  （无）")

    print()
    print("=" * 78)
    print("  B 级 · 运行期产生 —— 打包必须排除")
    print("=" * 78)
    for x in report["level_b"]:
        print(f"  {x['dir']}/  {x['files']} 个文件, {x['bytes']:,} B")
        if x["hits_in_logs"]:
            print("      日志内含: " + ", ".join(f"{k}×{v}" for k, v in x["hits_in_logs"].items()))
    if not report["level_b"]:
        print("  （无）")

    print()
    print("=" * 78)
    print("  C 级 · 源码硬编码/示例值 —— 建议替换")
    print("=" * 78)
    for x in report["level_c"]:
        print(f"  {x['file']:<38} " + ", ".join(f"{k}×{v}" for k, v in x["hits"].items()))
    if not report["level_c"]:
        print("  （无）")

    print()
    print("=" * 78)
    total = len(report["level_a"]) + len(report["level_b"]) + len(report["level_c"])
    print(f"  共 {total} 项需要处理")
    print("=" * 78)
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
