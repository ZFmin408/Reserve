"""配置持久化 + 默认值（纯标准库）。"""
from __future__ import annotations

import copy
import json
import os
import threading
from typing import Any, Dict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(ROOT, "config.json")
LOG_PATH = os.path.join(ROOT, "logs", "reserve.log")

DEFAULT_CONFIG: Dict[str, Any] = {
    "system": "ic",                 # 适配器: ic | generic
    "base_url": "http://10.20.15.27",
    "account": {
        "username": "",
        "password": "",
    },
    # 会话：留空则由程序自动登录获取。也可以直接把抓包到的值粘进来，跳过登录。
    "session": {
        "auto_login": True,
        "cookie": "",        # 例如 ic-cookie=xxxxxxxx-xxxx-...
        "token": "",         # 例如 eb8c6b2b68594c52be5907ef44977f60
    },
    "target": {
        "space_id": "",      # 房间 ID (roomIds)
        "seat_id": "",       # 座位 ID (devId)
        "seat_name": "",     # 座位名，如 4F-A106
        "reserve_date": "",  # YYYY-MM-DD，留空=预约当天
        # 时段预设：morning(08:00-12:00) / afternoon(13:30-18:00) / evening(19:00-22:00)
        "slot": "afternoon",
        # 以下两个只在 slot 为空时作为兼容回退
        "start_time": "13:30",
        "end_time": "18:00",
        "segment_ids": "",
        "segments": "",
    },
    "schedule": {
        "mode": "at_time",       # at_time | daily | countdown
        "fire_date": "",         # YYYY-MM-DD；留空=今天（时刻已过也只等今天，不推明天）
        "fire_time": "13:30",    # HH:MM:SS 或 YYYY-MM-DD HH:MM:SS
        "countdown_seconds": 0,
        "advance_ms": 0,
        "retry": True,
        "retry_max": 3,          # ⭐ 最多重试次数（避免频繁重复发请求）
        "retry_interval_ms": 3000,   # 重试间隔默认 3 秒，不是高频轰炸
        "retry_timeout_s": 60,
        "prewarm": False,        # 默认关闭预热
        "prewarm_advance_s": 60,
        "stop_on_success": True, # 成功后立即停止
        "keep_running_after_success": False,
    },
    "request": {
        "timeout_s": 8,
        "verify_tls": False,
        "extra_headers": {},     # 覆盖/追加请求头
    },
    "paths": {
        "login": "/ic-web/login/user",
        "public_key": "/ic-web/login/publicKey",
        "seat_free": "/ic-web/seat/freeSeat",
        "space_list": "/ic-web/space/list",
        "booking": "/ic-web/seat/booking",
        "booking_check": "/ic-web/seat/booking/check",
        "user_info": "/ic-web/user/info",
    },
    # 登录请求的形态。code=100/参数错误 基本就是这里或 fields 不对。
    # 实测（2026-10 抓包）：字段平铺、密码 1024 位 RSA PKCS#1 + base64。
    "login": {
        "encoding": "json",      # json | form | query
        "wrap": "",              # 包装层：param / data / loginDto ... 留空=平铺
        "extra_fields": {        # 抓包里实际存在的固定字段
            "captcha": "",
            "consoleType": 16,
            "privacy": True,
        },
        "pwd_mode": "auto_pubkey",  # ⭐ 实测流程：先取公钥+nonce，再 RSA(密码+";"+nonce)
        "rsa_output": "rsa_b64",    # auto_pubkey 的输出编码：rsa_b64 | rsa_hex | rsa_upper
        "nonce_sep": ";",           # 明文拼接符，实测是 ";"
        "send_nonce": False,        # 是否需要把 nonceStr 一起回传
        "rsa_public_key": "",       # 留空即可；auto_pubkey 会自动从接口取
        "rsa_padding": "pkcs1",     # pkcs1 | raw
        # 直接粘贴抓包到的请求体原文，支持 {{username}} {{password_enc}} 等占位符。
        # 填了就忽略 encoding / wrap / 字段名。下面这行是实测抄下来的真实结构。
        "body": "",
        "origin": "",            # 后端校验 Origin 时填，如 http://10.20.15.27
        "referer": "",
    },
    # 字段名映射：实测抓包确认。logonName 多了个 o；密码字段是 password 不是 pwd。
    "fields": {
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
    },
    # 下单参数：全部来自实测 cURL 抓包（POST /ic-web/reserve/seat）
    "booking": {
        "sys_kind": 8,           # 座位系统 = 8（研讨室是 1）
        "member_kind": 1,
        "resv_property": 0,
        "resv_dev": [0],
        "test_name": "",
        "memo": "",
        "captcha": "",
        # 信封加密参数（全部来自 JS 源码 chunk-common 模块 fa7d 的 b() 函数）
        "aes_mode": "cbc",       # JS: CryptoJS.mode.CBC
        "aes_padding": "zero",   # JS: CryptoJS.pad.ZeroPadding
        "aes_iv": "ABCDEF1234123412",   # JS: Utf8.parse("ABCDEF1234123412")
        "aes_key_len": 16,
        "aes_output": "hex_upper",      # JS: ciphertext.toString().toUpperCase()
        "rsa_output": "rsa_b64",
        "rsa_padding": "pkcs1",
    },
    # 重放模板：把抓包到的 POST 原样贴进来，程序只替换 {{占位符}}
    "replay": {        "enabled": False,
        "path": "",
        "content_type": "application/json;charset=UTF-8",
        "body": "",
    },
}

_lock = threading.RLock()
_cache: Dict[str, Any] | None = None
_cache_mtime: float = -1.0


def _deep_merge(base: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in (patch or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _read_file() -> Dict[str, Any]:
    data: Dict[str, Any] = {}
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}
    return data


def _mtime() -> float:
    try:
        return os.path.getmtime(CONFIG_PATH)
    except OSError:
        return -1.0


def load() -> Dict[str, Any]:
    """读配置。会检查文件 mtime：文件被外部改动（手改/别的进程写）时自动重载，
    避免服务进程一直用内存里的旧配置。"""
    global _cache, _cache_mtime
    with _lock:
        disk_mtime = _mtime()
        if _cache is not None and disk_mtime == _cache_mtime:
            return copy.deepcopy(_cache)
        _cache = _deep_merge(DEFAULT_CONFIG, _read_file())
        _cache_mtime = disk_mtime
        return copy.deepcopy(_cache)


def reload() -> Dict[str, Any]:
    """强制丢弃内存缓存，从磁盘重读。"""
    global _cache, _cache_mtime
    with _lock:
        _cache = None
        _cache_mtime = -1.0
    return load()


def save(patch: Dict[str, Any]) -> Dict[str, Any]:
    """合并式保存：patch 里出现的键覆盖，未出现的保留。"""
    global _cache, _cache_mtime
    with _lock:
        current = load()
        merged = _deep_merge(current, patch or {})
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        tmp = CONFIG_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(merged, f, ensure_ascii=False, indent=2)
        os.replace(tmp, CONFIG_PATH)
        _cache = merged
        _cache_mtime = _mtime()
        return copy.deepcopy(merged)


def redact(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """返回给前端时打码敏感字段。"""
    out = copy.deepcopy(cfg)
    for key in ("password",):
        if out.get("account", {}).get(key):
            out["account"][key] = "********"
    if out.get("session", {}).get("token"):
        t = out["session"]["token"]
        out["session"]["token"] = t[:6] + "..." + t[-4:]
    if out.get("session", {}).get("cookie"):
        c = out["session"]["cookie"]
        out["session"]["cookie"] = c[:14] + "..." if len(c) > 17 else "***"
    return out
