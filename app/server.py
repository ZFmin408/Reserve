"""纯标准库 HTTP 服务：静态页面 + JSON API。

启动：python -m app.server   或   python run.py
默认监听 127.0.0.1:8765（只本机可访问）
"""
from __future__ import annotations

import io
import json
import mimetypes
import os
import sys
import threading
import traceback
from collections import deque
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Tuple
from urllib.parse import parse_qs, urlparse

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from app import adapters, config as cfgmod
    from app.httpclient import Client
    from app.scheduler import Scheduler
else:
    from . import adapters, config as cfgmod
    from .httpclient import Client
    from .scheduler import Scheduler

ROOT = cfgmod.ROOT


def resource_dir(name: str = "") -> str:
    """只读资源目录（web/）。

    PyInstaller 打包后资源被解压到 sys._MEIPASS；开发环境就是项目目录。
    """
    base = getattr(sys, "_MEIPASS", None) or ROOT
    return os.path.join(base, name) if name else base


WEB_DIR = resource_dir("web")

HOST = os.environ.get("RESERVE_HOST", "127.0.0.1")
DEFAULT_PORT = int(os.environ.get("RESERVE_PORT", "8765"))


def bind_server(preferred: int = DEFAULT_PORT, tries: int = 50) -> ThreadingHTTPServer:
    """绑定端口，被占用就自动往后找一个空闲的。

    优先尝试 preferred（默认 8765 / 环境变量 RESERVE_PORT），
    依次试 preferred+1 ... preferred+tries；都失败则让系统随机分配。
    """
    last_err: Exception | None = None
    for port in range(preferred, preferred + tries):
        try:
            srv = ThreadingHTTPServer((HOST, port), Handler)
            srv.daemon_threads = True
            if port != preferred:
                print(f"[提示] 端口 {preferred} 已被占用，改用 {port}")
            return srv
        except OSError as e:            # 端口占用 / 权限不足
            last_err = e
            continue
    try:
        srv = ThreadingHTTPServer((HOST, 0), Handler)   # 0 = 让系统分配
        srv.daemon_threads = True
        print(f"[提示] 连续 {tries} 个端口都不可用，已让系统自动分配端口")
        return srv
    except OSError:
        raise RuntimeError(f"无法绑定任何端口（起始 {preferred}）: {last_err}")


# ----------------------------- 日志 -----------------------------
_logs = deque(maxlen=800)
_log_lock = threading.Lock()


def log(level: str, message: str) -> None:
    line = {
        "ts": datetime.now().strftime("%H:%M:%S.%f")[:-3],
        "level": level,
        "message": message,
    }
    with _log_lock:
        _logs.append(line)
    tag = {"INFO": "INFO", "WARN": "WARN", "ERROR": "ERR ", "SUCCESS": "OK  "}.get(level, level)
    print(f"[{line['ts']}] {tag} {message}", flush=True)
    try:
        os.makedirs(os.path.dirname(cfgmod.LOG_PATH), exist_ok=True)
        with open(cfgmod.LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now().isoformat(timespec='milliseconds')}] {tag} {message}\n")
    except Exception:
        pass


scheduler = Scheduler(log)


# ----------------------------- 业务封装 -----------------------------
def _mk_client(cfg: Dict[str, Any]) -> Client:
    req = cfg.get("request") or {}
    return Client(timeout=float(req.get("timeout_s") or 8), verify_tls=bool(req.get("verify_tls")))


def do_login() -> Dict[str, Any]:
    cfg = cfgmod.load()
    client = _mk_client(cfg)
    ad = adapters.build(cfg, client, log)
    ad.apply_session()
    ok, msg, detail = ad.login()
    if ok:
        s = dict(cfg.get("session") or {})     # 保留已有字段（尤其 acc_no），只覆盖下面几项
        if client.extra_headers.get("token"):
            s["token"] = client.extra_headers["token"]
        if client.cookies:
            s["cookie"] = client.cookie_header()
        # accNo 用于下单 payload 的 appAccNo/resvMember，必须持久化
        acc = detail.get("accNo") or detail.get("user_id")
        if acc is not None:
            s["acc_no"] = acc
        cfgmod.save({"session": s})
        log("SUCCESS",
            f"登录成功 token={str(detail.get('token'))[:12]}... accNo={acc}")
    else:
        log("WARN", f"登录失败: {msg}")
    return {"ok": ok, "message": msg, "detail": {k: v for k, v in detail.items() if k != "raw"},
            "session": cfgmod.redact(cfgmod.load()).get("session")}


