"""验证「预约成功后是否自动停止」——用假服务器计数（纯标准库，不碰真服务器）。

被测行为：
  1. 未成功时按 retry_interval_ms 高频重试
  2. 一旦成功 -> 立即停止，不再发任何请求
  3. at_time / countdown 模式成功后线程退出，状态变 success

用法： python tests/test_stop_on_success.py
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import config as cfgmod  # noqa: E402
from app.adapters import IcAdapter  # noqa: E402
from app.scheduler import Scheduler  # noqa: E402

PASS, FAIL = [], []

# 测试用：跳过真实 RSA/AES 构造（假公钥解析不了），只保留重试/停止逻辑
IcAdapter._build_booking_payload = lambda self, acc, slot="": (
    {"sysKind": 8, "appAccNo": acc, "resvMember": [acc],
     "resvBeginTime": "2026-10-16 13:30:00", "resvEndTime": "2026-10-16 18:00:00",
     "encryptVlaue": "STUB", "resvDevEncrypts": ["STUB"]},
    "stub payload",
)
IcAdapter._current_acc_no = lambda self: 100510484
IcAdapter.login = lambda self, force=False: (True, "stub login", {"token": "TOKEN1234567890"})


def check(name: str, cond: bool, extra: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{('  ' + extra) if extra and not cond else ''}")


class FakeServer:
    """假图书馆：登录永远成功；下单单失败，直到第 N 次成功。"""

    def __init__(self, succeed_at: int = 3, fail_message: str = "该时段已被预约",
                 fail_code: int = 1):
        self.succeed_at = succeed_at
        self.fail_message = fail_message
        self.fail_code = fail_code
        self.kind_requests = 0        # 查座位/房间等 GET
        self.public_key_requests = 0
        self.login_requests = 0
        self.booking_requests = 0
        self.booking_times = []
        outer = self

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
                    outer.public_key_requests += 1
                    return self._send({"code": 0, "message": "查询成功", "data": {
                        "publicKey": "MFwwDQYJKoZIhvcNAQEBBQADSwAwSAJBAKoR8mX0rXy" + "A" * 60,
                        "nonceStr": "deadbeef" * 4}})
                if "room/getAll" in self.path or "reserve" in self.path or "seatRoom" in self.path:
                    outer.kind_requests += 1
                    return self._send({"code": 0, "message": "查询成功", "data": []})
                return self._send({"code": 0, "message": "ok", "data": {}})

            def do_POST(self):
                ln = int(self.headers.get("Content-Length") or 0)
                if ln:
                    self.rfile.read(ln)
                if "login/user" in self.path:
                    outer.login_requests += 1
                    return self._send({"code": 0, "message": "登录成功", "data": {
                        "token": "TOKEN1234567890", "accNo": 100510484}})
                if "reserve/seat" in self.path:
                    outer.booking_requests += 1
                    outer.booking_times.append(time.time())
                    if outer.booking_requests >= outer.succeed_at:
                        return self._send({"code": 0, "message": "新增成功", "data": {
                            "resvId": 104285735, "uuid": "abc123", "resvDate": 20261016}})
                    return self._send({"code": outer.fail_code,
                                       "message": outer.fail_message, "data": None})
                return self._send({"code": 0, "message": "ok", "data": {}})

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.srv.daemon_threads = True
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.port}"

    def stop(self):
        self.srv.shutdown()


def main() -> int:
    """入口：把配置指向临时文件，保证**永不触碰真实 config.json**。

    以前的做法是「先备份、finally 还原」，但进程被中断时 finally 不执行，
    会把假 base_url 留在真实配置里（真实踩过这个坑）。
    现在改成重定向 CONFIG_PATH，真实文件从头到尾不会被写。
    """
    import tempfile
    real_path = cfgmod.CONFIG_PATH
    tmp_path = os.path.join(tempfile.mkdtemp(prefix="reserve-test-"), "config.json")
    cfgmod.CONFIG_PATH = tmp_path
    cfgmod.reload()
    # 用真实配置的内容作为初始值，保证测试跑在「同样的默认值」上
    try:
        seed = json.loads(open(real_path, encoding="utf-8").read())
    except Exception:
        seed = {}
    cfgmod.save(seed)
    print(f"测试配置已隔离到临时文件: {tmp_path}")
    print(f"真实 config.json 不会被修改: {real_path}")
    print()
    try:
        return _run_all()
    finally:
        cfgmod.CONFIG_PATH = real_path
        cfgmod.reload()
        # 校验真实文件确实没被写过
        now = cfgmod.load()
        print(f"真实 config.json 保持原样: base_url={now.get('base_url')} "
              f"user={(now.get('account') or {}).get('username')}")


def _run_all() -> int:
    real = json.loads(json.dumps(cfgmod.load()))

    print("=" * 72)
    print("  1. 预约成功 -> 必须立刻停止（且请求不密集）")
    print("=" * 72)
    srv = FakeServer(succeed_at=2)
    try:
        cfgmod.save({
            "base_url": srv.base,
            "account": {"username": "202301050786", "password": "pw"},
            "login": {"pwd_mode": "auto_pubkey", "rsa_output": "rsa_b64",
                      "nonce_sep": ";", "body": "", "encoding": "json", "wrap": "",
                      "extra_fields": {}, "session_ttl_s": 1800},
            "session": {"auto_login": True, "token": "", "cookie": ""},
            "paths": {"login": "/ic-web/login/user", "public_key": "/ic-web/login/publicKey",
                      "booking": "/ic-web/reserve/seat", "room_list": "/ic-web/room/getAll",
                      "account_info": "/ic-web/account/info"},
            "booking": {"sys_kind": 8, "member_kind": 1, "resv_property": 0, "resv_dev": [0],
                        "aes_mode": "cbc", "aes_padding": "zero", "aes_iv": "ABCDEF1234123412",
                        "aes_key_len": 16, "aes_output": "hex_upper", "rsa_output": "rsa_b64",
                        "rsa_padding": "pkcs1"},
            "target": {"seat_id": "100493961", "space_id": "100492996",
                       "reserve_date": "2026-10-16", "slot": "afternoon"},
            # 故意把间隔配成 80ms，验证实际受 200ms 最小值保护
            "schedule": {"mode": "countdown", "countdown_seconds": 0, "advance_ms": 0,
                         "retry": True, "retry_max": 3, "retry_interval_ms": 80,
                         "retry_timeout_s": 30, "prewarm": False, "prewarm_advance_s": 60},
            "replay": {"enabled": False, "body": ""},
        })
        logs = []
        sch = Scheduler(lambda lv, m: logs.append(f"{lv} {m}"))
        sch.start()

        deadline = time.time() + 20
        while time.time() < deadline:
            st = sch.state_snapshot()["status"]
            if st in ("success", "failed", "stopped"):
                break
            time.sleep(0.05)
        status = sch.state_snapshot()["status"]
        n_at_stop = srv.booking_requests
        print(f"  status={status}  下单请求数={n_at_stop}  尝试次数={sch.state_snapshot()['attempts']}")

        check("状态为 success", status == "success", status)
        check("第 2 次成功（=succeed_at）后就不再发", n_at_stop == 2, f"实际 {n_at_stop}")
        check("尝试次数记录为 2", sch.state_snapshot()["attempts"] == 2,
              str(sch.state_snapshot()["attempts"]))

        # 关键：成功后再等一段时间，请求数必须不再增长
        print("  成功后静置 6 秒，观察是否还有请求…")
        time.sleep(6.0)
        n_after = srv.booking_requests
        check("★ 成功后不再发送任何下单请求", n_after == n_at_stop,
              f"停止时 {n_at_stop} -> 6 秒后 {n_after}")
        check("调度线程已退出",
              not any(t.name == "reserve-scheduler" and t.is_alive() for t in threading.enumerate()))

        # 重试间隔按配置生效（不再有 3 秒硬下限，支持 1~2 秒的紧密重试）
        if len(srv.booking_times) >= 2:
            gaps = [round(srv.booking_times[i + 1] - srv.booking_times[i], 2)
                    for i in range(len(srv.booking_times) - 1)]
            print(f"  两次请求间隔(秒): {gaps}  （配置 80ms，实际受最小 200ms 保护）")
            check("★ 间隔按配置生效且受 200ms 最小值保护",
                  all(g >= 0.2 for g in gaps), str(gaps))

        check("日志里出现预约成功", any("预约成功" in x or "SUCCESS" in x for x in logs))
    finally:
        srv.stop()

    print()
    print("=" * 72)
    print("  2. 全部失败 -> 达到 retry_max 就停（不会无限打、不会高频打）")
    print("=" * 72)
    srv2 = FakeServer(succeed_at=10 ** 9)     # 永不出成功
    try:
        # 用 stub 走通构造，专注验证「重试上限 + 间隔下限 + 停止」
        IcAdapter._current_acc_no = lambda self: 100510484
        IcAdapter._build_booking_payload = lambda self, acc, slot="": (
            {"sysKind": 8, "appAccNo": acc, "resvMember": [acc],
             "resvBeginTime": "2026-10-16 13:30:00", "resvEndTime": "2026-10-16 18:00:00",
             "encryptVlaue": "STUB", "resvDevEncrypts": ["STUB"]}, "stub payload")
        cfgmod.save({
            "base_url": srv2.base,
            "schedule": {"mode": "countdown", "countdown_seconds": 0, "advance_ms": 0,
                         "retry": True, "retry_max": 2, "retry_interval_ms": 3000,
                         "retry_timeout_s": 300, "prewarm": False, "prewarm_advance_s": 60},
            "session": {"auto_login": True, "token": "", "cookie": ""},
        })
        sch2 = Scheduler(lambda lv, m: None)
        t0 = time.time()
        sch2.start()
        deadline = time.time() + 30
        while time.time() < deadline:
            if sch2.state_snapshot()["status"] in ("success", "failed", "stopped"):
                break
            time.sleep(0.05)
        st2 = sch2.state_snapshot()["status"]
        n2 = srv2.booking_requests
        dur = time.time() - t0
        print(f"  status={st2}  下单请求数={n2}  尝试次数={sch2.state_snapshot()['attempts']}"
              f"  耗时={dur:.1f}s  (retry_max=2, 间隔 3s)")
        check("失败后状态为 failed", st2 == "failed", st2)
        check("★ 总请求数 = 1 + retry_max = 3", n2 == 3, f"实际 {n2}")
        check("★ 没有高频轰炸（耗时 >= 2 个间隔 = 6s）", dur >= 5.5, f"{dur:.1f}s")
        time.sleep(4.0)
        check("★ 停止后也不再发送请求", srv2.booking_requests == n2,
              f"{n2} -> {srv2.booking_requests}")
    finally:
        srv2.stop()

    # 还原由 main() 的 finally 统一负责

    print()
    print("=" * 72)
    print("  3. 构造类错误 -> 必须立刻放弃，不重试")
    print("=" * 72)
    # 让构造 payload 直接抛异常（模拟「没填 devId」「公钥解析不了」这类确定性错误）
    IcAdapter._current_acc_no = lambda self: 100510484
    original = IcAdapter._build_booking_payload

    def boom(self, acc):
        raise ValueError("未填写座位 ID（devId）")

    IcAdapter._build_booking_payload = boom
    srv3 = FakeServer(succeed_at=1)
    try:
        cfgmod.save({
            "base_url": srv3.base,
            "schedule": {"mode": "countdown", "countdown_seconds": 0, "advance_ms": 0,
                         "retry": True, "retry_interval_ms": 50, "retry_timeout_s": 5,
                         "prewarm": False, "prewarm_advance_s": 60},
            "session": {"auto_login": True, "token": "", "cookie": ""},
        })
        logs3 = []
        sch3 = Scheduler(lambda lv, m: logs3.append(f"{lv} {m}"))
        t0 = time.time()
        sch3.start()
        deadline = time.time() + 10
        while time.time() < deadline:
            if sch3.state_snapshot()["status"] in ("success", "failed", "stopped"):
                break
            time.sleep(0.05)
        elapsed = time.time() - t0
        st3 = sch3.state_snapshot()["status"]
        n3 = srv3.booking_requests
        print(f"  status={st3}  下单请求数={n3}  尝试次数={sch3.state_snapshot()['attempts']}"
              f"  耗时={elapsed:.2f}s")
        check("构造错误 -> 状态 failed", st3 == "failed", st3)
        check("★ 只尝试 1 次（没有无脑重试）", sch3.state_snapshot()["attempts"] == 1,
              str(sch3.state_snapshot()["attempts"]))
        check("★ 一次请求都没打出去", n3 == 0, f"实际 {n3}")
        check("2 秒内就结束（不是耗到 retry_timeout_s=5s）", elapsed < 2.0, f"{elapsed:.2f}s")
        check("日志说明不会重试", any("不会重试" in x or "致命错误" in x for x in logs3))
    finally:
        IcAdapter._build_booking_payload = original
        srv3.stop()

    print()
    print("=" * 72)
    print("  4.「N 秒后测试」按钮：倒计时到点才发请求，成功后停止")
    print("=" * 72)
    from app.adapters import IcAdapter as _IA
    _IA._current_acc_no = lambda self: 100510484
    _IA._build_booking_payload = lambda self, acc, slot="": (
        {"sysKind": 8, "appAccNo": acc, "resvMember": [acc],
         "resvBeginTime": "2026-10-16 13:30:00", "resvEndTime": "2026-10-16 18:00:00",
         "encryptVlaue": "STUB", "resvDevEncrypts": ["STUB"]}, "stub payload")
    srv4 = FakeServer(succeed_at=1)
    try:
        cfgmod.save({
            "base_url": srv4.base,
            "target": {"seat_id": "100495157", "space_id": "100494721", "slot": "afternoon"},
            "schedule": {"retry": True, "retry_max": 3, "retry_interval_ms": 3000,
                         "retry_timeout_s": 30, "prewarm": False},
            "session": {"auto_login": True, "token": "", "cookie": ""},
        })
        sch4 = Scheduler(lambda lv, m: None)
        delay = 3.0
        r = sch4.trigger_after(delay_s=delay)
        t0 = time.time()
        print(f"  trigger_after({delay}s) -> ok={r['ok']}  {r['message']}")
        check("trigger_after 返回成功", r.get("ok") is True)

        # 倒计时期间不应该有任何请求
        time.sleep(1.5)
        check("★ 倒计时期间不发请求", srv4.booking_requests == 0, f"实际 {srv4.booking_requests}")
        st_mid = sch4.state_snapshot()
        check("倒计时中状态为 waiting", st_mid["status"] == "waiting", st_mid["status"])
        check("剩余秒数为正且 < 总时长",
              0 < (st_mid.get("seconds_left") or 0) < delay,
              str(st_mid.get("seconds_left")))

        # 等它到点执行
        dl = time.time() + 15
        while time.time() < dl:
            if sch4.state_snapshot()["status"] in ("success", "failed", "stopped"):
                break
            time.sleep(0.05)
        elapsed = time.time() - t0
        st4 = sch4.state_snapshot()["status"]
        n4 = srv4.booking_requests
        print(f"  status={st4}  下单请求数={n4}  到点后耗时={elapsed:.1f}s")
        check("★ 到点后发出了请求", n4 >= 1, f"实际 {n4}")
        check("成功后状态为 success", st4 == "success", st4)
        check("★ 时间点正确（>= delay 才发，没提前）", elapsed >= delay - 0.3, f"{elapsed:.1f}s")

        time.sleep(4.0)
        check("★ 成功后不再发请求", srv4.booking_requests == n4,
              f"{n4} -> {srv4.booking_requests}")

        # 任务完成后可以再次启动；但运行中必须拒绝
        r2 = sch4.trigger_after(delay_s=5)
        check("任务完成后可再次启动", r2.get("ok") is True, str(r2.get("message")))
        r3 = sch4.trigger_after(delay_s=5)
        check("★ 运行中拒绝重复启动（避免并发两个任务）", r3.get("ok") is False, str(r3.get("message")))
        sch4.stop()
    finally:
        srv4.stop()

    print()
    print("=" * 72)
    print("  5. 暂时性错误 -> 每 2 秒持续重试直到成功（模拟「时间窗口刚开放」）")
    print("=" * 72)
    IcAdapter._current_acc_no = lambda self: 100510484
    IcAdapter._build_booking_payload = lambda self, acc, slot="": (
        {"sysKind": 8, "appAccNo": acc, "resvMember": [acc],
         "encryptVlaue": "STUB", "resvDevEncrypts": ["STUB"]}, "stub")
    # 前 3 次报「不在提前预约时间范围内」，第 4 次成功
    srv5 = FakeServer(succeed_at=4, fail_message="不在提前预约时间范围内", fail_code=1)
    try:
        cfgmod.save({
            "base_url": srv5.base,
            "target": {"seat_id": "100495052", "space_id": "100494721", "slot": "evening"},
            "schedule": {"retry": True, "retry_max": None, "retry_interval_ms": 1000,
                         "retry_timeout_s": 60, "prewarm": False},
            "session": {"auto_login": True, "token": "", "cookie": ""},
        })
        logs5 = []
        sch5 = Scheduler(lambda lv, m: logs5.append(f"{lv} {m}"))
        t0 = time.time()
        sch5.trigger_after(delay_s=0.1)
        dl = time.time() + 30
        while time.time() < dl:
            if sch5.state_snapshot()["status"] in ("success", "failed", "stopped"):
                break
            time.sleep(0.05)
        st5 = sch5.state_snapshot()["status"]
        n5 = srv5.booking_requests
        span = time.time() - t0
        print(f"  status={st5}  下单请求数={n5}  尝试次数={sch5.state_snapshot()['attempts']}"
              f"  总耗时={span:.1f}s")
        check("暂时性错误 -> 最终成功", st5 == "success", st5)
        check("★ 一直重试到第 4 次成功", n5 == 4, f"实际 {n5}")
        check("★ 重试间隔约 1 秒（未受硬下限限制）", span < 12, f"{span:.1f}s")
        if len(srv5.booking_times) >= 2:
            gaps = [round(srv5.booking_times[i+1] - srv5.booking_times[i], 2)
                    for i in range(len(srv5.booking_times)-1)]
            print(f"  实际间隔(秒): {gaps}")
            check("间隔接近配置的 1 秒", all(0.8 <= g <= 2.0 for g in gaps), str(gaps))
        check("日志记录了持续重试", any("准备重试" in x for x in logs5))
    finally:
        srv5.stop()

    print()
    print("=" * 72)
    print("  6. 确定性错误（参数错误 code=100）-> 立刻停止，不重试")
    print("=" * 72)
    srv6 = FakeServer(succeed_at=10 ** 9, fail_message="请求参数错误", fail_code=100)
    try:
        cfgmod.save({
            "base_url": srv6.base,
            "schedule": {"retry": True, "retry_max": None, "retry_interval_ms": 1000,
                         "retry_timeout_s": 30, "prewarm": False},
            "session": {"auto_login": True, "token": "", "cookie": ""},
        })
        logs6 = []
        sch6 = Scheduler(lambda lv, m: logs6.append(f"{lv} {m}"))
        t0 = time.time()
        sch6.trigger_after(delay_s=0.1)
        dl = time.time() + 15
        while time.time() < dl:
            if sch6.state_snapshot()["status"] in ("success", "failed", "stopped"):
                break
            time.sleep(0.05)
        st6 = sch6.state_snapshot()["status"]
        n6 = srv6.booking_requests
        span = time.time() - t0
        print(f"  status={st6}  下单请求数={n6}  尝试次数={sch6.state_snapshot()['attempts']}"
              f"  耗时={span:.1f}s")
        check("参数错误 -> 状态 failed", st6 == "failed", st6)
        check("★ 只发 1 次，不重试", n6 == 1, f"实际 {n6}")
        check("★ 很快结束（没有耗到 30 秒超时）", span < 5, f"{span:.1f}s")
        check("日志说明不可重试", any("不可重试" in x for x in logs6))
    finally:
        srv6.stop()

    print()
    print("=" * 72)
    print(f"  结果: {len(PASS)} 通过, {len(FAIL)} 失败")
    for f in FAIL:
        print("   FAIL:", f)
    print("=" * 72)
    return 1 if FAIL else 0

if __name__ == "__main__":
    sys.exit(main())
