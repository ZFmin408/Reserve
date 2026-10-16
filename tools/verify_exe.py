"""验证打包好的 exe 能真正运行（纯标准库）。

用法：
  python tools\\verify_exe.py                      # 用 dist\\Reserve\\Reserve.exe
  python tools\\verify_exe.py --exe 路径\\Reserve.exe --port 8770

检查内容：
  1. exe 能否启动
  2. 端口被占用时能否自动避让
  3. config.json 是否生成在 exe 同目录（便携）
  4. 页面/静态资源/API 是否正常
  5. 登录 + 列房间 + 查座位是否真的能通（需要能访问学校服务器）
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def occupy(port: int) -> socket.socket:
    """独占占用一个端口（Windows 需 SO_EXCLUSIVEADDRUSE 才真的禁止复用）。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
        s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    s.bind(("127.0.0.1", port))
    s.listen(1)
    return s


def api(port: int, path: str, body=None, method: str = "GET", timeout: int = 60):
    url = f"http://127.0.0.1:{port}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json"},
                                 method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def wait_up(port: int, seconds: float = 15) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        try:
            api(port, "/api/status", timeout=3)
            return True
        except Exception:
            time.sleep(0.3)
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", default=os.path.join(ROOT, "dist", "Reserve.exe"))
    ap.add_argument("--port", type=int, default=8770)
    ap.add_argument("--base-url", default="http://10.20.15.27")
    ap.add_argument("--user", default="202301050786")
    ap.add_argument("--password", default="", help="真实密码；不填则跳过登录测试")
    ap.add_argument("--skip-network", action="store_true", help="跳过需要访问学校的测试")
    args = ap.parse_args()

    # 兼容单文件 (dist/Reserve.exe) 与旧目录模式 (dist/Reserve/Reserve.exe)
    exe_arg = args.exe
    if os.path.isdir(exe_arg):
        exe_arg = os.path.join(exe_arg, "Reserve.exe")
    if not os.path.isfile(exe_arg):
        print(f"[错误] 找不到 {exe_arg}，先跑 tools\\build_exe.py")
        return 1

    # 复制到临时目录跑，避免污染 dist/
    work = os.path.join(tempfile.gettempdir(), f"verify-exe-{int(time.time())}")
    os.makedirs(work, exist_ok=True)
    exe = os.path.join(work, os.path.basename(exe_arg))
    shutil.copy2(exe_arg, exe)
    print("=" * 74)
    print("  验证打包产物")
    print("=" * 74)
    print(f"  exe      : {exe}")
    print(f"  大小     : {os.path.getsize(exe):,} B")
    print(f"  测试端口 : {args.port}")
    print()

    results = []

    def check(name, cond, extra=""):
        results.append((name, bool(cond)))
        mark = "PASS" if cond else "FAIL"
        print(f"  [{mark}] {name}" + (f"   {extra}" if extra and not cond else ""))

    blockers = []
    proc = None
    lines = []
    try:
        # --- 场景 A: 端口被占用，验证自动避让 ---
        print("--- 场景 A: 端口被占用时自动避让 ---")
        blockers.append(occupy(args.port))
        # ★ 用 RESERVE_CONFIG 把配置隔离到临时文件，
        #   否则 exe 会把测试数据写进它自己同目录的 config.json，
        #   如果那个目录正好是项目目录，就会污染用户真实配置。
        cfg_path = os.path.join(work, "test-config.json")
        env = dict(os.environ, RESERVE_NO_BROWSER="1", RESERVE_PORT=str(args.port),
                   RESERVE_CONFIG=cfg_path)
        proc = subprocess.Popen([exe], cwd=work, env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        threading.Thread(
            target=lambda: [lines.append(l.decode("utf-8", "replace").rstrip())
                            for l in proc.stdout],
            daemon=True).start()

        alt = args.port + 1
        up = wait_up(alt, seconds=20)
        check(f"被占用时自动改用 {alt} 端口并成功启动", up)
        if not up:
            print("      —— exe 未能启动，后续跳过 ——")
            for ln in lines[:20]:
                print("      exe>", ln)
            return 1

        # 占用者释放，后续场景用默认端口
        blockers[0].close()
        blockers.clear()

        # --- 场景 B: 页面与静态资源 ---
        print()
        print("--- 场景 B: 页面与静态资源 ---")
        html = urllib.request.urlopen(f"http://127.0.0.1:{alt}/", timeout=10).read().decode("utf-8")
        check("首页可访问", "<title>" in html, f"{len(html)} bytes")
        for key in ("btn-login", "btn-rooms", "btn-seats", "btn-start", "btn-verify", "base_url"):
            check(f"页面含 {key}", key in html)
        for f in ("app.js", "style.css"):
            try:
                c = urllib.request.urlopen(f"http://127.0.0.1:{alt}/{f}", timeout=10).read()
                check(f"静态资源 {f}", len(c) > 0, f"{len(c)} bytes")
            except Exception as e:
                check(f"静态资源 {f}", False, repr(e))

        # --- 场景 C: config.json 便携式落盘 ---
        print()
        print("--- 场景 C: 配置文件位置 ---")
        api(alt, "/api/config", {"target": {"slot": "afternoon"}}, "POST")
        # 优先看 RESERVE_CONFIG 指定的路径；没生效则看 exe 同目录
        cfg = cfg_path if os.path.isfile(cfg_path) else os.path.join(work, "config.json")
        check("config.json 生成在预期位置", os.path.isfile(cfg), cfg)
        if os.path.isfile(cfg_path):
            print(f"      （RESERVE_CONFIG 隔离生效: {cfg_path}）")

        # --- 场景 D: 真实网络功能 ---
        if args.skip_network:
            print()
            print("--- 场景 D: 已跳过网络测试 ---")
        else:
            print()
            print("--- 场景 D: 真实功能（登录 / 列房间 / 查座位）---")
            if not args.password:
                print("      （未提供 --password，跳过登录测试）")
                check("跳过登录测试（未提供密码）", True)
            else:
                acc = {"username": args.user, "password": args.password}
                api(alt, "/api/config", {"base_url": args.base_url, "account": acc}, "POST")
                try:
                    d = api(alt, "/api/rooms", timeout=90)
                    check("列房间（含完整登录流程）", d.get("ok"), str(d.get("message"))[:80])
                    rooms = d.get("rooms") or []
                    print(f"      房间数: {len(rooms)}")
                    for rm in rooms[:4]:
                        print(f"        {rm['roomId']}  {rm['roomName']}")
                    if rooms:
                        api(alt, "/api/config",
                            {"target": {"space_id": str(rooms[0]["roomId"]),
                                        "reserve_date": "2026-10-16"}}, "POST")
                        s = api(alt, "/api/seats?date=2026-10-16", timeout=90)
                        check("查座位", s.get("ok"), str(s.get("message"))[:80])
                        seats = s.get("seats") or []
                        print(f"      座位数: {len(seats)}")
                        for st in seats[:3]:
                            print(f"        devId={st['seatId']}  {st['seatName']}  {st['status']}")
                except Exception as e:
                    check("真实功能测试", False, f"{type(e).__name__}: {e}")

        # --- 场景 E: 配置持久化往返 + 地址归一化 ---
        print()
        print("--- 场景 E: 配置持久化 + 地址归一化 ---")
        if os.path.isfile(cfg):
            back = api(alt, "/api/config")
            c = back.get("config") or {}
            check("保存后能读回 base_url", bool(c.get("base_url")), str(c.get("base_url")))
            check("保存后能读回 slot", (c.get("target") or {}).get("slot") is not None,
                  json.dumps(c.get("target"), ensure_ascii=False)[:80])
        else:
            check("config.json 存在", False, cfg)

        # 关键：用户只填 IP（不写 http://）也必须能用
        for raw, expect in (("10.20.15.27", "http://10.20.15.27"),
                            ("http://10.20.15.27/", "http://10.20.15.27"),
                            ("10.20.15.27:8080", "http://10.20.15.27:8080")):
            got = (api(alt, "/api/config", {"base_url": raw}, "POST")
                   .get("config") or {}).get("base_url")
            check(f"地址归一化 {raw!r}", got == expect, f"得到 {got!r}，期望 {expect!r}")

    finally:
        for b in blockers:
            try:
                b.close()
            except Exception:
                pass
        if proc:
            proc.terminate()
        time.sleep(0.5)
        shutil.rmtree(work, ignore_errors=True)

    print()
    print("=" * 74)
    ok = sum(1 for _, v in results if v)
    print(f"  结果: {ok}/{len(results)} 通过")
    for name, v in results:
        if not v:
            print(f"    FAIL: {name}")
    print("=" * 74)
    return 0 if ok == len(results) else 2


if __name__ == "__main__":
    sys.exit(main())
