"""配置持久化 + 默认值（纯标准库）。"""
from __future__ import annotations

import copy
import json
import os
import sys
import threading
from typing import Any, Dict


def _resolve_dirs() -> tuple:
    """返回 (ROOT, DATA_DIR)。

    ROOT      —— 程序所在目录（开发环境是项目目录；打包后是 exe 所在目录）
    DATA_DIR  —— 可写目录，用于放 config.json 和 logs/

    打包后双击 exe 时，exe 可能被放在 C:\\Program Files 这类只读位置，
    所以先探测 exe 同目录能否写入；不能写就退到 %APPDATA%。
    """
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        root = exe_dir
    else:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        exe_dir = root

    def _writable(d: str) -> bool:
        probe = os.path.join(d, ".write_test")
        try:
            os.makedirs(d, exist_ok=True)
            with open(probe, "w", encoding="utf-8") as f:
                f.write("x")
            os.remove(probe)
            return True
        except OSError:
            return False

    # 优先级：exe 同目录（便携式）> 用户 AppData
    if _writable(exe_dir):
        return root, exe_dir

    appdata = os.environ.get("APPDATA") or os.path.expanduser("~")
    fallback = os.path.join(appdata, "SeatReserver")
    if _writable(fallback):
        return root, fallback

    # 最后退回临时目录，保证程序仍能启动
    import tempfile
    fallback = os.path.join(tempfile.gettempdir(), "SeatReserver")
    os.makedirs(fallback, exist_ok=True)
    return root, fallback


ROOT, DATA_DIR = _resolve_dirs()

# 允许用环境变量指定配置文件路径。
# 用途：测试/多实例/便携模式，避免写到默认位置（exe 同目录）。
# 例如：set RESERVE_CONFIG=D:\tmp\test.json && Reserve.exe
_ENV_CONFIG = os.environ.get("RESERVE_CONFIG", "").strip()
if _ENV_CONFIG:
    _ENV_CONFIG = os.path.abspath(_ENV_CONFIG)
    CONFIG_PATH = _ENV_CONFIG
    DATA_DIR = os.path.dirname(_ENV_CONFIG) or DATA_DIR
    LOG_PATH = os.path.join(DATA_DIR, "logs", "reserve.log")
else:
    CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
    LOG_PATH = os.path.join(DATA_DIR, "logs", "reserve.log")

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
        "acc_no": None,      # 登录成功后自动缓存，下单 payload 的 appAccNo 用它
    },
    "target": {
        "space_id": "",      # 房间 ID (roomIds)
        "seat_id": "",       # 座位 ID (devId)
        "seat_name": "",     # 座位名，如 1F-A001
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
        "retry_max": None,       # null = 不限次数，只受 retry_timeout_s 约束
        "retry_interval_ms": 2000,   # 重试间隔 2 秒：贴近时间窗口开放瞬间，又不高频轰炸
        "retry_timeout_s": 1800,     # 全局上限 30 分钟，防止无限打
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
    # 接口路径。全部为实测值 —— 别再改回猜的值，改错就是 404。
    "paths": {
        "login": "/ic-web/login/user",
        "public_key": "/ic-web/login/publicKey",
        "booking": "/ic-web/reserve/seat",          # ★ 实测：下单端点
        "room_list": "/ic-web/room/getAll",         # 列房间
        "seat_list": "/ic-web/reserve",             # ★ 实测：查座位
        "room_open": "/ic-web/seatRoom/openScope",  # 开放时段
        "seat_map": "/ic-web/seatDevice/coordinate",
        "account_info": "/ic-web/account/info",     # 取 accNo
        # 以下为其它系统的可能端点，保留兼容
        "seat_free": "/ic-web/seat/freeSeat",
        "space_list": "/ic-web/space/list",
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
    "replay": {
        "enabled": False,
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


def acct_hash(cfg: Dict[str, Any]) -> str:
    """账号指纹：服务器地址 + 学号 + 密码。

    用途：判断会话（token/accNo）是否还属于当前账号。
    改账号后如果继续用旧 token，就会「以旧账号身份下单」——
    accNo 是下单 payload 里的 appAccNo，直接决定约在谁名下。
    """
    import hashlib
    acc = cfg.get("account") or {}
    raw = str(cfg.get("base_url") or "").strip().rstrip("/")
    # 补 http://，与 adapters.normalize_base_url 保持等价
    if raw and "://" not in raw:
        raw = "http://" + raw.lstrip("/")
    key = f"{raw}|{acc.get('username')}|{acc.get('password')}"
    return hashlib.md5(key.encode("utf-8")).hexdigest()


def save(patch: Dict[str, Any], maintain: bool = False) -> Dict[str, Any]:
    """合并式保存：patch 里出现的键覆盖，未出现的保留。

    maintain=True 表示「程序内部维护会话」（保存 token/accNo），
                 此时不做账号变更检测 —— 否则刚登录拿到的 token
                 会被自己清掉。

    maintain=False（默认，用户改配置）时额外做两件保护：
      1) account 里没带的字段沿用旧值 —— 否则前端某条路径只传 username 时
         会把已存的密码清掉。
      2) 账号指纹（地址/学号/密码）变化时**清空 session 的 token 与 acc_no**，
         否则会继续用上一个账号的 token 下单。
    """
    global _cache, _cache_mtime
    with _lock:
        current = load()
        patch = copy.deepcopy(patch or {})

        if not maintain:
            # 1) account 增量合并：没传的字段（尤其 password）保留旧值。
            #    只认「键不存在」—— 显式传空串仍然会覆盖（用户可能真想清空）。
            if isinstance(patch.get("account"), dict):
                old_acc = current.get("account") or {}
                for k, v in old_acc.items():
                    if k not in patch["account"]:
                        patch["account"][k] = v

            # 2) 账号变了 -> 旧会话作废。
            #    仅在**旧配置本来就有学号**时才判定为「换账号」。
            #    否则全新安装（DEFAULT_CONFIG 里 username 是空串）的首次保存
            #    会被误判成「账号已变更」，把刚存的 token 清掉。
            if isinstance(patch.get("account"), dict) or "base_url" in patch:
                old_u = ((current.get("account") or {}).get("username") or "").strip()
                if old_u:
                    try:
                        before = acct_hash(current)
                    except Exception:
                        before = ""
                    probe = _deep_merge(current, patch)
                    try:
                        after = acct_hash(probe)
                    except Exception:
                        after = before
                    if before != after:
                        new_u = (probe.get("account") or {}).get("username") or "(空)"
                        # 清掉属于旧账号的凭证
                        patch.setdefault("session", {})
                        if isinstance(patch["session"], dict):
                            patch["session"]["token"] = ""
                            patch["session"]["acc_no"] = None
                            patch["session"]["account_hash"] = ""
                        try:
                            import logging
                            logging.getLogger("reserve").warning(
                                "账号已变更（%s -> %s），已清除旧登录状态", old_u, new_u)
                        except Exception:
                            pass

        merged = _deep_merge(current, patch)
        merged["session"] = merged.get("session") or {}
        if isinstance(merged["session"], dict):
            merged["session"]["account_hash"] = acct_hash(merged)

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
