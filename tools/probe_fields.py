"""登录接口字段名穷举探测器（纯标准库）。

背景：登录返回 code=100「请求参数错误」，说明接口不认我们发的字段名。
     不要靠猜——直接穷举常见字段组合，用返回码把正确答案筛出来。

判定原理：
  - 字段名不对  -> 服务端反序列化后拿不到账号字段 -> 仍然 code=100 / 参数错误
  - 字段名对了  -> 服务端走到业务校验 -> 返回"密码错误/用户不存在"之类的业务码
  所以「code != 100」就是命中信号。

安全：使用假账号 __probe__ / __probe_pwd__，不会触发真实账号的失败计数与锁定。

用法：
  python tools/probe_fields.py
  python tools/probe_fields.py --base http://10.20.15.27
  python tools/probe_fields.py --path /ic-web/login/user
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.httpclient import Client  # noqa: E402

FAKE_USER = "__probe__"
FAKE_PWD = "__probe_pwd__"

# 常见的用户名字段名
USER_FIELDS = [
    "logName", "username", "userName", "loginName", "account", "accountName",
    "name", "userNo", "cardNo", "readerNo", "readerId", "studentNo", "id",
    "loginAccount", "mobile", "phone", "code",
]
# 常见的密码字段名
PWD_FIELDS = [
    "pwd", "password", "passWord", "passwd", "userPwd", "loginPwd",
    "pass", "secret", "loginPassword", "passWord2",
]

PATHS = ["/ic-web/login/user", "/ic-web/login", "/ic-web/user/login", "/ic-web/login/login"]

# 额外必须带的字段（部分版本要求）
EXTRA_BODIES = [
    ("无附加", {}),
    ("+loginType=1", {"loginType": 1}),
    ("+type=1", {"type": 1}),
    ("+lan=1", {"lan": 1}),
    ("+sysType=2", {"sysType": 2}),
]

WRAP_KEYS = [None, "param", "data", "loginDto", "loginParam"]


def classify(status: int, text: str) -> str:
    """给响应打个粗分类，方便肉眼扫。"""
    t = text or ""
    if status == 0:
        return "NET-ERROR"
    if status >= 500:
        return f"HTTP{status}"
    if status == 404:
        return "404-路径不存在"
    if "参数错误" in t or '"code":100' in t or '"code": 100' in t:
        return "参数错误"
    if any(k in t for k in ("密码", "口令")):
        return "密码相关★"
    if any(k in t for k in ("不存在", "未找到", "无此", "not found", "Not Found")):
        return "用户不存在★"
    if any(k in t for k in ("验证码", "captcha")):
        return "要验证码★"
    if any(k in t for k in ("成功", "success", "token")):
        return "疑似成功★"
    return "其他★"


def probe_one(client: Client, url: str, body: dict, wrap) -> tuple:
    payload = {wrap: body} if wrap else dict(body)
    r = client.post_json(url, payload)
    text = r.brief(300)
    return r.status, text, classify(r.status, text)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://10.20.15.27", help="服务地址")
    ap.add_argument("--path", default="", help="只探测指定登录路径")
    ap.add_argument("--top", type=int, default=12, help="最多展示几条命中")
    args = ap.parse_args()

    client = Client(timeout=6.0, verify_tls=False)
    client.set_cookie_string("ic-cookie=00000000-0000-0000-0000-000000000000")
    client.extra_headers["lan"] = "1"

    paths = [args.path] if args.path else PATHS

    print("=" * 78)
    print("  第 1 步：确认登录接口路径")
    print("=" * 78)
    live_paths = []
    for p in paths:
        url = args.base.rstrip("/") + p
        r = client.post_json(url, {"probe": 1})
        cat = classify(r.status, r.brief(200))
        flag = "★ 存在" if r.status not in (0, 404, 405) else "  无"
        print(f"  {flag}  {p:<26} HTTP {r.status:<4} {cat:<12} {r.brief(90)!r}")
        if r.status not in (0, 404, 405):
            live_paths.append(p)
    if not live_paths:
        print("\n  [!] 所有候选路径都不通。可能接口路径完全不同，")
        print("      请把浏览器 F12 里登录那条请求的完整 URL 发我。")
        return

    print(f"\n  可用路径: {live_paths}")

    print("\n" + "=" * 78)
    print("  第 2 步：穷举字段名（用假账号，不影响真实账号）")
    print("=" * 78)

    hits = []
    total = 0
    for path in live_paths:
        url = args.base.rstrip("/") + path
        # 先跑不带附加字段的组合，命中后再补测附加字段
        for uf, pf in itertools.product(USER_FIELDS, PWD_FIELDS):
            total += 1
            status, text, cat = probe_one(client, url, {uf: FAKE_USER, pf: FAKE_PWD}, None)
            if "★" in cat:
                hits.append((path, uf, pf, None, "无附加", status, cat, text))
                print(f"  ★ {path}  {uf} / {pf}  HTTP {status}  [{cat}]")
                print(f"      {text[:200]}")
            time.sleep(0.02)

    print(f"\n  共试了 {total} 种组合，命中 {len(hits)} 条。")

    if not hits:
        print("\n  [!] 没有任何组合命中「非参数错误」。说明问题不在字段名：")
        print("      · 请求体不是 JSON 而是 form-urlencoded（x-www-form-urlencoded）")
        print("      · 或者字段在 URL query 上（POST /login?logName=..&pwd=..）")
        print("      · 或者密码需要先加密（MD5/SHA256/RSA）后再传")
        print("      · 或者必须带某个固定头（Referer / Origin / 自定义签名）")
        print("      下一步：把 F12 里那条登录请求 Copy as cURL 贴出来，一次就能定死。")
        return

    # 对命中的组合补测附加字段与包装层
    print("\n" + "=" * 78)
    print("  第 3 步：为命中组合补测附加字段 / 包装层")
    print("=" * 78)
    best = hits[0]
    path, uf, pf = best[0], best[1], best[2]
    url = args.base.rstrip("/") + path

    for label, extra in EXTRA_BODIES:
        body = {uf: FAKE_USER, pf: FAKE_PWD}
        body.update(extra)
        status, text, cat = probe_one(client, url, body, None)
        print(f"  {label:<16} HTTP {status:<4} [{cat}]  {text[:150]!r}")

    print()
    for wrap in WRAP_KEYS:
        if wrap is None:
            continue
        status, text, cat = probe_one(client, url, {uf: FAKE_USER, pf: FAKE_PWD}, wrap)
        label = '{"' + wrap + '":{...}}'
        print(f"  包装 {label:<22} HTTP {status:<4} [{cat}]  {text[:150]!r}")

    print("\n" + "=" * 78)
    print("  结论")
    print("=" * 78)
    print(f"  登录路径 : {path}")
    print(f"  账号字段 : {uf}")
    print(f"  密码字段 : {pf}")
    print(f"  把这三行填进 config.json 的 paths.login 和 fields.login_user / fields.login_pass")
    print("  （或直接把这三行发我，我改配置。）")

    out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs", "probe-fields.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump({
            "base": args.base,
            "live_paths": live_paths,
            "hits": [{"path": h[0], "user_field": h[1], "pwd_field": h[2],
                      "status": h[5], "class": h[6], "body": h[7]} for h in hits],
        }, f, ensure_ascii=False, indent=2)
    print(f"\n  完整结果已写入: {out}")


if __name__ == "__main__":
    main()
