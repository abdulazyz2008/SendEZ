"""Free public link through a Cloudflare Quick Tunnel (no account needed).

Used by the in-app "Share online" button and by `run.py --online`. The `cloudflared` tool is
downloaded once from Cloudflare's official GitHub releases into the data folder.
"""
import platform
import re
import shutil
import subprocess
import tarfile
import threading
import time
import urllib.request
from pathlib import Path

URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
TUNNEL_ASSETS = {
    ("windows", "amd64"): "cloudflared-windows-amd64.exe", ("windows", "x86_64"): "cloudflared-windows-amd64.exe",
    ("windows", "arm64"): "cloudflared-windows-amd64.exe",  # runs under Windows-on-ARM emulation
    ("darwin", "arm64"): "cloudflared-darwin-arm64.tgz", ("darwin", "x86_64"): "cloudflared-darwin-amd64.tgz",
    ("linux", "x86_64"): "cloudflared-linux-amd64", ("linux", "amd64"): "cloudflared-linux-amd64",
    ("linux", "aarch64"): "cloudflared-linux-arm64", ("linux", "arm64"): "cloudflared-linux-arm64",
}


class TunnelError(Exception):
    pass


def _download(url: str, dest: Path) -> None:
    try:
        with urllib.request.urlopen(url, timeout=120) as r, open(dest, "wb") as f:
            shutil.copyfileobj(r, f)
    except Exception:  # e.g. missing SSL certificates on a fresh macOS Python: fall back to the system curl
        try:
            subprocess.check_call(["curl", "-fsSL", "-o", str(dest), url])
        except Exception as e:
            raise TunnelError("Couldn't download Cloudflare's tunnel tool. Check the internet connection.") from e


def get_cloudflared(tools_dir: Path) -> str:
    found = shutil.which("cloudflared")
    if found:
        return found
    key = (platform.system().lower(), platform.machine().lower())
    asset = TUNNEL_ASSETS.get(key)
    if not asset:
        raise TunnelError(f"Sharing online isn't available for {key[0]} on {key[1]}.")
    tools_dir.mkdir(parents=True, exist_ok=True)
    exe = tools_dir / ("cloudflared.exe" if key[0] == "windows" else "cloudflared")
    if not exe.exists():
        url = f"https://github.com/cloudflare/cloudflared/releases/latest/download/{asset}"
        part = exe.with_suffix(exe.suffix + ".part")
        if asset.endswith(".tgz"):
            tgz = tools_dir / asset
            _download(url, tgz)
            with tarfile.open(tgz) as t:
                member = next(m for m in t.getmembers() if m.name.endswith("cloudflared"))
                member.name = part.name
                t.extract(member, tools_dir)
            tgz.unlink()
        else:
            _download(url, part)
        part.chmod(0o755)
        part.replace(exe)  # only a complete download ever becomes the tool
    return str(exe)


def start_tunnel(port: int, tools_dir: Path, timeout: float = 90) -> tuple[subprocess.Popen, str]:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # no console window flashing up on Windows
    proc = subprocess.Popen([get_cloudflared(tools_dir), "tunnel", "--no-autoupdate", "--url", f"http://localhost:{port}"],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
                            creationflags=flags)
    found: list[str] = []

    def reader():
        for line in proc.stdout:  # keep draining so the tunnel never blocks on a full pipe
            m = URL_RE.search(line)
            if m and not found:
                found.append(m.group(0))

    threading.Thread(target=reader, daemon=True).start()
    deadline = time.time() + timeout
    while not found and time.time() < deadline and proc.poll() is None:
        time.sleep(0.3)
    if not found:
        proc.terminate()
        raise TunnelError("Couldn't open the public link (no internet, or Cloudflare is busy). Try again in a minute.")
    return proc, found[0]


class ShareManager:
    """One shared link at a time, started in the background so the app stays responsive."""

    def __init__(self):
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self.state = "off"  # off | starting | on | error
        self.url = ""
        self.error = ""

    def status(self) -> dict:
        with self._lock:
            if self.state == "on" and self._proc is not None and self._proc.poll() is not None:
                self._stopped_locked("The public link stopped. Press Start to open a new one.")
            return {"state": self.state, "url": self.url, "error": self.error}

    def start(self, port: int, tools_dir: Path) -> dict:
        with self._lock:
            if self.state in ("starting", "on"):
                return {"state": self.state, "url": self.url, "error": ""}
            self.state, self.url, self.error = "starting", "", ""
        threading.Thread(target=self._run, args=(port, tools_dir), daemon=True).start()
        return self.status()

    def _run(self, port: int, tools_dir: Path) -> None:
        from . import config
        try:
            proc, url = start_tunnel(port, tools_dir)
        except Exception as e:  # TunnelError or OS errors: report, don't crash the app
            with self._lock:
                self.state, self.error = "error", str(e)
            return
        with self._lock:
            if self.state != "starting":  # stopped while starting
                proc.terminate()
                return
            self._proc, self.state, self.url = proc, "on", url
        config.set_hosted_url(url, temporary=True)

    def _stopped_locked(self, error: str = "") -> None:
        from . import config
        if self._proc is not None and self._proc.poll() is None:
            self._proc.terminate()
        self._proc, self.state, self.url, self.error = None, ("error" if error else "off"), "", error
        config.set_hosted_url("")

    def stop(self) -> dict:
        with self._lock:
            self._stopped_locked()
        return self.status()


share = ShareManager()
