"""协议适配层。

两类适配器：
- IcAdapter      面向 「IC空间预约」系（/ic-web/*）。字段名可能随学校版本变化，
                 所以每一步的字段名都允许通过 config.fields 覆盖，不改代码。
- GenericAdapter 面向任意 REST 接口：完全靠 config.replay 里粘贴的抓包原文重放。

约定：每个方法返回 (ok: bool, message: str, detail: dict)
"""
from __future__ import annotations

import json
import os
import re
import time
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode

from . import aes, crypto
from .httpclient import Client, ok_json

PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_.]+)\s*\}\}")

# 时段预设（前端下拉菜单用；后端也认，避免前端传具体时间）
TIME_SLOTS: Dict[str, Dict[str, str]] = {
    "morning":   {"label": "上午", "start": "08:00", "end": "12:00"},
    "afternoon": {"label": "下午", "start": "13:30", "end": "18:00"},
    "evening":   {"label": "晚上", "start": "19:00", "end": "22:00"},
}
DEFAULT_SLOT = "afternoon"


# ----------------------------- 小工具 -----------------------------
def resolve_date(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return date.today().isoformat()
    if s.lower() in ("today", "今天"):
        return date.today().isoformat()
    if s.lower() in ("tomorrow", "明天"):
        return (date.today() + timedelta(days=1)).isoformat()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y%m%d"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return s


def resolve_slot_time(target: Dict[str, Any], slot_override: str = "") -> Tuple[str, str, str]:
    """把「时段预设」解析成 (start, end, slot_key)。

    前端只让用户选 上午/下午/晚上；后端把预设映射成具体 HH:MM。
    若 target.slot 为空，则回退到手填的 start_time / end_time（兼容旧配置）。
    """
    slot = (slot_override or target.get("slot") or "").strip().lower()
    if slot in TIME_SLOTS:
        s = TIME_SLOTS[slot]
        return s["start"], s["end"], slot
    return (str(target.get("start_time") or "").strip(),
            str(target.get("end_time") or "").strip(),
            "")


def split_items(s: Any) -> List[str]:
    if s is None:
        return []
    if isinstance(s, (list, tuple)):
        return [str(x).strip() for x in s if str(x).strip() != ""]
    return [p.strip() for p in str(s).replace("，", ",").replace(";", ",").split(",") if p.strip()]


def unwrap(j: Any) -> Any:
    """IC 系响应常见包装：{code,message,data:{...}}。取出真正的业务数据。"""
    if isinstance(j, dict) and "data" in j and len(j) <= 6:
        return j.get("data")
    return j


def find_first(obj: Any, names: List[str]) -> Any:
    """在嵌套 dict 里递归找第一个命中的键。"""
    stack = [obj]
    seen = 0
    while stack and seen < 5000:
        cur = stack.pop(0)
        seen += 1
        if isinstance(cur, dict):
            for n in names:
                if n in cur and cur[n] not in (None, ""):
                    return cur[n]
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur[:50])
    return None


def _fmt(url: str, method: str, body: str) -> str:
    return f"{method} {url}\n{body[:1200]}"


def _looks_like_rate_limit(resp) -> bool:
    """服务器说「请求频繁」—— 这套系统登录接口有频率限制，必须退避。"""
    t = resp.text or ""
    return "请求频繁" in t or "稍后重试" in t or "too many" in t.lower()


def _looks_like_param_error(resp) -> bool:
    """服务器说「请求参数错误」：字段名/编码/密码加密方式不对。
    实测这套系统 code=100 就是这个意思。"""
    t = resp.text or ""
    return ('"code":100' in t.replace(" ", "")
            or '"code": 100' in t
            or "参数错误" in t
            or "请求参数错误" in t)