def do_list_seats(day: str) -> Dict[str, Any]:
    cfg = cfgmod.load()
    client = _mk_client(cfg)
    ad = adapters.build(cfg, client, log)
    ad.apply_session()
    if not ad.ensure_session():
        return {"ok": False, "message": "登录失败或会话无效，请先点「诊断登录」排查", "seats": []}
    persist_session(ad)
    day = day or (cfg.get("target") or {}).get("reserve_date") or date.today().isoformat()
    ok, msg, detail = ad.fetch_seats(day)
    return {"ok": ok, "message": msg, "seats": detail.get("seats", []), "raw": str(detail.get("raw"))[:4000]}


def persist_session(ad) -> None:
    """把刷新后的 token/cookie 落盘，避免下次又用旧 token。

    注意：必须**保留 session 里的其它字段**（尤其 acc_no），
    否则会被「只写 token/cookie」覆盖掉，导致下单时拿不到 accNo。
    """
    tok = ad.client.extra_headers.get("token")
    ck = ad.client.cookie_header()
    if not (tok or ck):
        return
    s = dict((cfgmod.load().get("session") or {}))
    if tok:
        s["token"] = tok
    if ck:
        s["cookie"] = ck
    # 适配器可能在本次会话里拿到了 accNo
    cur = (ad.cfg.get("session") or {}).get("acc_no")
    if cur is not None:
        s["acc_no"] = cur
    cfgmod.save({"session": s})


def do_list_rooms() -> Dict[str, Any]:
    """列出房间（实测端点 GET /ic-web/room/getAll?sysKind=8）。"""
    cfg = cfgmod.load()
    client = _mk_client(cfg)
    ad = adapters.build(cfg, client, log)
    ad.apply_session()
    if not ad.ensure_session():
        return {"ok": False, "message": "登录失败或会话无效，请先点「诊断登录」排查", "rooms": []}
    persist_session(ad)
    ok, msg, detail = ad.fetch_rooms()
    return {"ok": ok, "message": msg, "rooms": detail.get("rooms", [])}


def do_my_reservations(day: str = "") -> Dict[str, Any]:
    """列出当前账号的预约。"""
    cfg = cfgmod.load()
    client = _mk_client(cfg)
    ad = adapters.build(cfg, client, log)
    ad.apply_session()
    if not ad.ensure_session():
        return {"ok": False, "message": "会话无效，请先点「诊断登录」", "reservations": []}
    persist_session(ad)
    ok, msg, detail = ad.list_my_reservations(day)
    return {"ok": ok, "message": msg, "reservations": detail.get("reservations", []),
            "tried": detail.get("tried", [])}


def do_cancel(resv_id: Any = None, uuid: str = "") -> Dict[str, Any]:
    cfg = cfgmod.load()
    client = _mk_client(cfg)
    ad = adapters.build(cfg, client, log)
    ad.apply_session()
    if not ad.ensure_session():
        return {"ok": False, "message": "会话无效，请先点「诊断登录」"}
    persist_session(ad)
    ok, msg, detail = ad.cancel_reservation(resv_id, uuid)
    return {"ok": ok, "message": msg, "detail": detail}


def do_diagnose() -> Dict[str, Any]:
    """一条命令看清：当前配置 + 实际发出的请求 + 服务器原始响应。

    登录失败时最有用 —— 不用猜，直接看发出去的是什么。
    """
    cfgmod.reload()          # 强制从磁盘重读，避免进程缓存导致配置不一致
    cfg = cfgmod.load()
    lg = cfg.get("login") or {}
    client = _mk_client(cfg)
    ad = adapters.build(cfg, client, log)

    steps: List[Dict[str, Any]] = []

    # 1) 公钥接口
    pub = {"step": "取公钥", "url": ad.url(ad.path("public_key", "/ic-web/login/publicKey"))}
    try:
        resp = client.get(pub["url"])
        pub["status"] = resp.status
        pub["response"] = resp.brief(400)
        j = resp.json() or {}
        d = j.get("data") or {}
        pub["publicKey_len"] = len(d.get("publicKey") or "")
        pub["nonceStr"] = d.get("nonceStr")
    except Exception as e:
        pub["error"] = repr(e)
    steps.append(pub)

    # 2) 真正登录
    ad.apply_session()
    ok, msg, detail = ad.login()
    steps.append({
        "step": "登录",
        "ok": ok,
        "message": msg,
        "sent": detail.get("sent"),
        "response": detail.get("body"),
        "status": detail.get("status"),
        "raw_keys": list((detail.get("raw") or {}).keys()) if isinstance(detail.get("raw"), dict) else None,
    })

    return {
        "ok": ok,
        "config": {
            "base_url": cfg.get("base_url"),
            "username": (cfg.get("account") or {}).get("username"),
            "password_len": len((cfg.get("account") or {}).get("password") or ""),
            "paths.login": (cfg.get("paths") or {}).get("login"),
            "paths.public_key": (cfg.get("paths") or {}).get("public_key"),
            "login.encoding": lg.get("encoding"),
            "login.wrap": lg.get("wrap"),
            "login.pwd_mode": lg.get("pwd_mode"),
            "login.extra_fields": lg.get("extra_fields"),
            "fields.login_user": (cfg.get("fields") or {}).get("login_user"),
            "fields.login_pass": (cfg.get("fields") or {}).get("login_pass"),
        },
        "steps": steps,
    }


