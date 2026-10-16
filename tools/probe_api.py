"""接口发现：登录后带 token 探测座位/预约相关端点（纯标准库）。

用法：
  python tools\\probe_api.py                    # 默认读 config.json
  python tools\\probe_api.py --user X --pass Y
  python tools\\probe_api.py --grep seat        # 只测名字含 seat 的端点

输出：控制台表格 + logs\\api-probe.txt
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import adapters, config as cfgmod  # noqa: E402
from app.httpclient import Client  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 候选端点（GET，带 token）
CANDIDATES = [
    "/ic-web/seat/freeSeat",
    "/ic-web/seat/list",
    "/ic-web/seat/seatList",
    "/ic-web/seat/info",
    "/ic-web/seat/booking",
    "/ic-web/seat/myBooking",
    "/ic-web/seat/bookingList",
    "/ic-web/seat/reservation",
    "/ic-web/space/list",
    "/ic-web/space/info",
    "/ic-web/space/spaceList",
    "/ic-web/space/seatList",
    "/ic-web/reserve/seat",
    "/ic-web/reserve/list",
    "/ic-web/reservation/list",
    "/ic-web/user/info",
    "/ic-web/user/getUserInfo",
    "/ic-web/sysConfig/list",
    "/ic-web/config/list",
    "/ic-web/build/list",
    "/ic-web/floor/list",
    "/ic-web/room/list",
    "/ic-web/area/list",
    "/login/publicKey",
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--user", default="")
    ap.add_argument("--pass", dest="pwd", default="")
    ap.add_argument("--grep", default="")
    ap.add_argument("--write", action="store_true", help="把发现的路径写回 config.json")
    args = ap.parse_args()

    cfg = cfgmod.load()
    if args.user:
        cfg["account"]["username"] = args.user
    if args.pwd:
        cfg["account"]["password"] = args.pwd

    if not cfg["account"]["username"] or not cfg["account"]["password"]:
        print("缺账号密码。用 --user/--pass，或先在 config.json 里填。")
        return

    cli = Client(timeout=10, verify_tls=False)
    ad = adapters.build(cfg, cli, lambda l, m: print(f"[{l}] {m}"))
    ad.apply_session()

    print("=" * 78)
    print("  第 1 步：登录")
    print("=" * 78)
    ok, msg, d = ad.login()
    if not ok:
        print(f"  登录失败: {msg}")
        return
    token = d.get("token") or ""
    uid = d.get("user_id")
    print(f"  token={token}")
    print(f"  accNo/userId 候选={uid}")

    print()
    print("=" * 78)
    print("  第 2 步：带 token 探测端点")
    print("=" * 78)

    items = [p for p in CANDIDATES if args.grep.lower() in p.lower()] if args.grep else CANDIDATES
    results = []
    for p in items:
        url = ad.url(p)
        r = cli.get(url)
        body = r.brief(220).replace("\n", " ")
        flag = ""
        if r.status == 200 and '"code":0' in r.text:
            flag = "★ 可用"
        elif r.status == 200:
            flag = "  code非0"
        elif r.status == 404:
            flag = "  404"
        else:
            flag = f"  HTTP{r.status}"
        # 200 且不是「未登录/无权限」的，算候选
        if r.status == 200 and any(k in r.text for k in ("未登录", "token", "请先")):
            flag += "(需登录?)"
        print(f"{flag:<14} {p}")
        if r.status == 200 and '"code":0' in r.text:
            print(f"               {body}")
        results.append({"path": p, "status": r.status, "flag": flag.strip(), "brief": body})

    print()
    print("=" * 78)
    print("  结论")
    print("=" * 78)
    live = [x for x in results if "可用" in x["flag"]]
    if live:
        for x in live:
            print(f"  {x['path']}  ->  {x['brief'][:120]}")
    else:
        print("  没有端点直接返回 code:0。可能都需要额外查询参数（如 spaceId/date），")
        print("  或者路径不同 —— 请用 F12 抓一次「进入座位预约页」的请求，把 URL 发我。")

    out = os.path.join(ROOT, "logs", "api-probe.txt")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(f"token={token}\nuser_id={uid}\n\n")
        for x in results:
            f.write(f"{x['flag']:<14} {x['path']}\n    {x['brief']}\n")
    print(f"\n  报告: {out}")


if __name__ == "__main__":
    main()
