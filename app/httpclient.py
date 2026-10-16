"""极简 HTTP 客户端：基于 urllib，支持 gzip/deflate、超时、Cookie 与原始响应记录。"""
from __future__ import annotations

import gzip
import io
import json
import ssl
import time
import urllib.error
import urllib.request
import zlib
from typing import Any, Dict, Optional, Tuple
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36 Edg/155.0.0.0"
)


class Response:
    def __init__(self, status: int, headers: Dict[str, str], raw: bytes, elapsed_ms: float):
        self.status = status
        self.headers = headers
        self.raw = raw
        self.elapsed_ms = elapsed_ms

    @property
    def text(self) -> str:
        return self.raw.decode("utf-8", errors="replace")

    def json(self) -> Optional[Any]:
        try:
            return json.loads(self.text)
        except Exception:
            return None

    def brief(self, limit: int = 800) -> str:
        return self.text[:limit]


class Client:
    def __init__(self, timeout: float = 8.0, verify_tls: bool = False):
        self.timeout = timeout
        self.verify_tls = verify_tls
        self.cookies: Dict[str, str] = {}
        self.extra_headers: Dict[str, str] = {}
        self.last: Optional[Response] = None
        ctx = None
        if not verify_tls:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        self._ctx = ctx

    # ---------- Cookie ----------
    def set_cookie_string(self, s: str) -> None:
        self.cookies = {}
        for part in (s or "").split(";"):
            part = part.strip()
            if not part or "=" not in part:
                continue
            k, v = part.split("=", 1)
            self.cookies[k.strip()] = v.strip()

    def cookie_header(self) -> str:
        return "; ".join(f"{k}={v}" for k, v in self.cookies.items())

    def update_cookies(self, headers) -> None:
        try:
            raw = headers.get_all("Set-Cookie") or []
        except Exception:
            raw = []
        for item in raw:
            first = item.split(";", 1)[0]
            if "=" in first:
                k, v = first.split("=", 1)
                self.cookies[k.strip()] = v.strip()

    # ---------- 请求 ----------
    def request(
        self,
        method: str,
        url: str,
        body: bytes | None = None,
        headers: Dict[str, str] | None = None,
        json_body: Any = None,
    ) -> Response:
        hdrs = {
            "User-Agent": DEFAULT_UA,
            "Accept": "application/json, text/plain, */*",
            "Accept-Encoding": "gzip, deflate",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Connection": "keep-alive",
            "lan": "1",
        }
        hdrs.update(self.extra_headers)
        hdrs.update(headers or {})
        if self.cookies:
            hdrs["Cookie"] = self.cookie_header()

        data = body
        if json_body is not None:
            data = json.dumps(json_body, ensure_ascii=False).encode("utf-8")
            hdrs.setdefault("Content-Type", "application/json;charset=UTF-8")
        if method.upper() in ("POST", "PUT", "PATCH") and data is None:
            data = b""

        req = urllib.request.Request(url, data=data, headers=hdrs, method=method.upper())
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=self._ctx) as resp:
                raw = resp.read()
                h = {k.lower(): v for k, v in resp.headers.items()}
                self.update_cookies(resp.headers)
                status = resp.status
        except urllib.error.HTTPError as e:
            raw = e.read()
            h = {k.lower(): v for k, v in (e.headers or {}).items()} if e.headers else {}
            self.update_cookies(e.headers)
            status = e.code
        except Exception as e:  # 网络层异常
            elapsed = (time.perf_counter() - started) * 1000
            r = Response(0, {}, str(e).encode("utf-8"), elapsed)
            self.last = r
            return r

        enc = (h.get("content-encoding") or "").lower()
        if "gzip" in enc:
            try:
                raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
            except Exception:
                pass
        elif "deflate" in enc:
            try:
                raw = zlib.decompress(raw)
            except Exception:
                try:
                    raw = zlib.decompress(raw, -zlib.MAX_WBITS)
                except Exception:
                    pass

        elapsed = (time.perf_counter() - started) * 1000
        r = Response(status, h, raw, elapsed)
        self.last = r
        return r

    def get(self, url: str, headers: Dict[str, str] | None = None) -> Response:
        return self.request("GET", url, headers=headers)

    def post_json(self, url: str, payload: Any, headers: Dict[str, str] | None = None) -> Response:
        return self.request("POST", url, json_body=payload, headers=headers)

    def post_raw(
        self,
        url: str,
        body: str | bytes,
        content_type: str = "application/json;charset=UTF-8",
        headers: Dict[str, str] | None = None,
    ) -> Response:
        data = body.encode("utf-8") if isinstance(body, str) else body
        h = {"Content-Type": content_type}
        h.update(headers or {})
        return self.request("POST", url, body=data, headers=h)


def ok_json(resp: Response) -> Tuple[bool, Any, str]:
    """判断业务是否成功。IC 系统常见：code==0 / code==200 / success==true。"""
    if resp.status == 0:
        return False, None, f"网络错误: {resp.text[:200]}"
    if resp.status >= 400:
        return False, None, f"HTTP {resp.status}: {resp.text[:200]}"
    j = resp.json()
    if j is None:
        return False, None, f"非 JSON 响应: {resp.text[:200]}"
    if not isinstance(j, dict):
        # 有的接口直接返回数组或裸值
        return True, j, "ok"
    code = j.get("code")
    if code in (0, 200, "0", "200", None):
        if j.get("success") is False:
            return False, j, str(j.get("message") or j.get("msg") or "success=false")
        return True, j, "ok"
    return False, j, f"code={code} msg={j.get('message') or j.get('msg') or ''}"