def do_probe_request(req: Dict[str, Any]) -> Dict[str, Any]:
    """透传探测：手动指定 method/path/body/headers，看服务器真实返回。用于确定接口字段。"""
    cfg = cfgmod.load()
    client = _mk_client(cfg)
    ad = adapters.build(cfg, client, log)
    ad.apply_session()
    method = (req.get("method") or "GET").upper()
    path = req.get("path") or "/"
    url = ad.url(path)
    body = req.get("body") or ""
    headers = req.get("headers") or {}
    if method == "GET":
        resp = client.get(url, headers=headers)
    else:
        resp = client.post_raw(url, body, req.get("content_type") or "application/json;charset=UTF-8",
                               headers=headers)
    log("INFO", f"探测 {method} {url} -> {resp.status} ({resp.elapsed_ms:.0f}ms)")
    return {
        "ok": resp.status != 0 and resp.status < 400,
        "status": resp.status,
        "elapsed_ms": round(resp.elapsed_ms, 1),
        "headers": resp.headers,
        "body": resp.brief(6000),
    }


def do_login_probe() -> Dict[str, Any]:
    """探测登录接口存在性：看几种常见路径哪个不是 404。"""
    cfg = cfgmod.load()
    client = _mk_client(cfg)
    ad = adapters.build(cfg, client, log)
    ad.apply_session()
    candidates = [
        "/ic-web/login/user", "/ic-web/login", "/ic-web/user/login", "/ic-web/login/login",
        "/ic-web/sysInfo?sysType=2&sysValue=100494743&sysKind=4&status=2",
        "/ic-web/seat/freeSeat", "/ic-web/space/list",
    ]
    out = []
    for p in candidates:
        resp = client.get(ad.url(p))
        out.append({"path": p, "status": resp.status, "brief": resp.brief(200)})
        log("INFO", f"探测 {p} -> {resp.status}")
    return {"ok": True, "results": out}


# 登录字段名穷举用的候选（与 tools/probe_fields.py 保持一致）
PROBE_USER_FIELDS = [
    "logName", "username", "userName", "loginName", "account", "accountName",
    "name", "userNo", "cardNo", "readerNo", "readerId", "studentNo", "id",
]
PROBE_PWD_FIELDS = ["pwd", "password", "passWord", "passwd", "userPwd", "loginPwd", "pass"]
PROBE_PATHS = ["/ic-web/login/user", "/ic-web/login", "/ic-web/user/login"]
PROBE_WRAPS = ["", "param", "data"]
PROBE_ENCODINGS = ["json", "form"]

FAKE_USER = "__probe__"
FAKE_PWD = "__probe_pwd__"


def _login_classify(resp) -> str:
    t = resp.text or ""
    if resp.status == 0:
        return "net-error"
    if resp.status == 404:
        return "path-404"
    if resp.status >= 500:
        return f"http-{resp.status}"
    if "参数错误" in t or '"code":100' in t or '"code": 100' in t:
        return "param-error"
    if any(k in t for k in ("密码", "口令")):
        return "pwd-related"
    if any(k in t for k in ("不存在", "未找到", "无此")):
        return "user-404"
    if any(k in t for k in ("验证码", "captcha")):
        return "captcha"
    if any(k in t for k in ("成功", "success", "token")):
        return "maybe-ok"
    return "other"


