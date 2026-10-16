"""登录后查询场馆 / 开放时段 / 账号信息（纯标准库）。

用法：
  python tools\\list_rooms.py                 # 列出全部场馆
  python tools\\list_rooms.py --sys-kind 1    # 指定 sysKind（座位=1，设备外借=8）
  python tools\\list_rooms.py --room 100492994 --date 2026-10-16   # 查某场馆开放时段
  python tools\\list_rooms.py --info          # 只看账号信息
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import adapters, config as cfgmod  # noqa: E402
from app.httpclient import Client  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sys-kind", type=int, default=1, help="1=座位, 8=设备外借")
    ap.add_argument("--room", type=int, default=0)
    ap.add_argument("--date", default="")
    ap.add_argument("--info", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    cfg = cfgmod.load()
    if not cfg["account"]["username"] or not cfg["account"]["password"]:
        print("config.json 里没有账号密码，先在界面填好。")
        return

    cli = Client(timeout=12, verify_tls=False)
    ad = adapters.build(cfg, cli, lambda l, m: print(f"[{l}] {m}"))
    ad.apply_session()

    print("=" * 70)
    print("  登录")
    print("=" * 70)
    ok, msg, d = ad.login()
    if not ok:
        print("  登录失败:", msg)
        return
    print("  token =", d.get("token"))

    B = ad.base_url
    day = args.date or datetime.date.today().isoformat()

    if args.info or not args.room:
        r = cli.get(B + "/ic-web/account/info")
        j = r.json() or {}
        data = j.get("data") or {}
        if data:
            print()
            print("=" * 70)
            print("  账号信息")
            print("=" * 70)
            for k in ("accNo", "logonName", "pid", "cardNo", "trueName", "className",
                      "deptName", "kind", "status", "balance", "creditStar"):
                if data.get(k) not in (None, ""):
                    print(f"  {k:<12} = {data[k]}")
            print()
            print(f"  ★ accNo = {data.get('accNo')}   （下单 payload 里的 appAccNo / resvMember 用它）")

    if args.room:
        r = cli.get(B + f"/ic-web/seatRoom/openScope?roomId={args.room}&resvDate={day}")
        j = r.json() or {}
        print()
        print("=" * 70)
        print(f"  场馆 {args.room} 在 {day} 的开放时段")
        print("=" * 70)
        for item in (j.get("data") or []):
            wk = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][item.get("dayOfWeek", 0) % 7]
            ft = item.get("firstTime") or []
            print(f"  {wk}  {ft[0] if ft else '?'} - {ft[1] if len(ft) > 1 else '?'}")
        return

    # 列场馆
    r = cli.get(B + f"/ic-web/room/getAll?sysKind={args.sys_kind}")
    j = r.json() or {}
    rooms = j.get("data") or []
    print()
    print("=" * 70)
    print(f"  场馆列表 (sysKind={args.sys_kind})  共 {len(rooms)} 个")
    print("=" * 70)
    print(f"  {'roomId':<14} {'roomSn':<8} roomName")
    for rm in rooms:
        print(f"  {str(rm.get('roomId')):<14} {str(rm.get('roomSn') or ''):<8} {rm.get('roomName')}")

    if args.json:
        out = os.path.join(ROOT, "logs", "rooms.json")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            json.dump({"sysKind": args.sys_kind, "rooms": rooms}, f, ensure_ascii=False, indent=2)
        print(f"\n  已写入 {out}")

    print()
    print("  下一步：python tools\\list_rooms.py --room <roomId>  看开放时段")
    print("         然后把 seatId（座位）从座位图请求里取到，填进 config.json")


if __name__ == "__main__":
    main()