# ----------------------------- 基类 -----------------------------
class BaseAdapter:
    name = "base"

    def __init__(self, cfg: Dict[str, Any], client: Client, log):
        self.cfg = cfg
        self.client = client
        self.log = log

    # --- config 便捷访问
    @property
    def base_url(self) -> str:
        return (self.cfg.get("base_url") or "").rstrip("/")

    def path(self, key: str, fallback: str) -> str:
        return (self.cfg.get("paths") or {}).get(key) or fallback

    def url(self, path: str) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            return path
        return self.base_url + ("" if path.startswith("/") else "/") + path

    def apply_session(self) -> None:
        s = self.cfg.get("session") or {}
        if s.get("cookie"):
            self.client.set_cookie_string(s["cookie"])
        if s.get("token"):
            self.client.extra_headers["token"] = s["token"]
        for k, v in (self.cfg.get("request") or {}).get("extra_headers", {}).items():
            self.client.extra_headers[k] = v

    # ---------- 会话复用（登录接口有频率限制，必须少登） ----------
    _session_cache: Optional[Dict[str, Any]] = None   # {"hash":..., "at": ts, "detail": {...}}

    def _acct_hash(self) -> str:
        import hashlib as _h
        acc = self.cfg.get("account") or {}
        key = f"{self.base_url}|{acc.get('username')}|{acc.get('password')}"
        return _h.md5(key.encode("utf-8")).hexdigest()

    def has_session(self) -> bool:
        return bool(self.client.extra_headers.get("token"))

    def _remember(self, h: str, detail: Dict[str, Any]) -> None:
        type(self)._session_cache = {"hash": h, "at": time.time(), "detail": detail}

    # --- 接口
    def login(self, force: bool = False) -> Tuple[bool, str, dict]:
        """登录（带会话复用，避免触发服务端登录频率限制）。

        子类实现 _login_impl()，这里只负责：命中缓存/已存 token 就直接复用。
        这套系统实测连续登录会返回 code=1「请求频繁,请稍后重试」。
        """
        ttl = float(((self.cfg.get("login") or {}).get("session_ttl_s")) or 1800)
        h = self._acct_hash()

        if not force:
            c = type(self)._session_cache
            if c and c.get("hash") == h and (time.time() - c["at"]) < ttl:
                tok = (c["detail"] or {}).get("token")
                if tok:
                    self.client.extra_headers["token"] = tok
                    self.log("INFO", "复用已登录会话（未重复调用登录接口）")
                    return True, "复用已登录会话", c["detail"]
            st = (self.cfg.get("session") or {}).get("token")
            if st:
                self.client.extra_headers["token"] = st
                self.log("INFO", "复用 config.json 里保存的 token（未重复登录）")
                self._remember(h, {"token": st})
                return True, "复用已保存的 token", {"token": st}

        ok, msg, detail = self._login_impl()
        if ok:
            self._remember(h, detail)
        return ok, msg, detail

    def _login_impl(self) -> Tuple[bool, str, dict]:
        raise NotImplementedError

    def fetch_seats(self, day: str) -> Tuple[bool, str, dict]:
        return False, "该适配器未实现座位查询，请用浏览器/抓包获取座位 ID", {}

    def reserve(self) -> Tuple[bool, str, dict]:
        raise NotImplementedError


# ----------------------------- 重放模板 -----------------------------
class ReplayMixin:
    """把抓包原文贴进 config.replay.body，替换 {{占位符}} 后原样发送。"""

    def _vars(self) -> Dict[str, str]:
        t = self.cfg.get("target") or {}
        day = resolve_date(t.get("reserve_date", ""))
        seg_ids = split_items(t.get("segment_ids"))
        segs = split_items(t.get("segments"))
        now = datetime.now()
        return {
            "base_url": self.base_url,
            "username": (self.cfg.get("account") or {}).get("username", ""),
            "password": (self.cfg.get("account") or {}).get("password", ""),
            "cookie": self.client.cookie_header(),
            "token": self.client.extra_headers.get("token", ""),
            "seat_id": str(t.get("seat_id", "")),
            "space_id": str(t.get("space_id", "")),
            "seat_name": str(t.get("seat_name", "")),
            "date": day,
            "reserve_date": day,
            "timestamp": str(int(time.time() * 1000)),
            "start_time": str(t.get("start_time", "")),
            "end_time": str(t.get("end_time", "")),
            "segment_ids": ",".join(seg_ids),
            "segment_id": seg_ids[0] if seg_ids else "",
            "segments": ",".join(segs),
            "segment": segs[0] if segs else "",
            "now": now.strftime("%Y-%m-%d %H:%M:%S"),
        }

    def render(self, text: str) -> str:
        v = self._vars()

        def sub(m):
            key = m.group(1)
            cur: Any = v
            for part in key.split("."):
                if isinstance(cur, dict):
                    cur = cur.get(part, "")
                else:
                    cur = ""
                    break
            return str(cur)

        return PLACEHOLDER_RE.sub(sub, text)


