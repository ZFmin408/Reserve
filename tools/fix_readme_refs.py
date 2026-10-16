# -*- coding: utf-8 -*-
"""修正 README 里残留的过时引用（学校名、已不存在的工具脚本）。

统一用 Python + 显式 UTF-8 写文件（PowerShell 文本重定向曾破坏过编码）。
用法： python tools/fix_readme_refs.py [--dry-run]
"""
from __future__ import annotations

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
README = os.path.join(ROOT, "README.md")

# 精确替换规则：(旧文本, 新文本)
RULES = [
    # 学校名：实际是青岛理工大学，不是中山大学
    ("## 2.6 中山大学东校园 IC 系统 —— 实测确定的完整协议",
     "## 2.6 IC 空间预约系统 —— 实测确定的完整协议"),

    # tools/sniffer.py 已从仓库移除；如实说明，并给出可用的替代方案
    ("python tools\\sniffer.py          # 监听 127.0.0.1:8800，转发到 10.20.15.27",
     "# 仓库里没有内置中继脚本，用 mitmproxy / Charles 即可\n"
     "# mitmproxy 示例: mitmproxy --mode regular@8800"),

    ("- 用 `tools\\sniffer.py` 做 HTTP 中继（手机 Wi-Fi 里设手动代理，指向你电脑的 `8800` 端口）",
     "- 用 `mitmproxy` / `Charles` 做 HTTP 中继（手机 Wi-Fi 里设手动代理，指向你电脑的 `8800` 端口）"),

    ("App 走不了 F12。用 `tools/sniffer.py` 做 HTTP 中继，或直接上 `mitmproxy` / Charles，",
     "App 走不了 F12。用 `mitmproxy` / `Charles` 做 HTTP 中继，"),

    # 重放模板示例用了错误端点和错误 payload —— 这就是早期把
    # DEFAULT_CONFIG 的 booking 默认值写错的源头。改成实测真实值。
    # 用正则是因为 README 里可能有尾随空格/不同缩进。
    (None, None),
]

# 正则替换：把「请求路径: /ic-web/seat/booking」这一行和紧随的请求体示例整体换掉
REGEX_RULES = [
    (
        r"请求路径:\s*/ic-web/seat/booking\s*\n\s*请求体:\s*\{[^\n]*segmentIds[^\n]*\}",
        '请求路径: /ic-web/reserve/seat\n'
        '请求体:   {"sysKind":8,"appAccNo":{{user_id}},"memberKind":1,"resvMember":[{{user_id}}],\n'
        '           "resvBeginTime":"{{date}} {{start_time}}","resvEndTime":"{{date}} {{end_time}}",\n'
        '           "testName":"","captcha":"","resvProperty":0,"resvDev":[0],'
        '"memo":""}\n'
        '（注意：IC 系的下单体还需要 encryptVlaue / resvDevEncrypts 两个加密字段，\n'
        '  重放模式无法生成它们，只能填抓到的固定值 —— 所以对本系统**不推荐用重放模式**，\n'
        '  直接用内置的 IC 适配器即可。）',
    ),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    raw = open(README, "rb").read()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        print(f"[错误] README 不是合法 UTF-8: {e}")
        return 1

    print("=" * 70)
    print("  修正 README 过时引用")
    print("=" * 70)

    changed = 0
    for old, new in RULES:
        if old is None:
            continue
        n = text.count(old)
        if n == 0:
            print(f"  [跳过] 未找到: {old[:56]}...")
            continue
        text = text.replace(old, new)
        changed += n
        print(f"  [替换 {n} 处] {old[:56]}...")

    # 正则规则（容忍空白差异）
    import re
    for rx, new in REGEX_RULES:
        new_text, n = re.subn(rx, new, text)
        if n == 0:
            print(f"  [跳过] 正则未匹配: {rx[:52]}...")
            continue
        text = new_text
        changed += n
        print(f"  [正则替换 {n} 处] {rx[:52]}...")

    if changed == 0:
        print()
        print("  没有需要修改的内容")
        return 0

    if args.dry_run:
        print()
        print("  (--dry-run) 未写入")
        return 0

    tmp = README + ".tmp"
    with open(tmp, "wb") as f:
        f.write(text.encode("utf-8"))
    os.replace(tmp, README)

    # 回读校验
    back = open(README, "rb").read().decode("utf-8")
    checks = {
        "合法 UTF-8": True,
        "已无 sniffer 引用": "sniffer" not in back,
        "已无中山大学": "中山大学" not in back,
        "保留了 IC 系统章节标题": "IC 空间预约系统 —— 实测确定的完整协议" in back,
        "提到了 mitmproxy": "mitmproxy" in back,
        "已无旧错误端点 seat/booking": "/ic-web/seat/booking" not in back,
        "重放示例用正确端点": "/ic-web/reserve/seat" in back,
    }
    print()
    for k, v in checks.items():
        print(f"  {'OK ' if v else '!! '} {k}")
    bad = [k for k, v in checks.items() if not v]
    print()
    if bad:
        print(f"  ⚠️ 校验未通过: {bad}")
        return 1
    print(f"  ✅ 已更新 ({len(back.encode('utf-8')):,} B)")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
