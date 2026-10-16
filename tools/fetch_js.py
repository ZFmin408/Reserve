"""前端 JS 抓取与 RSA 公钥提取（纯标准库）。

用途：登录密码若在前端做 RSA 加密，公钥就写死在 JS 里。
      这个工具把登录页和它引用的 JS 全下下来，把所有疑似 RSA 公钥挖出来，
      并自动判断能不能解析出 (n, e)。

用法：
  python tools\\fetch_js.py                       # 默认 http://10.20.15.27
  python tools\\fetch_js.py http://10.20.15.27
  python tools\\fetch_js.py http://10.20.15.27 --save   # 同时把 JS 存到 logs/js/

输出：
  logs\js-keys.json   挖到的公钥 + 解析结果
  控制台              公钥 + 建议填进 config.login.rsa_public_key 的值
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import crypto  # noqa: E402
from app.httpclient import Client  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 候选入口页
ENTRY_PAGES = ["/", "/index.html", "/ic-web/login", "/login", "/ic-web/"]

# JS 里公钥和内联脚本的匹配模式
KEY_PATTERNS = [
    r"MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQ[A-Za-z0-9+/=]{40,}",   # PKCS#8 base64
    r"MFwwDQYJKoZIhvcNAQEBBQADSwAwSAJBA[A-Za-z0-9+/=]{20,}",        # PKCS#1 512bit
    r"-----BEGIN PUBLIC KEY-----[\s\S]{40,}?-----END PUBLIC KEY-----",
    r"-----BEGIN RSA PUBLIC KEY-----[\s\S]{40,}?-----END RSA PUBLIC KEY-----",
]
SET_KEY_PATTERNS = [
    r"setPublicKey\s*\(\s*['\"]([^'\"]{40,})['\"]",
    r"getPublicKey\s*\(\s*['\"]([^'\"]{40,})['\"]",
    r"publicKey\s*[:=]\s*['\"]([^'\"]{40,})['\"]",
    r"PUBLIC_KEY\s*[:=]\s*['\"]([^'\"]{40,})['\"]",
]
CRYPTO_HINTS = [
    "JSEncrypt", "setPublicKey", "RSAKey", "jsencrypt", "CryptoJS", "AES",
    "encrypt", "md5", "sha256", "sm2", "sm4", "publicKey", "getKey",
]

SCRIPT_SRC_RE = re.compile(r"<script[^>]+src\s*=\s*['\"]([^'\"]+)['\"]", re.I)
INLINE_SCRIPT_RE = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)</script>", re.I)


def fetch(client: Client, url: str) -> str:
    r = client.get(url)
    if r.status == 0:
        print(f"  [x] {url} 网络错误: {r.brief(120)}")
        return ""
    print(f"  [{r.status}] {url}  {len(r.raw)} bytes  {r.headers.get('content-type','')}")
    return r.text if r.status < 400 else ""


def scan_for_keys(text: str, where: str, found: list) -> None:
    for pat in KEY_PATTERNS + SET_KEY_PATTERNS:
        for m in re.finditer(pat, text):
            raw = m.group(1) if m.groups() else m.group(0)
            if raw in [f["key"] for f in found]:
                continue
            parsed = crypto.parse_public_key(raw)
            entry = {
                "where": where,
                "key": raw,
                "parsed": bool(parsed),
                "bits": None,
                "e": None,
            }
            if parsed:
                n, e = parsed
                entry["bits"] = n.bit_length()
                entry["e"] = e
                print(f"  ★ 找到可解析的 RSA 公钥 ({entry['bits']} 位, e={e})  <- {where}")
                print(f"    {raw[:80]}{'...' if len(raw) > 80 else ''}")
            else:
                print(f"  ? 找到疑似公钥串但解析失败  <- {where}: {raw[:60]}...")
            found.append(entry)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("base", nargs="?", default="http://10.20.15.27")
    ap.add_argument("--save", action="store_true", help="把下载的 JS 存到 logs/js/")
    args = ap.parse_args()
    base = args.base.rstrip("/")

    client = Client(timeout=10.0, verify_tls=False)
    client.extra_headers["lan"] = "1"

    print("=" * 78)
    print("  第 1 步：找入口页")
    print("=" * 78)
    entry_html, entry_url = "", ""
    for p in ENTRY_PAGES:
        html = fetch(client, base + p)
        if html and "<html" in html.lower():
            entry_html, entry_url = html, base + p
            break

    if not entry_html:
        print("\n  [!] 没找到 HTML 入口页。直接手动指定一个 JS 地址试试：")
        print(f"      python tools\\fetch_js.py {base}   # 或改 ENTRY_PAGES")
        return

    print(f"\n  入口页: {entry_url}")

    print("\n" + "=" * 78)
    print("  第 2 步：收集脚本")
    print("=" * 78)
    scripts = SCRIPT_SRC_RE.findall(entry_html)
    print(f"  入口页引用 {len(scripts)} 个外部脚本:")
    for s in scripts:
        print(f"    - {s}")

    blobs = [(entry_url, entry_html)]
    save_dir = os.path.join(ROOT, "logs", "js")
    if args.save:
        os.makedirs(save_dir, exist_ok=True)

    for src in scripts:
        if src.startswith("//"):
            url = "http:" + src
        elif src.startswith("http"):
            url = src
        elif src.startswith("/"):
            url = base + src
        else:
            url = base + "/" + src
        text = fetch(client, url)
        if text:
            blobs.append((url, text))
            if args.save:
                safe = re.sub(r"[^A-Za-z0-9._-]", "_", src)
                with open(os.path.join(save_dir, safe), "w", encoding="utf-8", errors="replace") as f:
                    f.write(text)

    # 内联脚本
    for m in INLINE_SCRIPT_RE.finditer(entry_html):
        blobs.append((entry_url + " (inline)", m.group(1)))

    print("\n" + "=" * 78)
    print("  第 3 步：挖 RSA 公钥")
    print("=" * 78)
    found: list = []
    for where, text in blobs:
        scan_for_keys(text, where, found)

    if not found:
        print("  没挖到明显公钥串，扩大搜索：搜所有 base64 长串里的 DER 头")
        for where, text in blobs:
            for m in re.finditer(r"['\"]([A-Za-z0-9+/]{60,}={0,2})['\"]", text):
                parsed = crypto.parse_public_key(m.group(1))
                if parsed:
                    n, e = parsed
                    found.append({"where": where, "key": m.group(1), "parsed": True,
                                  "bits": n.bit_length(), "e": e})
                    print(f"  ★ {where}: {n.bit_length()} 位, e={e}")
                    print(f"    {m.group(1)[:80]}...")

    print("\n" + "=" * 78)
    print("  第 4 步：加密线索统计（判断密码到底怎么处理的）")
    print("=" * 78)
    for hint in CRYPTO_HINTS:
        hits = [(w, t.lower().count(hint.lower())) for w, t in blobs]
        total = sum(c for _, c in hits)
        if total:
            where = ", ".join(w.split("/")[-1] for w, c in hits if c)
            print(f"  {hint:<14} 出现 {total:>4} 次   ({where})")

    print("\n" + "=" * 78)
    print("  结论")
    print("=" * 78)
    good = [f for f in found if f["parsed"]]
    if good:
        print("  可用的 RSA 公钥：")
        for f in good:
            print(f"    {f['bits']} 位, e={f['e']}, 来源 {f['where']}")
            print(f"    {f['key']}")
        print("\n  下一步：把上面的公钥填进界面的 rsa_public_key，pwd_mode 选 rsa_hex，")
        print("          然后点「测试登录」。若还不行就把 pwd_mode 换成 rsa_b64 再试。")
    else:
        print("  没找到 RSA 公钥。")
        print("  可能这套系统密码是用 MD5/SHA 处理的（试 pwd_mode=md5 / sha256），")
        print("  或者公钥在异步 chunk 里（对 JS 文件名里带 chunk/vendor 的再跑一遍）。")

    out = os.path.join(ROOT, "logs", "js-keys.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump({
            "base": base,
            "entry": entry_url,
            "scripts": scripts,
            "keys": found,
            "crypto_hints": {h: sum(t.lower().count(h.lower()) for _, t in blobs) for h in CRYPTO_HINTS},
        }, f, ensure_ascii=False, indent=2)
    print(f"\n  结果已写入: {out}")


if __name__ == "__main__":
    main()
