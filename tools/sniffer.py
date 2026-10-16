"""抓包中继（纯标准库）。

用途：当你无法从 F12 / 抓包工具里拿到预约 POST 的原文时，用它做一次中间人，
把浏览器（或 App）发出的请求原样记录下来，同时转发给真实的图书馆服务器。

用法：
  1) python tools/sniffer.py                      # 默认监听 127.0.0.1:8800 -> 10.20.15.27
  2) 浏览器装个代理插件（如 SwitchyOmega），HTTP 代理指向 127.0.0.1:8800
  3) 在浏览器里正常走一遍「登录 -> 选座 -> 提交预约」
  4) 查看 logs/capture-*.txt，里面就是逐字节的请求与响应
  5) 把预约那一条 POST 的 body 粘进网页的「重放模板」

说明：只支持 HTTP。若目标站点是 HTTPS 且你无法装根证书，请改用 F12 或 Charles/Fiddler/mitmproxy。
"""
from __future__ import annotations

import os
import socket
import sys
import threading
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

UPSTREAM_HOST = os.environ.get("SNIFF_UPSTREAM_HOST", "10.20.15.27")
UPSTREAM_PORT = int(os.environ.get("SNIFF_UPSTREAM_PORT", "80"))
LISTEN_HOST = os.environ.get("SNIFF_LISTEN_HOST", "127.0.0.1")
LISTEN_PORT = int(os.environ.get("SNIFF_LISTEN_PORT", "8800"))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR = os.path.join(ROOT, "logs")
_lock = threading.Lock()
_session_file = os.path.join(LOG_DIR, f"capture-{datetime.now().strftime('%Y%m%d-%H%M%S')}.txt")


def record(title: str, data: str) -> None:
    with _lock:
        os.makedirs(LOG_DIR, exist_ok=True)
        with open(_session_file, "a", encoding="utf-8") as f:
            f.write(f"\n{'=' * 78}\n# {datetime.now().isoformat(timespec='milliseconds')}  {title}\n{'=' * 78}\n")
            f.write(data)
            if not data.endswith("\n"):
                f.write("\n")
    print(f"  -> {title}", flush=True)


def read_headers(sock: socket.socket) -> bytes:
    buf = b""
    while b"\r\n\r\n" not in buf and len(buf) < 65536:
        chunk = sock.recv(4096)
        if not chunk:
            break
        buf += chunk
    return buf


def head_of(raw: bytes) -> dict:
    text = raw.split(b"\r\n\r\n", 1)[0].decode("iso-8859-1", "replace")
    lines = text.split("\r\n")
    hdrs = {}
    for line in lines[1:]:
        if ":" in line:
            k, v = line.split(":", 1)
            hdrs[k.strip().lower()] = v.strip()
    return {"request_line": lines[0] if lines else "", "headers": hdrs}


def handle(client: socket.socket) -> None:
    try:
        raw = read_headers(client)
        if not raw:
            return
        meta = head_of(raw)
        req_line = meta["request_line"]
        hdrs = meta["headers"]

        # 读取请求体
        body = b""
        if raw.split(b"\r\n\r\n", 1).__len__() > 1:
            body = raw.split(b"\r\n\r\n", 1)[1]
        clen = int(hdrs.get("content-length") or 0)
        while len(body) < clen:
            chunk = client.recv(clen - len(body))
            if not chunk:
                break
            body += chunk

        head_text = raw.split(b"\r\n\r\n", 1)[0].decode("iso-8859-1", "replace")
        body_text = body.decode("utf-8", "replace")
        record(f"REQUEST  {req_line}", head_text + "\n\n[body]\n" + body_text)

        # 转发
        up = socket.create_connection((UPSTREAM_HOST, UPSTREAM_PORT), timeout=15)
        forward_head = raw.split(b"\r\n\r\n", 1)[0] + b"\r\n\r\n"
        up.sendall(forward_head + body)

        resp = read_headers(up)
        # 按 chunked 或 content-length 读完整响应（简化处理）
        resp_body = resp.split(b"\r\n\r\n", 1)[1] if b"\r\n\r\n" in resp else b""
        rh = head_of(resp)["headers"]
        if "content-length" in rh:
            need = int(rh["content-length"])
            while len(resp_body) < need:
                chunk = up.recv(need - len(resp_body))
                if not chunk:
                    break
                resp_body += chunk
        elif "chunked" in (rh.get("transfer-encoding") or ""):
            up.settimeout(3)
            while True:
                try:
                    chunk = up.recv(8192)
                except Exception:
                    break
                if not chunk:
                    break
                resp_body += chunk
                if resp_body.endswith(b"0\r\n\r\n"):
                    break
        up.close()

        client.sendall(resp.split(b"\r\n\r\n", 1)[0] + b"\r\n\r\n" + resp_body)

        try:
            import gzip
            import io
            import zlib

            rb = resp_body
            enc = (rh.get("content-encoding") or "").lower()
            if "gzip" in enc:
                rb = gzip.GzipFile(fileobj=io.BytesIO(resp_body)).read()
            elif "deflate" in enc:
                try:
                    rb = zlib.decompress(resp_body)
                except Exception:
                    pass
            record(f"RESPONSE {req_line}  status={resp.split(b' ')[1].decode('iso-8859-1', 'replace') if b' ' in resp else '?'}",
                   rb.decode("utf-8", "replace")[:20000])
        except Exception:
            record(f"RESPONSE(binary) {req_line}", f"<{len(resp_body)} bytes>")
    except Exception as e:
        record("ERROR", repr(e))
    finally:
        try:
            client.close()
        except Exception:
            pass


def main():
    os.makedirs(LOG_DIR, exist_ok=True)
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((LISTEN_HOST, LISTEN_PORT))
    srv.listen(64)
    print("=" * 70)
    print(f"  抓包中继已启动: {LISTEN_HOST}:{LISTEN_PORT}  ->  {UPSTREAM_HOST}:{UPSTREAM_PORT}")
    print(f"  浏览器 HTTP 代理设置为 {LISTEN_HOST}:{LISTEN_PORT}")
    print(f"  记录文件: {_session_file}")
    print("=" * 70)
    try:
        while True:
            client, _ = srv.accept()
            threading.Thread(target=handle, args=(client,), daemon=True).start()
    except KeyboardInterrupt:
        print("\n退出。记录文件:", _session_file)


if __name__ == "__main__":
    main()
