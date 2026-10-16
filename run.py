"""启动入口：python run.py  （也可 python -m app.server）"""
import os
import sys
import webbrowser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if __name__ == "__main__":
    from app import server

    if os.environ.get("RESERVE_NO_BROWSER") != "1":
        import threading
        import time

        def _open():
            time.sleep(1.0)
            webbrowser.open(f"http://{server.HOST}:{server.PORT}/")

        threading.Thread(target=_open, daemon=True).start()

    server.main()
