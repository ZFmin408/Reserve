"""HAR 解析器：从浏览器导出的 .har 里自动扒出登录包 / 座位列表包 / 预约 POST。

用法：
  1) 浏览器 F12 -> Network -> 右键 -> Save all as HAR with content
     存成   E:\\Project\\Reserve\\capture.har
  2) python tools\\parse_har.py capture.har
     （或 python tools\\parse_har.py           自动找同目录下的 *.har）

输出：
  - 控制台摘要：每条候选请求的完整路径、请求头、请求体、响应体
  - logs/har-report.txt  完整报告
  - logs/har-config-suggestion.json  根据抓到的包反推的配置建议，可直接对照手册填写

纯标准库实现，不依赖任何第三方库。
"""
from __future__ import annotations

import base64
import json
import os
import sys
from typing import Any, Dict, List

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

LOGIN_HINTS = ("login", "logon", "signin", "auth", "token")
BOOKING_HINTS = ("booking", "book", "reserve", "reservation", "order", "submit", "appoint", "seatbook")
SEAT_HINTS = ("freeseat", "seatlist", "seat/free", "seatinfo", "seat/status", "spacelist",
              "space/list", "seatdetail", "seat/detail", "free", "seat")


def load_har(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return json.load(f)


def body_of(entry: Dict[str, Any], which: str) -> str:
    """which in ('request','response')"""
    obj = entry.get(which) or {}
    pd = obj.get("postData") or obj.get("content") or {}
    text = pd.get("text")
    if text is None:
        return ""
    if pd.get("encoding") == "base64":
        try:
            return base64.b64decode(text).decode("utf-8", "replace")
        except Exception:
            return f"<base64 {len(text)} chars>"
    return text


def headers_of(entry: Dict[str, Any], which: str) -> Dict[str, str]:
    obj = entry.get(which) or {}
    return {h.get("name", ""): h.get("value", "") for h in (obj.get("headers") or [])}


def classify(entry: Dict[str, Any]) -> str:
    req = entry.get("request") or {}
    url = (req.get("url") or "")
    low = url.lower()
    method = (req.get("method") or "").upper()
    req_body = body_of(entry, "request")
    if any(h in low for h in LOGIN_HINTS) and method == "POST":
        return "LOGIN"
    if any(h in low for h in BOOKING_HINTS) and method == "POST":
        return "BOOKING"
    if method == "POST" and req_body:
        return "BOOKING"          # 任何带 body 的 POST 都当预约候选
    if any(h in low for h in SEAT_HINTS):
        return "SEATS"
    return "OTHER"


def fmt_entry(entry: Dict[str, Any], kind: str) -> str:
    req = entry.get("request") or {}
    resp = entry.get("response") or {}
    url = req.get("url", "")
    method = req.get("method", "")
    status = resp.get("status", "?")
    mime = ((resp.get("content") or {}).get("mimeType") or "")
    t = entry.get("time")

    out = []
    out.append("-" * 78)
    out.append(f"[{kind}]  {method} {url}")
    out.append(f"状态: HTTP {status}   {mime}   耗时: {t} ms")
    out.append("-" * 78)

    out.append("--- 请求头 ---")
    for k, v in headers_of(entry, "request").items():
        if k.lower() in ("cookie", "authorization", "token") or k.lower().startswith("x-"):
            out.append(f"  {k}: {v}")
    out.append("  <其余请求头见 HAR 原文>")

    rb = body_of(entry, "request")
    out.append("--- 请求体 ---")
    out.append("  " + (rb if rb else "<空>"))

    ob = body_of(entry, "response")
    out.append("--- 响应体 ---")
    out.append("  " + (ob[:4000] if ob else "<空>"))
    out.append("")
    return "\n".join(out)


def guess_config(entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    """从抓到的包反推配置建议。"""
    sug: Dict[str, Any] = {"notes": [], "fields": {}, "paths": {}, "login": {}, "sample_booking": {}}

    for entry in entries:
        kind = classify(entry)
        req = entry.get("request") or {}
        url = req.get("url", "")
        path = "/" + url.split("//", 1)[-1].split("/", 1)[-1] if "//" in url else url
        if "?" in path:
            path = path.split("?", 1)[0]
        ct = headers_of(entry, "request").get("Content-Type", "")
        rb = body_of(entry, "request")

        if kind == "LOGIN":
            sug["paths"]["login"] = path
            if "form-urlencoded" in ct.lower():
                sug["login"]["encoding"] = "form"
                sug["notes"].append("登录用的是 form-urlencoded，不是 JSON -> login.encoding 设为 form")
            else:
                sug["login"]["encoding"] = "json"
            try:
                obj = json.loads(rb) if rb.startswith("{") else dict(
                    kv.split("=", 1) for kv in rb.split("&") if "=" in kv)
            except Exception:
                obj = {}
            for key in obj:
                k = key.lower()
                if k in ("username", "logname", "loginname", "account", "cardno", "readerno",
                         "userno", "studentno", "name", "id"):
                    sug["fields"]["login_user"] = key
                if k in ("password", "pwd", "passwd", "userpwd", "loginpwd", "pass"):
                    sug["fields"]["login_pass"] = key
                    val = str(obj[key])
                    if len(val) in (32, 40, 64) and all(c in "0123456789abcdefABCDEF" for c in val):
                        mode = {32: "md5", 40: "sha1", 64: "sha256"}[len(val)]
                        sug["login"]["pwd_mode"] = mode
                        sug["notes"].append(f"密码字段是 {len(val)} 位 hex -> login.pwd_mode 设为 {mode}")
            # 包装层
            if len(obj) == 1:
                only = list(obj)[0]
                if isinstance(obj[only], dict):
                    sug["login"]["wrap"] = only
                    sug["notes"].append(f"登录体被包在 {{\"{only}\": {{...}}}} 里 -> login.wrap 设为 {only}")
            resp_text = body_of(entry, "response")
            for tok in ("token", "accessToken", "access_token"):
                if f'"{tok}"' in resp_text:
                    sug["notes"].append(f"响应里发现 token 字段: {tok}")
                    break
            sug["notes"].append("检查响应里 userId / libId 的字段名，填进 fields.user_id / fields.lib_id")

        elif kind == "BOOKING":
            sug["paths"]["booking"] = path
            sug["sample_booking"] = {"path": path, "content_type": ct, "body": rb,
                                     "response": body_of(entry, "response")[:1500]}
            try:
                obj = json.loads(rb)
            except Exception:
                obj = {}
            for key in obj:
                k = key.lower()
                if k in ("seatid", "id"):
                    sug["fields"]["seat_id"] = key
                if k in ("spaceid", "space"):
                    sug["fields"]["space_id"] = key
                if k in ("segmentids", "segments", "segmentid", "segment"):
                    sug["notes"].append(f"预约用时段字段 {key} -> 填 target.segment_ids 或 segments")
                if k in ("starttime", "begintime", "start"):
                    sug["notes"].append(f"预约用起止时间字段 {key} -> 需要按格式填 target.start_time")
                if k in ("userid", "readerid", "cardno"):
                    sug["fields"]["user_id"] = key
            sug["notes"].append("★ 预约请求体已抓到，最稳做法：把它整段粘进界面「重放模板」，"
                                "数字字段换成 {{seat_id}} {{space_id}} 占位符")
            sug["notes"].append(f"★ 失败判定: 成功响应 = {body_of(entry, 'response')[:200]}")

        elif kind == "SEATS":
            sug["paths"].setdefault("seat_free", path)
            resp_text = body_of(entry, "response")
            for key in ("seatId", "seatName", "spaceId", "seatNo"):
                if f'"{key}"' in resp_text:
                    sug["notes"].append(f"座位响应里发现字段 {key}")
            if not sug["fields"].get("seat_id") and '"seatId"' in resp_text:
                sug["fields"]["seat_id"] = "seatId"

    return sug


def main():
    args = sys.argv[1:]
    har_path = args[0] if args else ""
    if not har_path:
        cands = [f for f in os.listdir(ROOT) if f.lower().endswith(".har")]
        if not cands:
            print("用法: python tools\\parse_har.py <文件.har>")
            print(f"未在 {ROOT} 找到 .har 文件。先在浏览器 F12 -> Network -> 右键 -> Save all as HAR with content")
            return
        har_path = os.path.join(ROOT, cands[0])
        print(f"自动选用: {har_path}")

    har = load_har(har_path)
    entries = (har.get("log") or {}).get("entries") or []
    print(f"共 {len(entries)} 条请求\n")

    buckets: Dict[str, List[Dict[str, Any]]] = {"LOGIN": [], "SEATS": [], "BOOKING": [], "OTHER": []}
    for e in entries:
        buckets[classify(e)].append(e)

    report = []
    for key in ("LOGIN", "BOOKING", "SEATS", "OTHER"):
        items = buckets[key]
        report.append("#" * 78)
        report.append(f"# {key}  ({len(items)} 条)")
        report.append("#" * 78)
        for e in items[:12]:
            report.append(fmt_entry(e, key))
        report.append("")

    text = "\n".join(report)
    print(text[:20000])

    os.makedirs(os.path.join(ROOT, "logs"), exist_ok=True)
    with open(os.path.join(ROOT, "logs", "har-report.txt"), "w", encoding="utf-8") as f:
        f.write(text)

    sug = guess_config(entries)
    with open(os.path.join(ROOT, "logs", "har-config-suggestion.json"), "w", encoding="utf-8") as f:
        json.dump(sug, f, ensure_ascii=False, indent=2)

    print("\n" + "=" * 78)
    print("  配置建议（已写入 logs/har-config-suggestion.json）")
    print("=" * 78)
    print(json.dumps(sug, ensure_ascii=False, indent=2))
    print(f"\n完整报告: {os.path.join(ROOT, 'logs', 'har-report.txt')}")
    print("把这两个文件（或直接把这个 .har）发我，我一次把配置定死。")


if __name__ == "__main__":
    main()
