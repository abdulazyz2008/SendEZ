"""SendEZ desktop app: double-click to open SendEZ in its own window. No Python install needed
when packaged with PyInstaller (see packaging/sealsend.spec).

    SendEZ            open the app window (starts the local server)
    SendEZ --server-only [--port N]   run just the server (used by automated tests of the built app)
"""
import json
import multiprocessing
import os
import socket
import sys
import threading
import time
import urllib.request
import webbrowser


def _free_port(start: int = 8000) -> int:
    for port in range(start, start + 50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise SystemExit("No free port for SendEZ")


def _healthy(port: int, timeout: float = 1.0) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=timeout) as r:
            return json.loads(r.read()).get("ok") is True
    except Exception:
        return False


def _arg(name: str, default: str = "") -> str:
    if name in sys.argv:
        i = sys.argv.index(name)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return default


def _open_window(url: str, on_closed=None) -> bool:
    """Native window (WebKit on macOS, Edge WebView2 on Windows). Returns False if unavailable."""
    try:
        import webview
    except Exception:
        return False
    try:
        webview.settings["ALLOW_DOWNLOADS"] = True
        webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
        window = webview.create_window("SendEZ", url, width=1280, height=860, min_size=(420, 600))
        if on_closed:
            window.events.closed += on_closed
        webview.start()  # blocks until the window is closed (must run on the main thread)
        return True
    except Exception:
        return False


def main() -> None:
    multiprocessing.freeze_support()  # the packaged app renders large batches in worker processes
    for name in ("stdout", "stderr"):  # windowed apps have no console: give print/logging somewhere to write
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, "w"))
    server_only = "--server-only" in sys.argv
    os.environ.setdefault("SEALSEND_LOCAL_ONLY", "1")  # listen on this computer only; Share online is the way in
    os.environ.setdefault("SEALSEND_DESKTOP", "1")

    from sealsend import config  # data folder is chosen here (user's Application Support / AppData when packaged)

    marker = config.DATA_DIR / "desktop.json"
    if not server_only and marker.exists():  # already running: just show it
        try:
            port = int(json.loads(marker.read_text())["port"])
        except Exception:
            port = 0
        if port and _healthy(port):
            url = f"http://127.0.0.1:{port}/"
            if not _open_window(url):
                webbrowser.open(url)
            return

    port = int(_arg("--port") or 0) or _free_port(int(os.environ.get("SEALSEND_PORT", "8000")))
    os.environ["SEALSEND_PORT"] = str(port)
    config.PORT = port

    import uvicorn
    from sealsend.main import app

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", log_config=None,
                                           proxy_headers=True, forwarded_allow_ips="127.0.0.1"))
    if server_only:
        print(f"SendEZ server on http://127.0.0.1:{port}", flush=True)
        server.run()
        return

    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 60
    while not _healthy(port) and time.time() < deadline:
        time.sleep(0.2)
    marker.write_text(json.dumps({"port": port, "pid": os.getpid()}))
    url = f"http://127.0.0.1:{port}/"
    try:
        if not _open_window(url):
            webbrowser.open(url)  # fallback: the normal browser; quit SendEZ from the Dock / taskbar
            thread.join()
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        try:
            marker.unlink()
        except OSError:
            pass


if __name__ == "__main__":  # required for multiprocessing in packaged apps on Windows and macOS
    main()