def do_brute_login(limit: int = 400, include_encodings: bool = True) -> Dict[str, Any]:
    """穷举登录字段名/编码。命中判定 = 不再返回 code=100。"""
    cfg = cfgmod.load()
    client = _mk_client(cfg)
    client.extra_headers["lan"] = "1"
    base = (cfg.get("base_url") or "").rstrip("/")

    # 第 1 步：找活着的路径
    live: List[str] = []
    path_info = []
    for p in PROBE_PATHS:
        r = client.post_json(base + p, {"probe": 1})
        cat = _login_classify(r)
        path_info.append({"path": p, "status": r.status, "class": cat, "brief": r.brief(160)})
        if cat not in ("path-404", "net-error", "http-405"):
            live.append(p)
    log("INFO", f"登录路径探测: 可用 {live or '无'}")
    if not live:
        return {"ok": False, "message": "所有候选登录路径都不通，需 F12 提供真实路径",
                "paths": path_info}

    # 第 2 步：分层扫描。先默认形态（json + 无包装）扫全部字段名组合，
    #         再逐层换编码方式和包装层，保证高概率命中的组合优先被测到。
    tried = 0
    hits: List[Dict[str, Any]] = []
    from urllib.parse import urlencode

    attempts: List[Tuple[str, str, str]] = []
    for path in live:
        for enc in ["json", "form"]:
            for wrap in PROBE_WRAPS:
                attempts.append((path, enc, wrap))
    attempts.sort(key=lambda x: (x[1] != "json", x[2] != "", live.index(x[0])))

    def hit_cat(cat: str) -> bool:
        return cat not in ("param-error", "path-404", "net-error", "http-405", "http-400")

    for path, enc, wrap in attempts:
        if tried >= limit:
            break
        url = base + path
        for uf in PROBE_USER_FIELDS:
            for pf in PROBE_PWD_FIELDS:
                if tried >= limit:
                    break
                tried += 1
                body = {uf: FAKE_USER, pf: FAKE_PWD}
                payload = {wrap: body} if wrap else body
                if enc == "form":
                    r = client.post_raw(url, urlencode(payload),
                                        "application/x-www-form-urlencoded;charset=UTF-8")
                else:
                    r = client.post_json(url, payload)
                cat = _login_classify(r)
                if hit_cat(cat):
                    hits.append({"path": path, "wrap": wrap, "encoding": enc,
                                 "user_field": uf, "pwd_field": pf,
                                 "status": r.status, "class": cat, "brief": r.brief(300)})
                    log("SUCCESS",
                        f"命中! path={path} wrap={wrap or '-'} enc={enc} {uf}/{pf} -> [{cat}] {r.brief(120)}")
            if tried >= limit:
                break

    log("INFO", f"穷举完成: 试了 {tried} 种组合, 命中 {len(hits)} 条")
    if not hits:
        return {"ok": False, "tried": tried, "paths": path_info, "hits": [],
                "message": ("没有组合能绕过 code=100。说明不是字段名问题，可能是："
                            "① 密码需先加密 ② 需要签名/时间戳 ③ 必须带 Referer/Origin "
                            "④ 走的不是这个路径。请把 F12 里登录请求 Copy as cURL 贴出来。")}
    best = hits[0]
    return {"ok": True, "tried": tried, "paths": path_info, "hits": hits[:20],
            "message": f"命中 {len(hits)} 条，最优: {best['path']} {best['user_field']}/{best['pwd_field']} "
                       f"wrap={best['wrap'] or '-'} enc={best['encoding']}",
            "recommend": {"paths.login": best["path"], "login.encoding": best["encoding"],
                          "login.wrap": best["wrap"], "fields.login_user": best["user_field"],
                          "fields.login_pass": best["pwd_field"]}}


