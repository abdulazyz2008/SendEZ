"""Import a student list straight from a Google Sheets link (e.g. Google Form responses).

Only docs.google.com spreadsheet links are accepted, and the download URL is built here, so a pasted
link can never make the app fetch anything else. The sheet must be shared "Anyone with the link".
"""
import re
import ssl
import urllib.error
import urllib.request
from urllib.parse import unquote, urlparse

from . import config

ID_RE = re.compile(r"^/spreadsheets/d/([A-Za-z0-9_-]{20,})")
NOT_A_SHEET = "Paste a Google Sheets link. It starts with https://docs.google.com/spreadsheets/…"
PRIVATE = ("This sheet is private. In Google Sheets press Share → General access → “Anyone with the link” "
           "(Viewer), then try again.")


class SheetError(ValueError):
    pass


def sheet_id(url: str) -> str:
    u = urlparse((url or "").strip())
    if u.scheme not in ("https", "http") or u.hostname != "docs.google.com":
        raise SheetError(NOT_A_SHEET)
    m = ID_RE.match(u.path)
    if not m:
        raise SheetError(NOT_A_SHEET)
    return m.group(1)


def ssl_context() -> ssl.SSLContext:
    """The computer's own trusted certificates (works inside the packaged app too)."""
    try:
        import truststore
        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except Exception:
        pass
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def _fetch(url: str, limit: int) -> tuple[bytes, str]:
    req = urllib.request.Request(url, headers={"User-Agent": "SendEZ"})
    with urllib.request.urlopen(req, timeout=30, context=ssl_context()) as r:
        return r.read(limit + 1), r.headers.get("Content-Disposition", "")


def _filename(disposition: str) -> str:
    m = re.search(r"filename\*=UTF-8''([^;]+)", disposition) or re.search(r'filename="?([^";]+)"?', disposition)
    name = unquote(m.group(1)).strip() if m else ""
    name = re.sub(r'[\\/:*?"<>|]+', " ", name).strip()
    return name if name.lower().endswith(".xlsx") else (f"{name}.xlsx" if name else "Google Sheet.xlsx")


def download(url: str) -> tuple[bytes, str]:
    """Returns (xlsx bytes with every tab, file name)."""
    sid = sheet_id(url)
    limit = config.MAX_UPLOAD_MB * 1024 * 1024
    try:
        data, disposition = _fetch(f"https://docs.google.com/spreadsheets/d/{sid}/export?format=xlsx", limit)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise SheetError("That Google Sheet doesn't exist. Check the link.") from e
        if e.code in (401, 403):
            raise SheetError(PRIVATE) from e
        raise SheetError(f"Google Sheets answered with an error ({e.code}). Try again in a minute.") from e
    except (urllib.error.URLError, OSError) as e:
        raise SheetError("Couldn't reach Google Sheets. Check the internet connection and try again.") from e
    if len(data) > limit:
        raise SheetError(f"The sheet is larger than {config.MAX_UPLOAD_MB} MB")
    if not data.startswith(b"PK"):  # Google's sign-in page instead of a spreadsheet
        raise SheetError(PRIVATE)
    return data, _filename(disposition)
