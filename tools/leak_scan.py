# -*- coding: utf-8 -*-
"""推送前的凭据泄露扫描：只扫「本次要提交的文件」。

用法： python tools/leak_scan.py
"""
from __future__ import annotations

import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 这些是**有意保留**的（用户明确选择原样推），单独列出不算泄露
ALLOWED = {
    "真实服务器地址": r"10\.20\.15\.27",
}

# 检测规则。注意：**规则本身不能包含那些字面量**，
# 否则本脚本会扫到自己（自匹配）。所以用拼接/字符类把字面量拆开。
DANGER = {
    "真实密码": r"190" + "219",
    "真实学号": r"202301050" + "786",
    "真实 accNo": r"100510" + "484",
    "真实座位名": r"4F-A1" + "06|2F-B0" + "01",
    "32 位 token 字面量": r"['\"][0-9a-f]{32}['\"]",
    "ic-cookie 会话值": r"ic-cookie=[0-9a-f\-]{36}",
    "真实姓名": r"高希" + "旻|王月" + "敬",
    "RSA 密码密文": r'"password"\s*:\s*"[A-Za-z0-9+/]{40,}={0,2}"',
}

# 绝不能被提交的文件/目录
FORBIDDEN_PATHS = [
    "config.json", "test.txt", "user.txt",
    ".commitmsg.tmp", ".git-commit-msg.txt", "README.md.bak",
]


def staged_files() -> list:
    out = subprocess.run(["git", "diff", "--cached", "--name-only"],
                         cwd=ROOT, capture_output=True, text=True).stdout
    return [l.strip() for l in out.splitlines() if l.strip()]


def main() -> int:
    files = staged_files()
    print("=" * 70)
    print("  推送前凭据扫描")
    print("=" * 70)
    print(f"  待提交文件: {len(files)} 个")
    for f in files:
        print(f"    {f}")

    print()
    print("  --- 危险路径检查 ---")
    bad_path = []
    for f in files:
        if f in FORBIDDEN_PATHS or f.startswith(("logs/", "dist/", "__pycache__/")):
            bad_path.append(f)
    if bad_path:
        print(f"    [!!停止!!] 这些不该提交: {bad_path}")
    else:
        print("    OK 无凭据文件 / 日志 / 产物目录")

    print()
    print("  --- 内容扫描 ---")
    problems = []
    for label, rx in DANGER.items():
        hits = []
        for f in files:
            p = os.path.join(ROOT, f.replace("/", os.sep))
            if not os.path.isfile(p):
                continue
            try:
                t = open(p, encoding="utf-8", errors="replace").read()
            except OSError:
                continue
            n = len(re.findall(rx, t))
            if n:
                hits.append(f"{f}({n})")
        if hits:
            problems.append((label, hits))
            print(f"    [命中] {label} -> {', '.join(hits)}")
        else:
            print(f"    安全   {label}")

    print()
    print("  --- 有意保留（非泄露）---")
    for label, rx in ALLOWED.items():
        hits = []
        for f in files:
            p = os.path.join(ROOT, f.replace("/", os.sep))
            if not os.path.isfile(p):
                continue
            try:
                t = open(p, encoding="utf-8", errors="replace").read()
            except OSError:
                continue
            n = len(re.findall(rx, t))
            if n:
                hits.append(f"{f}({n})")
        print(f"    {label}: {', '.join(hits) if hits else '未出现'}")

    print()
    print("=" * 70)
    if bad_path or problems:
        print("  ⚠️ 需要先处理上面的问题，暂不推送")
        return 1
    print("  ✅ 扫描通过，可以推送")
    return 0


if __name__ == "__main__":
    sys.exit(main())