# ----------------------------- IC 系适配器 -----------------------------
class IcAdapter(ReplayMixin, BaseAdapter):
    name = "ic"

    # 字段名默认值：实测抓包确认（logonName 多了个 o，密码字段是 password 不是 pwd）
    FIELDS: Dict[str, str] = {
        "login_user": "logonName",
        "login_pass": "password",
        "seat_id": "seatId",
        "space_id": "spaceId",
        "date": "date",
        "start": "startTime",
        "end": "endTime",
        "segment_ids": "segmentIds",
        "segments": "segments",
        "user_id": "userId",
        "lib_id": "libId",
    }

    def f(self, key: str) -> str:
        return (self.cfg.get("fields") or {}).get(key) or self.FIELDS.get(key, key)

    def _segment_payload(self, payload: Dict[str, Any]) -> None:
        t = self.cfg.get("target") or {}
        seg_ids = split_items(t.get("segment_ids"))
        segs = split_items(t.get("segments"))
        if seg_ids:
            ids: Any = [int(x) if x.isdigit() else x for x in seg_ids]
            payload[self.f("segment_ids")] = ids
        if segs:
            vals: Any = [int(x) if x.isdigit() else x for x in segs]
            payload[self.f("segments")] = vals

    # ---------- 登录 ----------
    # ---------- 两步登录：先取公钥+nonce，再加密登录 ----------
    def fetch_public_key(self) -> Tuple[bool, str, dict]:
        """GET /ic-web/login/publicKey -> {publicKey, nonceStr}

        实测：这套系统（VPC/IC 系）登录前必须先取公钥和 nonce，
        密码的加密明文是  password + ";" + nonceStr
        """
        path = self.path("public_key", "/ic-web/login/publicKey")
        url = self.url(path)
        resp = self.client.get(url)
        ok, j, msg = ok_json(resp)
        if not ok:
            return False, f"取公钥失败: {msg}", {"status": resp.status, "body": resp.brief()}
        data = unwrap(j) or {}
        pk = data.get("publicKey") or ""
        nonce = data.get("nonceStr") or ""
        if not pk:
            return False, f"响应里没有 publicKey: {resp.brief(300)}", {}
        self.log("INFO", f"取公钥 -> GET {url}  nonceStr={nonce}")
        return True, "ok", {"publicKey": pk, "nonceStr": nonce}

    def _pwd_transform(self, pwd: str) -> str:
        """按 login.pwd_mode 处理密码：plain/md5/sha256/base64/rsa_hex/rsa_b64/..."""
        lg = self.cfg.get("login") or {}
        mode = (lg.get("pwd_mode") or "plain").lower()
        if mode.startswith("rsa"):
            return crypto.encrypt_password(pwd, mode, lg.get("rsa_public_key") or "",
                                           lg.get("rsa_padding") or "pkcs1")
        return crypto.hash_password(pwd, mode)

    def _prepare_password(self, pwd: str) -> Tuple[str, Dict[str, Any]]:
        """按 login.pwd_mode 产出要发送的密码串。

        支持 auto_pubkey 模式：先打 login/publicKey 拿公钥和 nonce，
        再把  pwd + ";" + nonceStr  用 RSA 加密（这就是本系统的真实逻辑）。
        返回 (密文, 额外字段)
        """
        lg = self.cfg.get("login") or {}
        mode = (lg.get("pwd_mode") or "plain").lower()

        if mode in ("auto_pubkey", "rsa_auto"):
            ok, msg, d = self.fetch_public_key()
            if not ok:
                raise RuntimeError(msg)
            pk, nonce = d["publicKey"], d["nonceStr"]
            sep = lg.get("nonce_sep", ";")
            plain = f"{pwd}{sep}{nonce}" if nonce else pwd
            out_mode = lg.get("rsa_output") or "rsa_b64"
            enc = crypto.encrypt_password(plain, out_mode, pk, lg.get("rsa_padding") or "pkcs1")
            # nonce 有时需要单独回传
            extra: Dict[str, Any] = {}
            if lg.get("send_nonce"):
                extra["nonceStr"] = nonce
            return enc, extra

        if mode.startswith("rsa"):
            key = lg.get("rsa_public_key") or ""
            if not key and lg.get("fetch_pubkey"):
                ok, msg, d = self.fetch_public_key()
                if not ok:
                    raise RuntimeError(msg)
                key = d["publicKey"]
            return crypto.encrypt_password(pwd, mode, key, lg.get("rsa_padding") or "pkcs1"), {}

        return crypto.hash_password(pwd, mode), {}

    def _login_impl(self) -> Tuple[bool, str, dict]:
        acc = self.cfg.get("account") or {}
        user, pwd = acc.get("username", ""), acc.get("password", "")
        if not user or not pwd:
            return False, "账号或密码为空", {}

        lg = self.cfg.get("login") or {}

        try:
            enc_pwd, dyn_extra = self._prepare_password(pwd)
        except Exception as e:
            return False, f"密码预处理失败: {e}", {}

        # 模式 A：直接粘贴抓到的请求体当模板（最稳，能覆盖任何离谱格式）
        template = (lg.get("body") or "").strip()
        path = self.path("login", "/ic-web/login/user")
        url = self.url(path)
        headers: Dict[str, str] = {}

        if template:
            vars_map = {
                "username": user,
                "password": pwd,
                "password_enc": enc_pwd,
                "password_md5": crypto.hash_password(pwd, "md5"),
                "password_sha256": crypto.hash_password(pwd, "sha256"),
                "timestamp": str(int(time.time() * 1000)),
                "cookie": self.client.cookie_header(),
            }

            def sub(m):
                return str(vars_map.get(m.group(1), ""))

            body_text = PLACEHOLDER_RE.sub(sub, template)
            sent_preview = f"{url}\n[模板] {body_text}"
            resp = self.client.request("POST", url, body=body_text.encode("utf-8"),
                                       headers={"Content-Type": "application/json"})
            self.log("INFO", f"登录(模板) -> POST {sent_preview}")
            ok, j, msg = ok_json(resp)
            if not ok:
                return False, f"登录失败(模板): {msg}", {"status": resp.status, "body": resp.brief()}
            return self._login_success(j, sent_preview)

        # 模式 B：按字段名构造
        body: Dict[str, Any] = {
            self.f("login_user"): user,
            self.f("login_pass"): enc_pwd,
        }
        # 兼容旧键 login_extra
        extra = dict(self.cfg.get("login_extra") or {})
        extra.update(lg.get("extra_fields") or {})
        body.update(extra)
        body.update(dyn_extra or {})   # auto_pubkey 模式可能带出 nonceStr
        if lg.get("console_type") is not None:
            body.setdefault("consoleType", lg["console_type"])

        wrap = (lg.get("wrap") or "").strip()
        payload: Any = {wrap: body} if wrap else body

        encoding = (lg.get("encoding") or "json").lower()

        # 后端可能校验来源头，抓包里出现过 Origin 就一定要带上
        if lg.get("origin"):
            headers["Origin"] = lg["origin"]
        elif self.base_url.startswith("http"):
            headers["Origin"] = self.base_url
        if lg.get("referer"):
            headers["Referer"] = lg["referer"]
        else:
            headers["Referer"] = self.base_url + "/"

        if encoding == "query":
            qs = urlencode(payload)
            url = url + ("&" if "?" in url else "?") + qs
            sent_preview = url
            resp = self.client.post_raw(url, b"", "application/x-www-form-urlencoded", headers=headers)
        elif encoding == "form":
            form = urlencode(payload)
            sent_preview = f"{url}\n{form}"
            resp = self.client.post_raw(url, form, "application/x-www-form-urlencoded;charset=UTF-8",
                                        headers=headers)
        else:
            sent_preview = f"{url}\n{json.dumps(payload, ensure_ascii=False)}"
            resp = self.client.post_json(url, payload, headers=headers)

        self.log("INFO", f"登录 -> POST {sent_preview}")
        ok, j, msg = ok_json(resp)

        # 自愈：明文/哈希被拒(code=100)时，自动改用「取公钥+nonce 再 RSA」重试一次。
        # 这套系统（中山大学 IC 系）实测必须走两步加密，明文一律 code=100。
        if not ok and not template and _looks_like_param_error(resp):
            mode = (lg.get("pwd_mode") or "plain").lower()
            if mode not in ("auto_pubkey", "rsa_auto") and not lg.get("body"):
                self.log("WARN", f"pwd_mode={mode} 被拒(code=100)，自动切换 auto_pubkey 重试")
                auto = dict(lg)
                auto["pwd_mode"] = "auto_pubkey"
                auto.setdefault("rsa_output", "rsa_b64")
                auto.setdefault("nonce_sep", ";")
                auto.setdefault("rsa_padding", "pkcs1")
                auto["body"] = ""
                saved = self.cfg.get("login")
                try:
                    self.cfg["login"] = auto
                    return self._login_impl()
                finally:
                    self.cfg["login"] = saved

        if not ok:
            if resp.status == 404:
                return False, "登录接口不存在 (404): 检查 paths.login", {"status": 404, "body": resp.brief()}
            if _looks_like_rate_limit(resp):
                return False, (
                    "登录被限流: " + (j.get("message") if isinstance(j, dict) else "") +
                    " —— 这套系统登录接口有频率限制。程序已内置退避，"
                    "但请避免短时间内反复点「测试登录」。"
                ), {"status": resp.status, "body": resp.brief(), "rate_limited": True}
            if _looks_like_param_error(resp):
                return False, (
                    "请求参数错误(code=100): 登录字段名/编码方式/密码加密方式不对。"
                    "① 点界面「穷举登录字段名」 ② 或把 F12 里登录请求 Payload 原文贴进 "
                    "「登录请求体模板」框（支持 {{username}} {{password_enc}} 占位符）。"
                    f" 当前发出: {sent_preview}"
                ), {"status": resp.status, "body": resp.brief()}
            return False, f"登录失败: {msg}", {"status": resp.status, "body": resp.brief()}

        return self._login_success(j, sent_preview)

    def _login_success(self, j: Any, sent_preview: str) -> Tuple[bool, str, dict]:
        data = unwrap(j) or {}
        token = find_first(data, ["token", "accessToken", "access_token"]) or \
                find_first(j, ["token", "accessToken"])
        if token:
            self.client.extra_headers["token"] = str(token)
        # 这套系统的 accNo 就是下单要用的 appAccNo / resvMember
        uid = find_first(data, ["accNo", "userId", "uid"])
        lib = find_first(data, ["libId", "libraryId", "lib_id"])
        # 顺手把会话写回 cfg，供同进程后续复用（避免重复登录触发限流）
        sess = self.cfg.setdefault("session", {})
        if token:
            sess["token"] = str(token)
        if self.client.cookies:
            sess["cookie"] = self.client.cookie_header()
        self.log("SUCCESS",
                 f"登录成功 token={str(token)[:12] if token else '(响应里无 token)'} accNo={uid}")
        return True, "登录成功", {"token": str(token) if token else "", "user_id": uid, "lib_id": lib,
                                  "accNo": uid,
                                  "sent": sent_preview,
                                  "raw": j if isinstance(j, dict) else {}}

    # ---------- 会话有效性 ----------
    @staticmethod
    def _looks_logged_out(resp) -> bool:
        """服务器说未登录 / token 失效。"""
        t = resp.text or ""
        return ("用户未登录" in t or "请重新登录" in t
                or '"code":300' in t.replace(" ", "") or '"code": 300' in t)

    def ensure_session(self) -> bool:
        """确保有一个能用的会话。

        配置里存的 token 可能已过期，所以这里**先验证**再决定是否复用；
        验证失败就强制重新登录（并刷新配置里的 token）。
        """
        # 先按缓存/已存 token 走一次
        ok, msg, detail = self.login()
        if not ok:
            return False
        # 用一个轻量接口验证 token 是否真的有效
        try:
            r = self.client.get(self.url(self.path("room_list", "/ic-web/room/getAll")) + "?sysKind=8")
            if self._looks_logged_out(r):
                self.log("WARN", "已存 token 已失效，强制重新登录")
                ok, msg, detail = self.login(force=True)
                if not ok:
                    return False
                r = self.client.get(self.url(self.path("room_list", "/ic-web/room/getAll")) + "?sysKind=8")
                if self._looks_logged_out(r):
                    self.log("ERROR", f"重新登录后仍然未登录: {r.brief(200)}")
                    return False
            return True
        except Exception as e:
            self.log("WARN", f"会话校验异常: {e!r}")
            return True   # 网络抖动不算失效，交给后续请求报错

    # ---------- 查座位（实测端点：GET /ic-web/reserve） ----------
    def fetch_seats(self, day: str) -> Tuple[bool, str, dict]:
        """GET /ic-web/reserve?roomIds=<roomId>&resvDates=YYYYMMDD&sysKind=8

        实测响应里每个座位形如：
        {"devId":100493961,"devName":"2F-B001","devStatus":0,"devProp":2,
         "coordinate":"67.75,26.54","kindName":"二楼座位","roomName":"二楼B区",
         "openStart":"08:00","openEnd":"22:00","resvInfo":[...]}
        devId 就是下单 payload 里 resvDevEncrypts 要加密的那个 ID。
        """
        t = self.cfg.get("target") or {}
        bk = self.cfg.get("booking") or {}
        room = str(t.get("space_id") or "").strip()
        if not room:
            return False, "未填写房间 ID（roomIds）。先用 tools/list_rooms.py 查，或点「列出房间」。", {}

        day = resolve_date(day or t.get("reserve_date", ""))
        compact = day.replace("-", "").replace("/", "")   # 2026-10-16 -> 20261016
        path = self.path("seat_list", "/ic-web/reserve")
        params = {
            "roomIds": room,
            "resvDates": compact,
            "sysKind": int(bk.get("sys_kind") or 8),
        }
        qs = urlencode(params)
        url = self.url(path) + ("&" if "?" in path else "?") + qs
        resp = self.client.get(url)
        # token 失效则强制重登并重试一次
        if self._looks_logged_out(resp):
            self.log("WARN", "查座位返回未登录，强制重登后重试")
            if self.login(force=True)[0]:
                resp = self.client.get(url)
        self.log("INFO", f"查座位 -> GET {url}  => HTTP {resp.status}")
        ok, j, msg = ok_json(resp)
        if not ok:
            return False, f"查座位失败: {msg}", {"body": resp.brief(), "url": url}

        data = unwrap(j)
        seats = self._normalize_seats(data)
        free = [s for s in seats if s.get("free")]
        return (True, f"{room} 在 {compact} 共 {len(seats)} 个座位，其中空闲 {len(free)} 个",
                {"seats": seats, "raw_count": len(data) if isinstance(data, list) else 0, "url": url})

    @staticmethod
    def _normalize_seats(data: Any) -> List[Dict[str, Any]]:
        """把 /ic-web/reserve 的响应整理成统一结构。"""
        out: List[Dict[str, Any]] = []
        rows = data if isinstance(data, list) else (data.get("rows") if isinstance(data, dict) else [])
        for node in (rows or []):
            if not isinstance(node, dict) or node.get("devId") is None:
                continue
            resv_info = node.get("resvInfo") or []
            # devStatus: 0=可用。resvInfo 非空说明该时段已被占
            busy = bool(resv_info) or node.get("devStatus") not in (0, "0", None)
            out.append({
                "seatId": node.get("devId"),
                "seatName": node.get("devName") or node.get("devSn"),
                "status": "占用" if busy else "空闲",
                "free": not busy,
                "spaceId": node.get("roomId"),
                "roomName": node.get("roomName"),
                "kindName": node.get("kindName"),
                "coordinate": node.get("coordinate"),
                "openTime": f"{node.get('openStart')}-{node.get('openEnd')}",
                "resvRuleId": (node.get("resvRule") or {}).get("ruleId"),
                "minResvTime": (node.get("resvRule") or {}).get("minResvTime"),
                "maxResvTime": (node.get("resvRule") or {}).get("maxResvTime"),
                "onlyView": node.get("onlyView"),
            })
        return out

    # ---------- 列房间（实测端点：GET /ic-web/room/getAll?sysKind=8） ----------
    def fetch_rooms(self) -> Tuple[bool, str, dict]:
        bk = self.cfg.get("booking") or {}
        kind = int(bk.get("sys_kind") or 8)
        url = self.url(self.path("room_list", "/ic-web/room/getAll")) + f"?sysKind={kind}"
        resp = self.client.get(url)
        self.log("INFO", f"列房间 -> GET {url}  => HTTP {resp.status}")
        ok, j, msg = ok_json(resp)
        if not ok:
            return False, f"列房间失败: {msg}", {"body": resp.brief()}
        data = unwrap(j) or []
        rooms = [{"roomId": r.get("roomId"), "roomSn": r.get("roomSn"), "roomName": r.get("roomName")}
                 for r in data if isinstance(r, dict)]
        return True, f"共 {len(rooms)} 个房间 (sysKind={kind})", {"rooms": rooms}

    # ---------- 我的预约：查询 / 取消 ----------
    def list_my_reservations(self, day: str = "") -> Tuple[bool, str, dict]:
        """列当前账号的预约。端点来自 JS：GET /ic-web/reserve/endList 等。

        这里优先用 endList（进行中的预约），失败再试 /reserve/count。
        """
        day = resolve_date(day or (self.cfg.get("target") or {}).get("reserve_date", ""))
        compact = day.replace("-", "").replace("/", "")
        tried: List[Dict[str, Any]] = []
        for key, path, params in (
            ("endList", "/ic-web/reserve/endList", {"resvDates": compact}),
            ("endList_all", "/ic-web/reserve/endList", {}),
            ("reserve_count", "/ic-web/reserve/count", {}),
        ):
            url = self.url(path)
            if params:
                url += "?" + urlencode(params)
            r = self.client.get(url)
            if self._looks_logged_out(r):
                self.login(force=True)
                r = self.client.get(url)
            tried.append({"path": path, "status": r.status, "brief": r.brief(300)})
            ok, j, msg = ok_json(r)
            if ok:
                data = unwrap(j)
                if isinstance(data, list) and data:
                    return True, f"共 {len(data)} 条预约", {"reservations": data, "raw": data}
        return False, "查询不到预约列表（端点可能需要其他参数）", {"tried": tried}

    def cancel_reservation(self, resv_id: Any = None, uuid: str = "") -> Tuple[bool, str, dict]:
        """取消预约。JS 里的相关端点：
        POST /ic-web/reserve/endReserve  （结束/取消）
        POST /ic-web/reserve/endAhaed    （提前结束）
        POST /ic-web/reserve/delete      （删除）
        参数名可能是 resvId / uuid，这里都带上。
        """
        targets = self.cfg.get("cancel") or {}
        path = targets.get("path") or "/ic-web/reserve/endReserve"
        attempts = []
        body: Dict[str, Any] = {}
        if resv_id is not None:
            body["resvId"] = resv_id
            body["resvID"] = resv_id
        if uuid:
            body["uuid"] = uuid
            body["resvUuid"] = uuid

        for p, b in ((path, body),
                     ("/ic-web/reserve/delete", body),
                     ("/ic-web/reserve/endAhaed", body)):
            url = self.url(p)
            r = self.client.post_json(url, b)
            ok, j, msg = ok_json(r)
            attempts.append({"path": p, "status": r.status, "ok": ok, "brief": r.brief(300)})
            self.log("SUCCESS" if ok else "WARN", f"取消预约 -> POST {url} {json.dumps(b, ensure_ascii=False)} => {r.brief(300)}")
            if ok:
                return True, f"取消成功 ({p})", {"attempts": attempts, "response": j}
        return False, "取消失败，三种端点都被拒", {"attempts": attempts}

    # ---------- 预约（按实测 cURL 还原的真实 payload） ----------
    def _build_booking_payload(self, acc_no: Any, slot_override: str = "") -> Tuple[Dict[str, Any], str]:
        """构造 POST /ic-web/reserve/seat 的请求体。

        实测（2026-10 抓包）真实结构：
        {"sysKind":8,"appAccNo":100510484,"memberKind":1,"resvMember":[100510484],
         "resvBeginTime":"2026-10-16 14:00:00","resvEndTime":"2026-10-16 18:00:00",
         "testName":"","captcha":"","resvProperty":0,"resvDev":[0],
         "encryptVlaue":"<RSA(随机口令) base64>",
         "resvDevEncrypts":["<AES(devId, 随机口令) 大写hex>"],"memo":""}

        信封加密（JS chunk-common 模块 fa7d 的 b 函数实测）：
          a = 16 位随机口令 [A-Za-z0-9]
          encryptVlaue      = RSA(a)  -> base64（PKCS#1 v1.5）
          resvDevEncrypts[] = AES-CBC(devId, a, iv="ABCDEF1234123412", ZeroPadding) -> 大写 hex
        """
        t = self.cfg.get("target") or {}
        bk = self.cfg.get("booking") or {}

        dev_id = str(t.get("seat_id") or "").strip()
        if not dev_id:
            raise ValueError("未填写座位 ID（devId）。请先点「查询可用座位」选座。")

        day = resolve_date(t.get("reserve_date", ""))
        start, end, slot_key = resolve_slot_time(t, slot_override)
        if not start or not end:
            raise ValueError("未选择时段。请在界面上选择 上午 / 下午 / 晚上。")

        def stamp(hhmm: str) -> str:
            s = hhmm if hhmm.count(":") >= 2 else f"{hhmm}:00"
            return f"{day} {s}"

        # 1) 取公钥（RSA 用）
        ok, msg, d = self.fetch_public_key()
        if not ok:
            raise RuntimeError(msg)
        pub = d["publicKey"]

        # 2) 生成随机口令 a
        key_len = int(bk.get("aes_key_len") or 16)
        a = aes.random_key_secure(key_len)

        # 3) encryptVlaue = RSA(a)
        enc_value = crypto.encrypt_password(a, bk.get("rsa_output") or "rsa_b64", pub,
                                            bk.get("rsa_padding") or "pkcs1")

        # 4) resvDevEncrypts = [ AES(devId, a) ]
        mode = (bk.get("aes_mode") or "ecb").lower()
        pad = bk.get("aes_padding") or "pkcs7"
        upper = (bk.get("aes_output") or "hex_upper").startswith("hex_upper")
        if mode == "cbc":
            iv = bk.get("aes_iv") or "ABCDEF1234123412"
            dev_enc = aes.encrypt_cbc(dev_id, a, iv, pad=pad, upper=upper)
        else:
            dev_enc = aes.encrypt_ecb(dev_id, a, pad=pad, upper=upper)

        payload: Dict[str, Any] = {
            "sysKind": int(bk.get("sys_kind") or 8),
            "appAccNo": acc_no,
            "memberKind": int(bk.get("member_kind") or 1),
            "resvMember": [acc_no],
            "resvBeginTime": stamp(start),
            "resvEndTime": stamp(end),
            "testName": bk.get("test_name") or "",
            "captcha": bk.get("captcha") or "",
            "resvProperty": int(bk.get("resv_property") or 0),
            "resvDev": bk.get("resv_dev") or [0],
            "encryptVlaue": enc_value,
            "resvDevEncrypts": [dev_enc],
            "memo": bk.get("memo") or "",
        }
        explain = (f"devId={dev_id}  口令={a}  AES({mode}/{pad})={dev_enc[:24]}...  "
                   f"RSA={enc_value[:24]}...")
        return payload, explain

    def _current_acc_no(self) -> Any:
        """拿当前账号的 accNo（下单 payload 的 appAccNo / resvMember）。"""
        # 先看登录缓存
        lids = self.cfg.get("_login_cache") or {}
        if lids.get("user_id") is not None:
            return lids["user_id"]
        # 再问服务端
        try:
            r = self.client.get(self.url(self.path("account_info", "/ic-web/account/info")))
            data = unwrap(r.json() or {}) or {}
            if data.get("accNo") is not None:
                return data["accNo"]
        except Exception:
            pass
        # 兜底：从配置里读
        return self.cfg.get("account", {}).get("acc_no") or None

    def reserve(self, slot_override: str = "") -> Tuple[bool, str, dict]:
        rep = self.cfg.get("replay") or {}
        if rep.get("enabled") and rep.get("body"):
            return self._reserve_replay()

        acc_no = self._current_acc_no()
        if acc_no is None:
            return False, "拿不到 accNo（appAccNo/resvMember 需要它）。请先成功登录一次。", \
                   {"fatal": True}

        try:
            payload, explain = self._build_booking_payload(acc_no, slot_override)
        except Exception as e:
            # 构造失败是确定性错误（缺 devId / 公钥解析不了 / 账号信息缺失），
            # 重试永远不会成功 —— 标记 fatal，让调度器立刻放弃，别白打接口。
            # （实测：不标记的话会按 retry 一直打 100 多次）
            return False, f"构造下单请求失败，不会重试: {e}", {"fatal": True}

        url = self.url(self.path("booking", "/ic-web/reserve/seat"))
        body = json.dumps(payload, ensure_ascii=False)
        headers = {"Content-Type": "application/json"}
        if self.base_url.startswith("http"):
            headers["Origin"] = self.base_url
        resp = self.client.post_raw(url, body, "application/json", headers=headers)
        ok, j, msg = ok_json(resp)
        # 成功响应里带 resvId/uuid，提取出来方便取消
        info: Dict[str, Any] = {"status": resp.status, "body": resp.brief(), "sent": body,
                                "explain": explain}
        data = unwrap(j) if ok else None
        if isinstance(data, dict):
            info["resvId"] = data.get("resvId")
            info["uuid"] = data.get("uuid")
            info["resvDate"] = data.get("resvDate")
            info["resvStatus"] = data.get("resvStatus")
            # ★ 成功响应里的 resvDevInfoList 才是座位信息的权威来源
            #   实测：{"devId":100495157,"devName":"4F-A106","roomId":100494721,
            #          "roomName":"四楼A区","kindName":"四楼座位"}
            devs = data.get("resvDevInfoList") or []
            d0 = (devs[0] or {}) if isinstance(devs, list) and devs else {}
            if d0.get("devId"):
                info["devId"] = d0.get("devId")
                info["devName"] = d0.get("devName")
                info["roomId"] = d0.get("roomId")
                info["roomName"] = d0.get("roomName")
                info["kindName"] = d0.get("kindName")
                msg = (f"{msg}  座位={d0.get('roomName')}/{d0.get('devName')} "
                       f"devId={d0.get('devId')} resvId={data.get('resvId')}")
                self.log("SUCCESS",
                         f"座位确认 -> {d0.get('roomName')} {d0.get('devName')} "
                         f"devId={d0.get('devId')} roomId={d0.get('roomId')}")
            else:
                msg = f"{msg}  resvId={data.get('resvId')} uuid={str(data.get('uuid'))[:8]}"
        self.log("SUCCESS" if ok else "WARN",
                 f"预约 -> POST {url}\n{body}\n=> HTTP {resp.status} {resp.brief(400)}\n  {explain}")
        return ok, msg, info

    def _reserve_replay(self) -> Tuple[bool, str, dict]:
        rep = self.cfg["replay"]
        path = rep.get("path") or self.path("booking", "/ic-web/seat/booking")
        url = self.url(self.render(path))
        body = self.render(rep.get("body") or "")
        headers = {k: self.render(v) for k, v in (rep.get("headers") or {}).items()}
        resp = self.client.post_raw(url, body, rep.get("content_type") or "application/json;charset=UTF-8",
                                    headers=headers)
        ok, j, msg = ok_json(resp)
        self.log("INFO" if ok else "WARN",
                 f"预约(重放) -> {_fmt(url, 'POST', body)} => {resp.status} {resp.brief(400)}")
        return ok, msg, {"status": resp.status, "body": resp.brief()}


