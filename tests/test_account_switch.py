# -*- coding: utf-8 -*-
"""验证「改了账号密码却仍用旧账号预约」的修复。

三层修复：
  1. config.save：账号指纹（地址/学号/密码）变化 -> 清空 session 的 token/accNo
  2. adapters.login：session 里存的 account_hash 与当前账号不符 -> 丢弃旧 token 重登
  3. config.save：account 里没传的字段（尤其 password）保留旧值

全程用假服务器，不碰真实账号，也不访问真实服务器。
用法： python tests/test_account_switch.py
"""
from __future__ import annotations

import base64
import importlib.util
import json
import os
import sys
import threading
import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

_st = None
PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {extra}" if extra and not cond else ""))


def main() -> int:
    # 隔离配置，避免污染真实 config.json
    os.environ["RESERVE_CONFIG"] = os.path.join(os.environ["TEMP"], "acct-switch-test.json")
    try:
        os.remove(os.environ["RESERVE_CONFIG"])
    except OSError:
        pass

    from app import config as cfgmod, adapters
    from app.httpclient import Client

    # 复用 selftest 的 RSA 工具（不执行其测试）
    spec = importlib.util.spec_from_file_location("_st", os.path.join(ROOT, "tests", "selftest.py"))
    st = importlib.util.module_from_spec(spec)
    st.__name__ = "_st"
    src = open(os.path.join(ROOT, "tests", "selftest.py"), encoding="utf-8").read()
    ns: dict = {"__name__": "_helpers_only",
                "__file__": os.path.join(ROOT, "tests", "selftest.py")}
    exec(compile(src.split("def main()")[0], "selftest_helpers", "exec"), ns)
    n, e, d = ns["gen_rsa"](1024)
    pub_b64 = base64.b64encode(ns["pkcs1_der"](n, e)).decode()

    logins = []          # 记录每次真正调用的登录
    bookings = []        # 记录下单 payload

    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def _send(self, obj):
            b = json.dumps(obj, ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json;charset=UTF-8")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def do_GET(self):
            if "login/publicKey" in self.path:
                return self._send({"code": 0, "data": {
                    "publicKey": pub_b64, "nonceStr": "abcd1234" * 4}})
            if "/ic-web/reserve" in self.path:
                return self._send({"code": 0, "data": [
                    {"devId": 100495052, "devName": "4F-A001", "roomId": 100494721,
                     "resvRule": {"ruleId": 1, "earliestResvTime": 0,
                                  "minResvTime": 60, "maxResvTime": 300}}]})
            if "room/getAll" in self.path:
                return self._send({"code": 0, "data": [{"roomId": 1, "roomName": "二楼A区"}]})
            return self._send({"code": 0, "data": {}})

        def do_POST(self):
            ln = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(ln) if ln else b""
            if "login/user" in self.path:
                body = json.loads(raw.decode() or "{}")
                user = body.get("logonName") or ""
                logins.append(user)
                # 每个学号分配不同的 accNo
                acc = {"100000001": 900000001, "100000002": 900000002}.get(user, 900000099)
                return self._send({"code": 0, "message": "登录成功",
                                   "data": {"token": f"TOKEN-{user}", "accNo": acc}})
            if "reserve/seat" in self.path:
                bookings.append(json.loads(raw.decode() or "{}"))
                return self._send({"code": 0, "message": "新增成功", "data": {"resvId": 7}})
            return self._send({"code": 0, "data": {}})

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    tmr = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()

    def fresh(user, pw):
        cfgmod.save({
            "base_url": base,
            "account": {"username": user, "password": pw},
            "target": {"space_id": "100494721", "seat_id": "100495052", "slot": "morning",
                       "reserve_date": tmr},
            "schedule": {"retry": False},
            "session": {"auto_login": True},
        })
        return cfgmod.load()

    print()
    print("=" * 74)
    print("  1. 账号 A 登录后，session 存下了 token 与 accNo")
    print("=" * 74)
    cfgA = fresh("100000001", "pwA")
    aA = adapters.build(cfgA, Client(timeout=5), lambda l, m: None)
    aA.apply_session()
    okA, msgA, dtA = aA.login(force=True)
    print(f"  登录 ok={okA} accNo={(dtA or {}).get('accNo')}")
    cfgmod.save({"session": {"token": (dtA or {}).get("token") or "",
                             "acc_no": (dtA or {}).get("accNo")}})
    cfgA = cfgmod.load()
    check("A 的 token 已保存", bool((cfgA.get("session") or {}).get("token")))
    check("A 的 accNo 已保存", (cfgA.get("session") or {}).get("acc_no") == 900000001,
          str((cfgA.get("session") or {}).get("acc_no")))
    check("A 的账号指纹已写入", bool((cfgA.get("session") or {}).get("account_hash")))

    print()
    print("=" * 74)
    print("  2. ★ 改账号后：token / accNo / 指纹 应被立刻清空")
    print("=" * 74)
    cfgmod.save({"account": {"username": "100000002", "password": "pwB"}})
    cfgB = cfgmod.load()
    sB = cfgB.get("session") or {}
    print(f"  改后 session.token = {sB.get('token')!r}")
    print(f"  改后 session.acc_no = {sB.get('acc_no')!r}")
    check("★ 旧 token 已被清空", not sB.get("token"), repr(sB.get("token")))
    check("★ 旧 accNo 已被清空", sB.get("acc_no") is None, repr(sB.get("acc_no")))
    check("账号指纹已更新为新账号", sB.get("account_hash") != (cfgA.get("session") or {}).get("account_hash"))

    print()
    print("=" * 74)
    print("  3. ★ 用新账号下单：appAccNo 必须是新账号的")
    print("=" * 74)
    logins.clear()
    bookings.clear()
    aB = adapters.build(cfgB, Client(timeout=5), lambda l, m: None)
    aB.apply_session()
    okB, msgB, infoB = aB.reserve()
    print(f"  reserve ok={okB}  调用登录的学号={logins}")
    if bookings:
        b = bookings[-1]
        print(f"    appAccNo   = {b.get('appAccNo')}")
        print(f"    resvMember = {b.get('resvMember')}")
        check("★ appAccNo 是新账号的 900000002", b.get("appAccNo") == 900000002,
              str(b.get("appAccNo")))
        check("★ resvMember 是新账号的", b.get("resvMember") == [900000002],
              str(b.get("resvMember")))
    else:
        check("构造出下单请求", False)
    check("★ 确实重新登录过（没有复用旧 token）", "100000002" in logins, str(logins))

    print()
    print("=" * 74)
    print("  4. 只改密码：也应清空会话并重登")
    print("=" * 74)
    aC = adapters.build(cfgmod.load(), Client(timeout=5), lambda l, m: None)
    aC.apply_session()
    aC.login(force=True)
    cfgmod.save({"session": {"token": "TOKEN-100000002", "acc_no": 900000002}})
    before_pw = (cfgmod.load().get("account") or {}).get("password")
    cfgmod.save({"account": {"password": "pwB2"}})
    after_pw = (cfgmod.load().get("account") or {}).get("password")
    sC = cfgmod.load().get("session") or {}
    print(f"  密码 {before_pw!r} -> {after_pw!r}")
    print(f"  session.token = {sC.get('token')!r}")
    check("密码已更新", after_pw == "pwB2")
    check("★ 改密码后旧 token 被清空", not sC.get("token"), repr(sC.get("token")))

    print()
    print("=" * 74)
    print("  5. account 只传 username 时，密码不能被清掉")
    print("=" * 74)
    pw_now = (cfgmod.load().get("account") or {}).get("password")
    cfgmod.save({"account": {"username": "100000002"}})
    pw_after = (cfgmod.load().get("account") or {}).get("password")
    check("★ 未传的密码字段保留旧值", pw_after == pw_now, f"{pw_now!r} -> {pw_after!r}")

    print()
    print("=" * 74)
    print("  6. 老配置（session 无 account_hash）必须重登，不能串号")
    print("=" * 74)
    c = cfgmod.load()
    c["session"] = {"auto_login": True, "token": "STALE-TOKEN", "acc_no": 900000002}
    c["session"].pop("account_hash", None)
    with open(os.environ["RESERVE_CONFIG"], "w", encoding="utf-8") as f:
        json.dump(c, f, ensure_ascii=False, indent=2)
    cfgmod.reload()
    logins.clear()
    bookings.clear()
    aD = adapters.build(cfgmod.load(), Client(timeout=5), lambda l, m: None)
    aD.apply_session()
    okD, msgD, _ = aD.reserve()
    print(f"  reserve ok={okD}  调用的登录={logins}")
    check("★ 没有 account_hash 时会真正登录", len(logins) > 0, str(logins))
    check("★ 不是复用 STALE-TOKEN",
          "STALE-TOKEN" not in str(aD.client.extra_headers.get("token")))

    srv.shutdown()
    try:
        os.remove(os.environ["RESERVE_CONFIG"])
    except OSError:
        pass

    print()
    print("=" * 74)
    print(f"  结果: {len(PASS)} 通过, {len(FAIL)} 失败")
    for f in FAIL:
        print("   FAIL:", f)
    print("=" * 74)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
