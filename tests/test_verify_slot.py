# -*- coding: utf-8 -*-
"""验证「预约验证」按钮现在使用界面当前填选的时段（不再强制下午）。

用假服务器提供可解析的 RSA 公钥与座位规则，拦下真实下单请求只看 payload。
用法： python tests/test_verify_slot.py
"""
from __future__ import annotations

import base64
import json
import os
import sys
import threading
import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

# ★ 必须在 import app.* 之前设置！app.config 在**导入时**就按环境变量解析
#   CONFIG_PATH，设晚了就会落到项目目录的 config.json，把用户真实配置写坏。
os.environ["RESERVE_CONFIG"] = os.path.join(os.environ["TEMP"], "verify-slot-test.json")
try:
    os.remove(os.environ["RESERVE_CONFIG"])
except OSError:
    pass

PASS, FAIL = [], []


def _load_rsa_helpers():
    """从 selftest.py 只加载顶部的辅助函数（到 def main() 之前），
    避免执行它的全部测试。"""
    path = os.path.join(ROOT, "tests", "selftest.py")
    src = open(path, encoding="utf-8").read()
    ns = {"__name__": "_helpers_only", "__file__": path}
    exec(compile(src.split("def main()")[0], "selftest_helpers", "exec"), ns)
    return ns


_HELPERS = None


def _gen_rsa(bits=1024):
    global _HELPERS
    if _HELPERS is None:
        _HELPERS = _load_rsa_helpers()
    return _HELPERS["gen_rsa"](bits)


def _pkcs1_der(n, e):
    global _HELPERS
    if _HELPERS is None:
        _HELPERS = _load_rsa_helpers()
    return _HELPERS["pkcs1_der"](n, e)


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {extra}" if extra and not cond else ""))


def main() -> int:
    # 双保险：即使环境变量没生效，也把配置路径改到临时目录
    from app import config as cfgmod
    real_cfg = cfgmod.CONFIG_PATH
    if os.path.abspath(real_cfg).startswith(os.path.abspath(ROOT)):
        cfgmod.CONFIG_PATH = os.environ["RESERVE_CONFIG"]
        print(f"  [保险] 配置路径已重定向: {cfgmod.CONFIG_PATH}")
    print(f"  使用配置: {cfgmod.CONFIG_PATH}")
    print()

    from app import adapters
    from app.httpclient import Client

    n, e, d = _gen_rsa(1024)
    pub_b64 = base64.b64encode(_pkcs1_der(n, e)).decode()
    captured = {}

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
                return self._send({"code": 0, "message": "ok", "data": {
                    "publicKey": pub_b64, "nonceStr": "deadbeef" * 4}})
            if "/ic-web/reserve" in self.path:
                return self._send({"code": 0, "message": "ok", "data": [
                    {"devId": 100495052, "devName": "4F-A001", "roomId": 100494721,
                     "openStart": "07:00", "openEnd": "22:00",
                     "resvRule": {"ruleId": 1, "earliestResvTime": 0,
                                  "minResvTime": 60, "maxResvTime": 300}}]})
            return self._send({"code": 0, "message": "ok", "data": {}})

        def do_POST(self):
            ln = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(ln) if ln else b""
            if "/ic-web/reserve/seat" in self.path:
                captured["body"] = raw.decode("utf-8")
                return self._send({"code": 0, "message": "新增成功",
                                   "data": {"resvId": 1}})
            return self._send({"code": 0, "message": "ok", "data": {}})

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"

    tmr = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()

    def save_cfg(target_extra):
        """保存测试配置。session 必须带 account_hash，
        否则（修复后）程序会因「无法确认 accNo 归属」而重新登录。"""
        cfg_dict = {
            "base_url": base,
            "account": {"username": "100000002", "password": "pw"},
            "target": dict({"space_id": "100494721", "seat_id": "100495052",
                            "reserve_date": tmr}, **target_extra),
            "schedule": {"retry": False},
            "session": {"auto_login": True},
        }
        cfg_dict["session"]["account_hash"] = cfgmod.acct_hash(cfg_dict)
        cfg_dict["session"]["token"] = "TOKEN-TEST"
        cfg_dict["session"]["acc_no"] = 999999999
        cfgmod.save(cfg_dict)
        return cfgmod.load()

    print()
    print("=" * 72)
    print("  1. 自定义时段：预约验证应使用填选的 09:30-11:30")
    print("=" * 72)
    captured.clear()
    cfg = save_cfg({"slot": "", "start_time": "09:30", "end_time": "11:30"})
    ad = adapters.build(cfg, Client(timeout=5), lambda l, m: None)
    ad.apply_session()
    # ★ 这就是「预约验证」现在的行为：不传 slot_override
    ok, msg, info = ad.reserve()
    print(f"  reserve() ok={ok}  msg={str(msg)[:70]}")
    if captured:
        b = json.loads(captured["body"])
        print(f"    resvBeginTime = {b['resvBeginTime']}")
        print(f"    resvEndTime   = {b['resvEndTime']}")
        check("★ 使用当前填选时段 09:30", b["resvBeginTime"] == f"{tmr} 09:30:00")
        check("★ 使用当前填选时段 11:30", b["resvEndTime"] == f"{tmr} 11:30:00")
        check("★ 不再强制下午 13:30", b["resvBeginTime"] != f"{tmr} 13:30:00")
        check("★ 不再强制下午 18:00", b["resvEndTime"] != f"{tmr} 18:00:00")
    else:
        check("构造出下单请求", False, "没抓到请求体")

    print()
    print("=" * 72)
    print("  2. 预设时段：预约验证应使用选中的预设")
    print("=" * 72)
    for slot, want_s, want_e in (("morning", "08:00:00", "12:00:00"),
                                 ("afternoon", "13:30:00", "18:00:00"),
                                 ("evening", "19:00:00", "22:00:00")):
        captured.clear()
        c2 = save_cfg({"slot": slot})
        a2 = adapters.build(c2, Client(timeout=5), lambda l, m: None)
        a2.apply_session()
        a2.reserve()
        if captured:
            b = json.loads(captured["body"])
            got_s = b["resvBeginTime"].split(" ")[1]
            got_e = b["resvEndTime"].split(" ")[1]
            print(f"  {slot:<10} -> {got_s} - {got_e}")
            check(f"{slot} 时段正确", got_s == want_s and got_e == want_e)
        else:
            check(f"{slot} 构造出请求", False)

    # 关键回归：触发入口不应再传 slot_override="afternoon"
    print()
    print("=" * 72)
    print("  3. 回归：/api/verify 不再硬编码 afternoon")
    print("=" * 72)
    src = open(os.path.join(ROOT, "app", "server.py"), encoding="utf-8").read()
    check("server.py 里无 slot_override=\"afternoon\"",
          'slot_override="afternoon"' not in src)
    check("/api/verify 走 trigger_now()",
          'if path == "/api/verify":' in src and "scheduler.trigger_now()" in src)
    html = open(os.path.join(ROOT, "web", "index.html"), encoding="utf-8").read()
    check("HTML 不再写「今天下午 13:30–18:00」", "今天下午 13:30" not in html)
    check("HTML 说明改为「当前填选的时段」", "当前填选的时段" in html)

    srv.shutdown()
    try:
        os.remove(os.environ["RESERVE_CONFIG"])
    except OSError:
        pass

    print()
    print("=" * 72)
    print(f"  结果: {len(PASS)} 通过, {len(FAIL)} 失败")
    for f in FAIL:
        print("   FAIL:", f)
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
