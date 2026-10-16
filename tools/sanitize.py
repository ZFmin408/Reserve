"""一键脱敏：把仓库里残留的个人信息替换成中性占位值（纯标准库）。

为什么需要：config.json（含真实密码/学号）已被 .gitignore 挡住，
但示例值、测试常量、工具默认参数里仍残留：
  - 学校服务器内网地址 10.0.0.10
  - 真实账号 accNo 100000001 / 100000002
  - 真实学号 202300000000

用法：
  python tools\\sanitize.py            # 预览将要替换的内容（不改文件）
  python tools\\sanitize.py --apply    # 真正执行替换
  python tools\\sanitize.py --apply --ip http://10.0.0.10
"""
from __future__ import annotations

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 只处理这些后缀，且只处理源码目录（logs/ 已被 gitignore，不动）
SCAN_DIRS = ("app", "web", "tools", "tests", ".")
SCAN_EXTS = (".py", ".js", ".html", ".css", ".md", ".json", ".bat")
SKIP_NAMES = {"config.json"}          # 本地真实配置，不碰
SKIP_DIRS = {"logs", ".git", ".venv", "__pycache__"}

DEFAULT_RULES = [
    # (原串, 替换为, 说明)
    ("10.0.0.10", "10.0.0.10", "学校内网服务器地址"),
    ("100000001", "100000001", "真实 accNo（本人）"),
    ("100000002", "100000002", "真实 accNo（他人）"),
    ("202300000000", "202300000000", "真实学号"),
]


def git_tracked_files() -> list:
    """只返回 git 会跟踪的文件（自动遵守 .gitignore）。

    这样 config.json / test.txt / user.txt / logs/ 这些本地数据永远不会被改动。
    """
    import subprocess
    try:
        out = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"],
                             cwd=ROOT, capture_output=True, text=True, timeout=30)
        if out.returncode == 0 and out.stdout.strip():
            return [os.path.join(ROOT, p) for p in out.stdout.splitlines() if p.strip()]
    except Exception as e:
        print(f"  [warn] git 不可用（{e}），退回文件系统扫描", file=sys.stderr)
    return list(_iter_fs_files())


def _iter_fs_files():
    for d in SCAN_DIRS:
        base = os.path.normpath(os.path.join(ROOT, d))
        if not os.path.isdir(base):
            continue
        if d == ".":
            for f in os.listdir(base):
                p = os.path.join(base, f)
                if os.path.isfile(p):
                    yield p
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [x for x in dirnames if x not in SKIP_DIRS]
            for fn in filenames:
                if fn in SKIP_NAMES or not fn.endswith(SCAN_EXTS):
                    continue
                yield os.path.join(dirpath, fn)


def iter_files():
    """仅遍历 git 跟踪/待跟踪的文件，且过滤后缀和排除名单。"""
    for path in git_tracked_files():
        fn = os.path.basename(path)
        if fn in SKIP_NAMES or not fn.endswith(SCAN_EXTS):
            continue
        if not os.path.isfile(path):
            continue
        # 双保险：排除被 gitignore 的路径
        parts = set(os.path.normpath(os.path.relpath(path, ROOT)).split(os.sep))
        if parts & SKIP_DIRS:
            continue
        yield path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真正写入（默认只预览）")
    ap.add_argument("--ip", default="", help="把服务器地址替换成这个值")
    args = ap.parse_args()

    rules = list(DEFAULT_RULES)
    if args.ip:
        rules[0] = (rules[0][0], args.ip, rules[0][2])

    print("=" * 76)
    print("  脱敏规则")
    print("=" * 76)
    for old, new, why in rules:
        print(f"  {old:<18} -> {new:<16} ({why})")
    print()

    changed = []
    for path in iter_files():
        try:
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
        except (UnicodeDecodeError, OSError):
            continue
        orig = text
        counts = {}
        for old, new, _ in rules:
            n = text.count(old)
            if n:
                counts[old] = n
                text = text.replace(old, new)
        if text != orig:
            rel = os.path.relpath(path, ROOT)
            changed.append((rel, counts))
            if args.apply:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(text)

    print("=" * 76)
    print(f"  {'已修改' if args.apply else '待修改'} {len(changed)} 个文件")
    print("=" * 76)
    for rel, counts in changed:
        detail = ", ".join(f"{k}×{v}" for k, v in counts.items())
        print(f"  {rel:<42} {detail}")
    if not args.apply:
        print()
        print("  这是预览。确认无误后执行： python tools\\sanitize.py --apply")
    else:
        print()
        print("  完成。建议接着跑测试确认没改坏： python tests\\selftest.py")


if __name__ == "__main__":
    main()