# ----------------------------- 通用适配器 -----------------------------
class GenericAdapter(ReplayMixin, BaseAdapter):
    name = "generic"

    def _login_impl(self) -> Tuple[bool, str, dict]:
        """通用模式不猜登录接口：直接用抓包来的 cookie/token。"""
        if self.client.extra_headers.get("token") or self.client.cookies:
            return True, "使用手动填写的会话凭证", {}
        return False, "通用模式需要手动填写 cookie/token（或在 paths.login 指定接口并自行扩展）", {}

    def reserve(self) -> Tuple[bool, str, dict]:
        rep = self.cfg.get("replay") or {}
        if not rep.get("body"):
            return False, "通用模式必须在「重放模板」里粘贴预约请求体", {}
        path = rep.get("path") or "/"
        url = self.url(self.render(path))
        body = self.render(rep.get("body") or "")
        headers = {k: self.render(v) for k, v in (rep.get("headers") or {}).items()}
        resp = self.client.post_raw(url, body, rep.get("content_type") or "application/json;charset=UTF-8",
                                    headers=headers)
        ok, j, msg = ok_json(resp)
        self.log("INFO" if ok else "WARN",
                 f"预约(通用) -> {_fmt(url, 'POST', body)} => {resp.status} {resp.brief(400)}")
        return ok, msg, {"status": resp.status, "body": resp.brief()}


def build(cfg: Dict[str, Any], client: Client, log) -> BaseAdapter:
    kind = (cfg.get("system") or "ic").lower()
    if kind == "generic":
        return GenericAdapter(cfg, client, log)
    return IcAdapter(cfg, client, log)
