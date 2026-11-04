#!/usr/bin/env python3
"""Start SendEZ on Windows, macOS or Linux.

Double-click start-windows.bat, start-mac.command or start-linux.sh (or run `python run.py`).
The first run creates a private Python environment in .venv and installs everything (needs internet once).
"""
import getpass
import os
import socket
import subprocess
import sys
import threading
import venv
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
REQ = ROOT / "requirements-app.txt"


def venv_python() -> Path:
    return VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def ensure_environment() -> None:
    if sys.version_info < (3, 10):
        print("SendEZ needs Python 3.10 or newer. Download it from https://www.python.org/downloads/")
        input("Press Enter to close...")
        sys.exit(1)
    if os.environ.get("SEALSEND_NO_VENV") or Path(sys.prefix).resolve() == VENV.resolve():
        return
    if not venv_python().exists():
        print("First run: preparing SendEZ (about 1-3 minutes, needs internet once)...")
        venv.EnvBuilder(with_pip=True).create(VENV)
    marker = VENV / ".installed"
    if not marker.exists() or marker.read_text() != REQ.read_text():
        print("Installing components...")
        subprocess.check_call([str(venv_python()), "-m", "pip", "install", "--disable-pip-version-check", "-q",
                               "--timeout", "60", "--retries", "10", "-r", str(REQ)])
        marker.write_text(REQ.read_text())
    sys.exit(subprocess.call([str(venv_python()), str(ROOT / "run.py"), *sys.argv[1:]]))


def free_port(start: int) -> int:
    for port in range(start, start + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("0.0.0.0", port))
                return port
            except OSError:
                continue
    raise SystemExit(f"No free port between {start} and {start + 19}")


# ---------------------------------------------------------------- free public link (Cloudflare Quick Tunnel)
def start_tunnel(port: int):
    sys.path.insert(0, str(ROOT))
    from sealsend.tunnel import TunnelError, start_tunnel as _start
    print("Opening your free public link (the first time this downloads Cloudflare's tool, 20-55 MB)...")
    try:
        return _start(port, ROOT / ".tools")
    except TunnelError as e:
        raise SystemExit(f"{e} Or use the normal start file.")


def ensure_password() -> None:
    from sealsend import settings
    if settings.admin_configured():
        return
    print("\nYour link will be public, so set a password first (8+ characters).")
    while True:
        a = getpass.getpass("  New password: ")
        b = getpass.getpass("  Repeat it:    ")
        if len(a) >= 8 and a == b:
            settings.set_admin_password(a)
            print("  Password saved.\n")
            return
        print("  Passwords must match and be at least 8 characters. Try again.")


def main() -> None:
    ensure_environment()
    online = "--online" in sys.argv
    port = free_port(int(os.environ.get("SEALSEND_PORT", "8000")))
    os.environ["SEALSEND_PORT"] = str(port)
    sys.path.insert(0, str(ROOT))
    tunnel = None
    if online:
        tunnel, public = start_tunnel(port)
        os.environ["SEALSEND_HOSTED_URL"] = public  # links and QR codes use the public address
        os.environ["SEALSEND_TEMPORARY_URL"] = "1"
    import uvicorn
    from sealsend import config

    local = f"http://localhost:{port}"
    if online:
        ensure_password()
        print("\n  SendEZ is ONLINE\n")
        print(f"  Your link (works on any device, anywhere):\n\n      {public}\n")
        print("  The link stays the same until you close this window. Next time you start, you get a new link.")
        print("  Keep this computer on and this window open while people use it.\n")
    else:
        print("\n  SendEZ is running\n")
        print(f"  On this computer:      {local}")
        print(f"  Phone/iPad (same Wi-Fi): http://{config.lan_ip()}:{port}")
        print("\n  Keep this window open while you use SendEZ. Close it (or press Ctrl+C) to stop.\n")
    if not os.environ.get("SEALSEND_NO_BROWSER"):
        threading.Timer(1.5, lambda: webbrowser.open(public if online else local)).start()
    try:
        uvicorn.run("sealsend.main:app", host="127.0.0.1" if online else "0.0.0.0", port=port, log_level="warning",
                    proxy_headers=True, forwarded_allow_ips="127.0.0.1")
    finally:
        if tunnel:
            tunnel.terminate()


if __name__ == "__main__":  # required on Windows/macOS: worker processes re-import this file
    main()
