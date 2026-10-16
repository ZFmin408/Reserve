"""启动入口。

开发环境：  python run.py
打包成 exe：双击即可（PyInstaller 会把本文件作为入口）

行为：
- 自动选择空闲端口（8765 被占用会顺延）
- 自动打开浏览器到"实际监听的那个端口"
- 打包成 exe 后双击运行时，若失败会打印错误并等待按键，避免窗口一闪而过
"""
from __future__ import annotations

import os
import sys

# 打包后是 sys._MEIPASS，开发环境是本文件所在目录
if getattr(sys, "frozen", False):
    BASE = os.path.dirname(sys.executable)
    sys.path.insert(0, getattr(sys, "_MEIPASS", BASE))
else:
    BASE = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, BASE)

FROZEN = getattr(sys, "frozen", False)


def _open_browser_later(server) -> None:
    import threading
    import time

    def _open():
        # 等端口真正就绪（最多 10 秒）
        for _ in range(100):
            port = server.current_port()
            if port:
                break
            time.sleep(0.1)
        port = server.current_port() or 0
        if port:
            try:
                import webbrowser
                webbrowser.open(f"http://{server.HOST}:{port}/")
            except Exception:
                pass

    threading.Thread(target=_open, daemon=True).start()


def _pause_if_frozen() -> None:
    """打包后双击运行：出错时不要让窗口瞬间消失。"""
    if not FROZEN:
        return
    try:
        input("\n按回车键退出…")
    except (EOFError, KeyboardInterrupt):
        pass


def main() -> int:
    try:
        from app import server
    except Exception as e:
        print(f"[错误] 加载程序失败: {e!r}")
        _pause_if_frozen()
        return 1

    if os.environ.get("RESERVE_NO_BROWSER") != "1":
        _open_browser_later(server)

    try:
        server.main()
    except SystemExit as e:
        print(f"[错误] {e}")
        _pause_if_frozen()
        return 1
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"\n[错误] 程序异常退出: {e!r}")
        _pause_if_frozen()
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
