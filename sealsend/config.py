"""Runtime configuration, read from environment variables (never hard-coded secrets)."""
import os
import sys
import threading
from pathlib import Path


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader so organizers can keep credentials out of the code."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


FROZEN = bool(getattr(sys, "frozen", False))  # running as the packaged desktop app (PyInstaller)
ROOT = Path(__file__).resolve().parent.parent
# Read-only files shipped with the app (static/, fonts/, sample/). Inside a packaged app they live in the bundle.
RESOURCE_ROOT = Path(getattr(sys, "_MEIPASS", ROOT))
PACKAGE_DIR = RESOURCE_ROOT / "sealsend" if FROZEN else Path(__file__).resolve().parent
SAMPLE_DIR = RESOURCE_ROOT / "sample"


def user_data_dir() -> Path:
    """Where a packaged app keeps its data (never inside the app itself, which may be read-only)."""
    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "SendEZ"
    if os.name == "nt":
        return Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming") / "SendEZ"
    return Path(os.environ.get("XDG_DATA_HOME") or home / ".local" / "share") / "SendEZ"


if not FROZEN:
    _load_dotenv(ROOT / ".env")

DATA_DIR = Path(os.environ.get("SEALSEND_DATA_DIR") or (user_data_dir() if FROZEN else ROOT / "data")).resolve()
DB_PATH = DATA_DIR / "sealsend.db"
UPLOAD_DIR = DATA_DIR / "uploads"
CERT_DIR = DATA_DIR / "certificates"
OUTBOX_DIR = DATA_DIR / "outbox"
KEY_DIR = DATA_DIR / "keys"
SIGNING_KEY_PATH = Path(os.environ.get("SEALSEND_SIGNING_KEY", KEY_DIR / "ed25519_private.pem"))

ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")

SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
SMTP_SECURITY = os.environ.get("SMTP_SECURITY", "starttls").lower()  # starttls | ssl | none
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
MAIL_FROM = os.environ.get("MAIL_FROM", SMTP_USER or "certificates@example.org")
MAIL_FROM_NAME = os.environ.get("MAIL_FROM_NAME", "SendEZ Certificates")
ISSUER_NAME = os.environ.get("ISSUER_NAME", "Event Organizing Committee")

SEND_DELAY_SECONDS = float(os.environ.get("SEND_DELAY_SECONDS", "0"))
MAX_SEND_ATTEMPTS = int(os.environ.get("MAX_SEND_ATTEMPTS", "3"))
RETRY_BASE_SECONDS = float(os.environ.get("RETRY_BASE_SECONDS", "1"))
SCHEDULER_SECONDS = float(os.environ.get("SEALSEND_SCHEDULER_SECONDS", "20"))  # how often scheduled sends are checked
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "20"))
PORT = int(os.environ.get("SEALSEND_PORT", "8000"))
SETTINGS_PATH = DATA_DIR / "settings.json"
THUMB_DIR = DATA_DIR / "thumbs"


def lan_ip() -> str:
    """This computer's address on the local network (no packets are sent)."""
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


_PUBLIC_ENV = os.environ.get("PUBLIC_BASE_URL", "")
LOCAL_ONLY = os.environ.get("SEALSEND_LOCAL_ONLY", "") == "1"  # desktop app listens on this computer only
DESKTOP = os.environ.get("SEALSEND_DESKTOP", "") == "1"  # running inside the desktop app window
WORKERS = int(os.environ.get("SEALSEND_WORKERS", "0")) or min(os.cpu_count() or 1, 8)

# The public address can come from the host (Render), from `run.py --online`, or from the in-app
# "Share online" button at runtime, so it is kept in a small mutable holder rather than a constant.
_hosted_lock = threading.Lock()
_hosted = {
    "url": os.environ.get("RENDER_EXTERNAL_URL", "") or os.environ.get("SEALSEND_HOSTED_URL", ""),
    "temporary": os.environ.get("SEALSEND_TEMPORARY_URL", "") == "1",  # free tunnel link: changes on every start
}


def hosted_url() -> str:
    with _hosted_lock:
        return _hosted["url"]


def is_temporary_url() -> bool:
    with _hosted_lock:
        return bool(_hosted["url"]) and _hosted["temporary"]


def set_hosted_url(url: str, temporary: bool = False) -> None:
    with _hosted_lock:
        _hosted["url"] = (url or "").rstrip("/")
        _hosted["temporary"] = bool(url) and temporary


def public_base_url() -> str:
    """Where QR codes and verify links point. Settings > env > website address > this laptop's LAN address."""
    from . import settings
    return (settings.get("public_base_url") or _PUBLIC_ENV or hosted_url() or f"http://{lan_ip()}:{PORT}").rstrip("/")


def app_url() -> str:
    """The address people open the app at: the website when hosted, else this laptop on the Wi-Fi."""
    return (hosted_url() or f"http://{lan_ip()}:{PORT}").rstrip("/")


PUBLIC_BASE_URL = _PUBLIC_ENV or "http://localhost:8000"  # legacy name; use public_base_url()

# AI rules copilot: "auto" uses Claude when ANTHROPIC_API_KEY is set, else the offline parser.
COPILOT_ENGINE = os.environ.get("COPILOT_ENGINE", "auto").lower()  # auto | claude | local
COPILOT_MODEL = os.environ.get("COPILOT_MODEL", "claude-opus-5-5")


def mail_mode() -> str:
    """sandbox | smtp | resend. The in-app Email settings win over environment variables."""
    from . import settings
    provider = settings.mail().get("provider", "")
    if provider in ("gmail", "outlook", "smtp") and settings.mail().get("address"):
        return "smtp"
    if provider == "resend" and settings.mail().get("password"):
        return "resend"
    if provider == "sandbox":
        return "sandbox"
    if SMTP_HOST:
        return "smtp"
    if RESEND_API_KEY:
        return "resend"
    return "sandbox"


for d in (DATA_DIR, UPLOAD_DIR, CERT_DIR, OUTBOX_DIR, KEY_DIR, DATA_DIR / "thumbs"):
    d.mkdir(parents=True, exist_ok=True)