# ----------------------------- HTTP 处理 -----------------------------
class Handler(BaseHTTPRequestHandler):
    server_version = "ReserveHelper/1.0"
    protocol_version = "HTTP/1.1"

    # --- 工具
    def _send(self, code: int, body: bytes, ctype: str = "application/json; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError):
            pass

    def _json(self, obj: Any, code: int = 200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def _read_json(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    def log_message(self, fmt, *args):  # 静音默认访问日志
        pass

    # --- 路由
    def do_GET(self):
        try:
            u = urlparse(self.path)
            path = u.path
            qs = parse_qs(u.query)

            if path in ("/", "/index.html"):
                return self._static("index.html")
            if path == "/api/config":
                return self._json({"ok": True, "config": cfgmod.redact(cfgmod.load())})
            if path == "/api/logs":
                since = int((qs.get("since") or ["0"])[0])
                with _log_lock:
                    items = list(_logs)
                return self._json({"ok": True, "total": len(items), "logs": items[since:]})
            if path == "/api/state":
                return self._json({"ok": True, "state": scheduler.state_snapshot(), "now": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
            if path == "/api/seats":
                day = (qs.get("date") or [""])[0]
                return self._json(do_list_seats(day))
            if path == "/api/rooms":
                return self._json(do_list_rooms())
            if path == "/api/my-reservations":
                day = (qs.get("date") or [""])[0]
                return self._json(do_my_reservations(day))
            if path == "/api/probe/login":
                return self._json(do_login_probe())
            if path == "/api/diagnose":
                return self._json(do_diagnose())
            if path == "/api/brute-login":
                return self._json(do_brute_login(int(body.get("limit") or 400)))
            if path == "/api/status":
                cfg = cfgmod.load()
                return self._json({"ok": True, "config_exists": os.path.exists(cfgmod.CONFIG_PATH),
                                   "now": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                   "version": "1.0.0"})
            return self._static(path.lstrip("/"))
        except Exception:
            log("ERROR", traceback.format_exc(limit=3))
            return self._json({"ok": False, "message": "服务器内部错误，见日志"}, 500)

    def do_POST(self):
        try:
            path = urlparse(self.path).path
            body = self._read_json()

            if path == "/api/config":
                # 服务器地址归一化：用户可能只填 10.20.15.27，这里补上 http://
                if isinstance(body, dict) and "base_url" in body:
                    body["base_url"] = adapters.normalize_base_url(body.get("base_url") or "")
                cfg = cfgmod.save(body)
                log("INFO", "配置已保存")
                return self._json({"ok": True, "message": "配置已保存", "config": cfgmod.redact(cfg)})
            if path == "/api/reload":
                cfg = cfgmod.reload()
                log("INFO", "配置已从磁盘重载")
                return self._json({"ok": True, "message": "已重载", "config": cfgmod.redact(cfg)})
            if path == "/api/login":
                return self._json(do_login())
            if path == "/api/start":
                return self._json(scheduler.start())
            if path == "/api/stop":
                return self._json(scheduler.stop())
            if path == "/api/trigger":
                return self._json(scheduler.trigger_now())
            if path == "/api/verify":
                # 「预约验证」：立即用当前预约参数下单，时段强制为下午(13:30-18:00)
                return self._json(scheduler.trigger_now(slot_override="afternoon"))
            if path == "/api/test-delay":
                # 「1分钟后测试」：delay_s 秒后走完整定时流程发送一次预约请求
                delay = float(body.get("delay_s") or 60)
                return self._json(scheduler.trigger_after(delay_s=delay))
            if path == "/api/cancel":
                return self._json(do_cancel(body.get("resvId"), body.get("uuid") or ""))
            if path == "/api/probe":
                return self._json(do_probe_request(body))
            if path == "/api/clear-logs":
                with _log_lock:
                    _logs.clear()
                return self._json({"ok": True})
            return self._json({"ok": False, "message": "未知接口"}, 404)
        except Exception:
            log("ERROR", traceback.format_exc(limit=3))
            return self._json({"ok": False, "message": "服务器内部错误，见日志"}, 500)

    def _static(self, rel: str):
        rel = rel.replace("\\", "/").lstrip("/")
        full = os.path.normpath(os.path.join(WEB_DIR, rel))
        if not full.startswith(os.path.normpath(WEB_DIR)) or not os.path.isfile(full):
            return self._json({"ok": False, "message": "not found"}, 404)
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        with open(full, "rb") as f:
            return self._send(200, f.read(), ctype)


def main():
    try:
        os.makedirs(os.path.join(cfgmod.ROOT, "logs"), exist_ok=True)
    except OSError:
        pass          # 只读环境（如 Program Files）下忽略，日志写入会走 try/except
    srv = bind_server()
    if srv is None:
        raise SystemExit("无法启动服务：没有可用端口")
    port = srv.server_address[1]
    url = f"http://{HOST}:{port}/"
    log("INFO", f"服务已启动: {url}")
    print("=" * 60)
    print(f"  图书馆座位预约助手  ->  {url}")
    print("  浏览器会自动打开；关掉这个窗口即退出程序")
    print("=" * 60)
    # 让 run.py / 外部脚本能拿到实际端口
    os.environ["RESERVE_ACTUAL_PORT"] = str(port)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        log("INFO", "服务退出")
    finally:
        srv.server_close()


def current_port() -> int:
    """返回本次实际监听的端口（供 run.py 打开浏览器用）。"""
    try:
        return int(os.environ.get("RESERVE_ACTUAL_PORT") or 0)
    except ValueError:
        return 0


if __name__ == "__main__":
    main()
