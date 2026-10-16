"""定时器 + 抢单执行器。

设计要点：
- 高精度对齐：先粗睡到目标时刻前 50ms，再自旋到目标毫秒，避免 time.sleep 抖动。
- 预热：开抢前 N 秒先登录/刷 token，让真正发单那一刻只花在预约请求上。
- 重试：失败后按 retry_interval_ms 高频重试，直到 success 或超时。
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from . import adapters, config as cfgmod
from .httpclient import Client

# 重试间隔下限（秒）。要求「不要频繁重复发送请求」，所以即使配置写得很小也不低于此值。
MIN_RETRY_INTERVAL_S = 3.0


class Scheduler:
    def __init__(self, logger):
        self.log = logger
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.state: Dict[str, Any] = {
            "status": "idle",          # idle|waiting|prewarm|firing|success|failed|stopped
            "mode": "",
            "fire_at": "",
            "seconds_left": None,
            "attempts": 0,
            "last_message": "",
            "started_at": "",
            "finished_at": "",
            "last_response": "",
        }

    # ---------------- 对外接口 ----------------
    def start(self) -> Dict[str, Any]:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return {"ok": False, "message": "已有任务在运行", "state": self.state}
            self._stop.clear()
            self._thread = threading.Thread(target=self._run_safe, daemon=True, name="reserve-scheduler")
            self._thread.start()
            return {"ok": True, "message": "任务已启动", "state": self.state}

    def stop(self) -> Dict[str, Any]:
        self._stop.set()
        self._set(status="stopped", last_message="已手动停止")
        return {"ok": True, "message": "已请求停止", "state": self.state}

    def trigger_now(self, slot_override: str = "") -> Dict[str, Any]:
        """不等定时，立即执行一次（只发一次，不重试）。

        slot_override: 内部参数，传了就强制用该时段预设；目前没有调用方会传
                       （「预约验证」已改为使用界面当前填选的时段）。
        """
        with self._lock:
            if self._thread and self._thread.is_alive():
                return {"ok": False, "message": "已有任务在运行，请先点「停止」", "state": self.state}
            self._once_slot = slot_override or ""
            self._thread = threading.Thread(target=self._once_safe, daemon=True, name="reserve-now")
            self._thread.start()
        msg = "已触发一次预约"
        if slot_override:
            msg = f"已触发一次预约（时段强制为 {slot_override}）"
        return {"ok": True, "message": msg, "state": self.state}

    _once_slot: str = ""

    def trigger_after(self, delay_s: float = 60.0, slot_override: str = "") -> Dict[str, Any]:
        """倒计时测试：delay_s 秒后发送一次预约请求。

        用途：临时验证「定时到点发送」是否真的工作，不必等到设定时刻。
        走的是和正式定时完全相同的路径（_sleep_until 精确对齐 + 同一套下单逻辑）。
        """
        with self._lock:
            if self._thread and self._thread.is_alive():
                return {"ok": False, "message": "已有定时任务在运行，请先点「停止」", "state": self.state}
            self._stop.clear()
            self._test_slot = slot_override or ""
            self._deadline = time.time() + max(1.0, float(delay_s))
            self._set(status="waiting", mode="countdown", attempts=0,
                      started_at=datetime.now().strftime("%H:%M:%S"), finished_at="",
                      last_response="", last_message=f"测试定时：{int(delay_s)} 秒后发送",
                      fire_at=datetime.fromtimestamp(self._deadline).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
                      seconds_left=round(max(0.0, self._deadline - time.time()), 2))
            self._thread = threading.Thread(target=self._test_run_safe, daemon=True,
                                            name="reserve-scheduler")
            self._thread.start()
        return {"ok": True,
                "message": f"已启动测试定时：{int(delay_s)} 秒后（"
                           f"{datetime.fromtimestamp(self._deadline).strftime('%H:%M:%S')}）发送预约请求",
                "state": self.state}

    _test_slot: str = ""

    def _test_run_safe(self):
        try:
            cfg = self._load_cfg()
            if self._sleep_until(self._deadline):
                return
            self._set(status="firing", last_message="测试定时：开始发送预约请求")
            self.log("INFO", "=== 测试定时到点，开始发送预约请求 ===")
            ok = self._attempt_loop(cfg, cfg.get("schedule") or {}, self._test_slot or "")
            self._set(status="success" if ok else "failed",
                      finished_at=datetime.now().strftime("%H:%M:%S"),
                      last_message=("测试成功，已停止发送请求" if ok else "测试失败"))
        except Exception as e:
            self.log("ERROR", f"测试定时异常: {e!r}")
            self._set(status="failed", last_message=f"异常: {e!r}")

    def state_snapshot(self) -> Dict[str, Any]:
        with self._lock:
            snap = dict(self.state)
        if snap.get("status") == "waiting" and self._deadline:
            snap["seconds_left"] = round(max(0.0, self._deadline - time.time()), 2)
        return snap

    _deadline: Optional[float] = None

    # ---------------- 内部 ----------------
    def _set(self, **kw):
        with self._lock:
            self.state.update(kw)

    def _load_cfg(self) -> Dict[str, Any]:
        return cfgmod.load()

    def _client(self, cfg: Dict[str, Any]) -> Client:
        req = cfg.get("request") or {}
        c = Client(timeout=float(req.get("timeout_s") or 8), verify_tls=bool(req.get("verify_tls")))
        return c

    def _run_safe(self):
        try:
            self._run()
        except Exception as e:  # 保证线程不静默死掉
            self.log("ERROR", f"调度线程异常: {e!r}")
            self._set(status="failed", last_message=f"异常: {e!r}")

    def _compute_fire_at(self, sch: Dict[str, Any]) -> Optional[float]:
        mode = sch.get("mode") or "at_time"
        if mode == "countdown":
            secs = float(sch.get("countdown_seconds") or 0)
            return time.time() + secs

        raw = (sch.get("fire_time") or "").strip()
        if not raw:
            return None
        now = datetime.now()

        # ★ 用户显式指定了「定时日期」：就按那个日期算，**不会因为时刻已过而推到明天**。
        #   例如 fire_date=今天 + fire_time=13:30，而现在已 15:00 —— 仍然定在今天 13:30，
        #   意味着会立刻执行（符合「默认当天 13:30 预约当天晚上」的语义）。
        fire_date = (sch.get("fire_date") or "").strip()
        base_day = None
        if fire_date:
            for f in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d", "%m-%d"):
                try:
                    d = datetime.strptime(fire_date, f)
                    base_day = now.replace(year=d.year if f != "%m-%d" else now.year,
                                           month=d.month, day=d.day)
                    break
                except ValueError:
                    continue
            if base_day is None:
                self.log("WARN", f"定时日期无法解析: {fire_date}，按今天处理")
                base_day = now

        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M", "%H:%M:%S", "%H:%M"):
            try:
                dt = datetime.strptime(raw, fmt)
            except ValueError:
                continue
            if fmt in ("%H:%M:%S", "%H:%M"):
                day = base_day or now
                dt = day.replace(hour=dt.hour, minute=dt.minute, second=dt.second, microsecond=0)
                # 没指定日期且时刻已过 -> 才推到明天（保持旧语义）
                if base_day is None and dt <= now:
                    dt = dt + timedelta(days=1)
            return dt.timestamp() - float(sch.get("advance_ms") or 0) / 1000.0

        self.log("WARN", f"时间格式无法解析: {raw}")
        return None

    def _run(self):
        cfg = self._load_cfg()
        sch = cfg.get("schedule") or {}
        mode = sch.get("mode") or "at_time"
        self._set(status="waiting", mode=mode, started_at=datetime.now().strftime("%H:%M:%S"),
                  attempts=0, last_message="计算定时", finished_at="", last_response="")

        while True:
            fire_at = self._compute_fire_at(sch)
            if fire_at is None:
                self._set(status="failed", last_message="未设置有效的定时时间")
                return
            self._deadline = fire_at
            self._set(fire_at=datetime.fromtimestamp(fire_at).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
                      seconds_left=round(max(0.0, fire_at - time.time()), 2))

            # 预热登录
            if sch.get("prewarm"):
                warm_at = fire_at - float(sch.get("prewarm_advance_s") or 60)
                if warm_at > time.time():
                    if self._sleep_until(warm_at):
                        return
                    self._set(status="prewarm", last_message="预热登录中")
                else:
                    self._set(status="prewarm", last_message="预热登录中")
                self._login_only(cfg)

            if self._sleep_until(fire_at):
                return

            self._set(status="firing", last_message="开始发送预约请求")
            self.log("INFO", "=== 开抢 ===")
            ok = self._attempt_loop(cfg, sch)

            if ok:
                # 默认：成功后立刻停止，不再发任何请求
                if not (mode == "daily" and sch.get("keep_running_after_success")):
                    self._set(status="success", finished_at=datetime.now().strftime("%H:%M:%S"),
                              last_message="预约成功，已停止发送请求")
                    self.log("INFO", "预约成功，任务结束（不再发送任何请求）")
                    return
                # daily + 显式开启「成功后继续」：等下一个每日时刻
                self._set(status="success", finished_at=datetime.now().strftime("%H:%M:%S"),
                          last_message="今日预约成功，等待下一个每日时刻")
                self.log("INFO", "今日预约成功；keep_running_after_success=true，继续等明天")
                continue
            if mode == "daily" and not self._stop.is_set() and sch.get("keep_running_after_success"):
                self.log("WARN", "本轮失败，等待下一个每日时刻")
                continue
            self._set(status="failed", finished_at=datetime.now().strftime("%H:%M:%S"))
            return

    def _sleep_until(self, deadline: float) -> bool:
        """睡到指定时间戳。返回 True 表示被要求停止。"""
        coarse = deadline - time.time() - 0.05
        if coarse > 0:
            end = time.time() + coarse
            while time.time() < end:
                if self._stop.is_set():
                    return True
                time.sleep(min(0.2, max(0.0, end - time.time())))
        # 精对齐
        while time.time() < deadline:
            if self._stop.is_set():
                return True
            time.sleep(0)  # 让出 GIL
        return False

    def _login_only(self, cfg: Dict[str, Any]) -> bool:
        client = self._client(cfg)
        ad = adapters.build(cfg, client, self.log)
        ad.apply_session()
        ok, msg, detail = ad.login()
        self.log("INFO" if ok else "WARN", f"预热登录: {msg}")
        if ok:
            # 缓存 token/cookie 到内存，供后续单次使用
            s = cfg.setdefault("session", {})
            if client.extra_headers.get("token"):
                s["token"] = client.extra_headers["token"]
            if client.cookies:
                s["cookie"] = client.cookie_header()
            # accNo 必须一起缓存：下单 payload 的 appAccNo/resvMember 用它。
            # （以前只写 _login_cache，预热关闭后就拿不到了）
            acc = detail.get("accNo") or detail.get("user_id")
            if acc is not None:
                s["acc_no"] = acc
            cfg["_login_cache"] = {"user_id": acc, "lib_id": detail.get("lib_id")}
            self._cached_cfg = cfg
        return ok

    _cached_cfg: Optional[Dict[str, Any]] = None

    def _attempt_loop(self, cfg: Dict[str, Any], sch: Dict[str, Any],
                      slot_override: str = "") -> bool:
        """发送预约请求。

        按要求改了策略：
        - **不高频轰炸**：间隔默认 3 秒（retry_interval_ms），不是 300ms
        - **次数有限**：最多 retry_max 次重试（默认 3 次），或 retry_timeout_s 到点
        - **成功立即停止**：一收到成功就返回，不再发任何请求
        """
        retry = bool(sch.get("retry", True))
        # 重试间隔可配置。默认 2 秒 —— 够贴近「时间窗口刚开放」的瞬间，
        # 又不至于高频轰炸服务器。配置里写多小就用多小（去掉硬下限）。
        interval = max(0.2, float(sch.get("retry_interval_ms") or 2000) / 1000.0)
        # retry_max=None 表示不限次数，只受 retry_timeout_s 约束
        raw_max = sch.get("retry_max")
        retry_max = None if raw_max is None else int(raw_max)
        timeout_s = float(sch.get("retry_timeout_s") or 0)
        deadline = (time.time() + timeout_s) if timeout_s > 0 else None
        attempts = 0

        client = self._client(cfg)
        ad = adapters.build(cfg, client, self.log)
        ad.apply_session()

        # 若没预热过，先登录一次
        s = cfg.get("session") or {}
        if (s.get("auto_login", True) and not client.extra_headers.get("token")
                and not (cfg.get("_login_cache"))):
            self._login_only(cfg)
            ad.apply_session()

        while True:
            attempts += 1
            self._set(attempts=attempts)
            ok, msg, detail = ad.reserve(slot_override or "")
            self._set(last_message=f"第 {attempts} 次: {msg}",
                      last_response=str(detail.get("body") or "")[:1500])
            if ok:
                self.log("SUCCESS", f"预约成功（第 {attempts} 次）: {msg}，已停止发送请求")
                return True

            # 确定性失败（未登录 / 参数错 / 座位不存在）—— 重试无意义，立刻放弃
            if detail.get("fatal"):
                self.log("ERROR", f"不可重试的错误，已停止: {msg}")
                self._set(status="failed")
                return False

            # 时间窗口没到 / 被抢先 —— 属于可重试
            kind = detail.get("kind") or "transient"
            self.log("INFO", f"第 {attempts} 次未成功（{kind}），准备重试: {msg[:80]}")

            if not retry or self._stop.is_set():
                return False
            if deadline is not None and time.time() >= deadline:
                self.log("WARN", f"已达重试超时（{timeout_s:.0f} 秒），停止发送请求")
                return False
            if retry_max is not None and attempts > retry_max:
                self.log("WARN", f"已达重试上限（{retry_max} 次），停止发送请求")
                return False

            # 服务器若给了「还要等多久」，就睡够再试；否则用配置间隔
            wait = float(detail.get("retry_after_s") or 0)
            time.sleep(max(interval, min(wait, 300.0)))

    def _once_safe(self):
        try:
            cfg = self._load_cfg()
            client = self._client(cfg)
            ad = adapters.build(cfg, client, self.log)
            ad.apply_session()
            ok, msg, detail = ad.reserve(self._once_slot or "")
            self._set(status="success" if ok else "failed",
                      attempts=self.state.get("attempts", 0) + 1,
                      last_message=f"立即执行: {msg}",
                      last_response=str(detail.get("body") or "")[:1500],
                      finished_at=datetime.now().strftime("%H:%M:%S"))
            self.log("SUCCESS" if ok else "WARN", f"立即执行结果: {msg}")
        except Exception as e:
            self.log("ERROR", f"立即执行异常: {e!r}")
            self._set(status="failed", last_message=f"异常: {e!r}")
