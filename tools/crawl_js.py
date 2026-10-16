"""把 SPA 的全部 JS chunk 爬下来挖 RSA 公钥 / 加密逻辑（纯标准库）。

适用：Vue CLI / webpack 打包的前端（页面只引用 chunk-vendors + chunk-common + index，
     真正的登录页和加密逻辑散在各个 chunk 里）。

用法：
  python tools\\crawl_js.py                          # 默认 http://10.20.15.27
  python tools\\crawl_js.py http://10.20.15.27
  python tools\\crawl_js.py --max 200                 # 最多下多少个文件

输出：
  logs\\crawl-keys.json    挖到的公钥
  logs\\crawl-report.txt   每个 chunk 的加密线索统计
  logs\\js\\*.js           下载的原始 JS（便于进一步分析）
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

# 从 JS/HTML 里抠文件引用
JS_REF_RE = re.compile(r"""['"]([^'"]*?chunk[^'"]*?\.js)['"]""", re.I)
ANY_JS_REF_RE = re.compile(r"""['"]([^'"]*?\.js)['"]""", re.I)
HREF_JS_RE = re.compile(r"""href=([^\s>]*?\.js)""", re.I)
SRC_JS_RE = re.compile(r"""src=['"]?([^\s>]*?\.js)""", re.I)

KEY_RES = [
    re.compile(r"MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQ[A-Za-z0-9+/=]{40,}"),
    re.compile(r"MFwwDQYJKoZIhvcNAQEBBQADSwAwSAJBA[A-Za-z0-9+/=]{20,}"),
    re.compile(r"-----BEGIN (?:RSA )?PUBLIC KEY-----[\s\S]{40,}?-----END (?:RSA )?PUBLIC KEY-----"),
]
SETKEY_RES = [
    re.compile(r"""setPublicKey\s*\(\s*['"]([^'"]{40,})['"]"""),
    re.compile(r"""(?:publicKey|PUBLIC_KEY|public_key)\s*[:=]\s*['"]([^'"]{40,})['"]"""),
    re.compile(r"""getKey\s*\(\s*\)\s*\{[^}]{0,200}?['"]([A-Za-z0-9+/]{60,}={0,2})['"]"""),
]
B64_RE = re.compile(r"""['"]([A-Za-z0-9+/]{80,}={0,2})['"]""")

CRYPTO_HINTS = [
    "JSEncrypt", "jsencrypt", "setPublicKey", "RSAKey", "rsa", "CryptoJS",
    "AES", "DES", "sm2", "sm4", "md5", "sha1", "sha256", "encrypt", "sign",
    "logonName", "consoleType", "captcha",
]


def norm(base: str, ref: str) -> str:
    ref = ref.strip().strip("'\"")
    if ref.startswith("//"):
        return "http:" + ref
    if ref.startswith("http"):
        return ref
    if ref.startswith("/"):
        return base + ref
    if ref.startswith("./"):
        ref = ref[2:]
    return base + "/" + ref


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("base", nargs="?", default="http://10.20.15.27")
    ap.add_argument("--max", type=int, default=160)
    args = ap.parse_args()
    base = args.base.rstrip("/")

    client = Client(timeout=12.0, verify_tls=False)
    client.extra_headers["lan"] = "1"

    js_dir = os.path.join(ROOT, "logs", "js")
    os.makedirs(js_dir, exist_ok=True)

    print("=" * 78)
    print("  第 1 步：下入口页，收集 JS 引用")
    print("=" * 78)
    entries = ["/", "/index.html"]
    refs: set = set()
    for e in entries:
        r = client.get(base + e)
        if r.status != 200:
            continue
        html = r.text
        for rx in (HREF_JS_RE, SRC_JS_RE, JS_REF_RE, ANY_JS_REF_RE):
            for m in rx.finditer(html):
                refs.add(norm(base, m.group(1)))
    print(f"  入口页收集到 {len(refs)} 个 JS 引用")

    # 递归：下每个 JS，再从里面抠新的 chunk 名
    seen: set = set()
    blobs: list = []
    queue = sorted(refs)
    while queue and len(blobs) < args.max:
        url = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        r = client.get(url)
        if r.status != 200 or not r.raw:
            continue
        text = r.text
        blobs.append((url, text))
        name = url.split("/")[-1].split("?")[0] or "index.js"
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", name)
        with open(os.path.join(js_dir, safe), "w", encoding="utf-8", errors="replace") as f:
            f.write(text)
        # 继续找新引用
        for rx in (JS_REF_RE, ANY_JS_REF_RE):
            for m in rx.finditer(text):
                cand = norm(base, m.group(1))
                if cand not in seen and cand.startswith(base):
                    queue.append(cand)
        if len(blobs) % 20 == 0:
            print(f"  已下 {len(blobs)} 个文件, 队列 {len(queue)} ...")

    total_bytes = sum(len(t) for _, t in blobs)
    print(f"\n  共下载 {len(blobs)} 个 JS, {total_bytes/1024:.0f} KB")

    print()
    print("=" * 78)
    print("  第 2 步：挖 RSA 公钥")
    print("=" * 78)
    found: list = []
    for url, text in blobs:
        hits_here = 0
        for rx in KEY_RES:
            for m in rx.finditer(text):
                raw = m.group(0)
                if any(f["key"] == raw for f in found):
                    continue
                parsed = crypto.parse_public_key(raw)
                item = {"where": url, "key": raw,
                        "bits": parsed[0].bit_length() if parsed else None,
                        "e": parsed[1] if parsed else None, "parsed": bool(parsed)}
                found.append(item)
                hits_here += 1
                flag = f"{item['bits']} 位, e={item['e']}" if parsed else "解析失败"
                print(f"  ★ {url.split('/')[-1]}: {flag}")
        if not hits_here:
            for rx in SETKEY_RES:
                for m in rx.finditer(text):
                    raw = m.group(1)
                    if any(f["key"] == raw for f in found):
                        continue
                    parsed = crypto.parse_public_key(raw)
                    item = {"where": url, "key": raw,
                            "bits": parsed[0].bit_length() if parsed else None,
                            "e": parsed[1] if parsed else None, "parsed": bool(parsed)}
                    found.append(item)
                    flag = f"{item['bits']} 位, e={item['e']}" if parsed else "解析失败"
                    print(f"  ★ {url.split('/')[-1]} (setPublicKey): {flag}")

    if not found:
        print("  模式匹配没中，扫所有长 base64 串试着解析 DER")
        for url, text in blobs:
            for m in B64_RE.finditer(text):
                raw = m.group(1)
                parsed = crypto.parse_public_key(raw)
                if parsed:
                    n, e = parsed
                    found.append({"where": url, "key": raw, "bits": n.bit_length(),
                                  "e": e, "parsed": True})
                    print(f"  ★ {url.split('/')[-1]}: {n.bit_length()} 位, e={e}")

    print()
    print("=" * 78)
    print("  第 3 步：加密线索定位（哪个 chunk 管登录）")
    print("=" * 78)
    ranking = []
    for url, text in blobs:
        score = 0
        detail = []
        for h in CRYPTO_HINTS:
            n = text.lower().count(h.lower())
            if n:
                score += n
                detail.append(f"{h}:{n}")
        if score:
            ranking.append((score, url, detail))
    ranking.sort(reverse=True)
    for score, url, detail in ranking[:15]:
        print(f"  [{score:>5}] {url.split('/')[-1]:<42} {', '.join(detail[:8])}")

    print()
    print("=" * 78)
    print("  结论")
    print("=" * 78)
    good = [f for f in found if f["parsed"]]
    if good:
        for f in good:
            print(f"  {f['bits']} 位 RSA, e={f['e']}, 来源 {f['where'].split('/')[-1]}")
            print(f"  {f['key']}")
        print("\n  把上面公钥填进界面 rsa_public_key，pwd_mode=rsa_b64，点「测试登录」。")
    else:
        print("  仍未找到公钥。把 logs\\crawl-report.txt 发我，我人工看加密逻辑。")

    report = os.path.join(ROOT, "logs", "crawl-report.txt")
    with open(report, "w", encoding="utf-8") as f:
        f.write(f"base: {base}\n文件数: {len(blobs)}  总字节: {total_bytes}\n\n")
        f.write("=== 公钥候选 ===\n")
        for x in found:
            f.write(json.dumps(x, ensure_ascii=False) + "\n")
        f.write("\n=== chunk 加密线索排名 ===\n")
        for score, url, detail in ranking:
            f.write(f"[{score}] {url}\n    {', '.join(detail)}\n")
        f.write("\n=== 文件名清单 ===\n")
        for url, _ in blobs:
            f.write(url + "\n")
    with open(os.path.join(ROOT, "logs", "crawl-keys.json"), "w", encoding="utf-8") as f:
        json.dump({"base": base, "keys": found,
                   "ranking": [{"score": s, "url": u, "detail": d} for s, u, d in ranking]},
                  f, ensure_ascii=False, indent=2)
    print(f"\n  报告: {report}")


if __name__ == "__main__":
    main()
