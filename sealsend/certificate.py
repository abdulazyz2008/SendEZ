"""Certificates: render by template id, embed proof metadata, previews, imports and the forgery demo."""
import hashlib
import json
import secrets
import shutil
import subprocess
import tempfile
from io import BytesIO
from pathlib import Path

from reportlab.lib.colors import HexColor
from reportlab.pdfgen import canvas

from . import config, db
from . import templates_lib as T

DEFAULT_TEMPLATE_ID = "university-classic"


def proof_keywords(cert_code: str, lock: str) -> str:
    return f"sealsend:v1:{cert_code}:{lock}"


def resolve(template_id: str) -> dict:
    return T.get_template(template_id or DEFAULT_TEMPLATE_ID) or T.BUILTIN_BY_ID[DEFAULT_TEMPLATE_ID]


def render_pdf(template_id: str, values: dict, cert_code: str = "", lock: str = "", assets: dict | None = None,
               qr_on: bool = True, title: str = "Certificate") -> bytes:
    """Byte-for-byte deterministic for the same inputs (built-in and image templates)."""
    tpl = resolve(template_id)
    meta = {"title": title, "author": values.get("ORG_NAME") or config.ISSUER_NAME, "subject": cert_code,
            "keywords": proof_keywords(cert_code, lock)}
    return T.render(tpl, values, assets or {}, meta, qr_on)


def render_job(job: tuple) -> bytes:
    """Process-pool entry point (must be a top-level function for Windows/macOS spawn)."""
    template_id, values, code, lock, assets, qr_on, title = job
    return render_pdf(template_id, values, code, lock, assets, qr_on, title)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_proof(data: bytes) -> dict:
    from pypdf import PdfReader

    try:
        meta = PdfReader(BytesIO(data)).metadata or {}
    except Exception:
        return {"code": "", "lock": ""}
    kw = str(meta.get("/Keywords", "") or "")
    parts = kw.split(":")
    if len(parts) == 4 and parts[0] == "sealsend":
        return {"code": parts[2], "lock": parts[3]}
    return {"code": str(meta.get("/Subject", "") or ""), "lock": ""}


def forge_copy(data: bytes, new_name: str) -> bytes:
    """Demo helper: what a forger does. Paints a new name over the middle of the page and keeps
    the original metadata, so the file still *claims* to be the genuine certificate."""
    from pypdf import PdfReader, PdfWriter

    reader = PdfReader(BytesIO(data))
    page = reader.pages[0]
    w, h = float(page.mediabox.width), float(page.mediabox.height)
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=(w, h))
    c.setFillColor(HexColor("#ffffff"))
    c.rect(w * 0.12, h * 0.44, w * 0.76, h * 0.11, stroke=0, fill=1)
    T.draw_text(c, new_name, w * 0.5, h * 0.47, "display", 38, HexColor("#1f3a5f"), max_w=w * 0.7)
    c.save()
    page.merge_page(PdfReader(BytesIO(buf.getvalue())).pages[0])
    writer = PdfWriter()
    writer.add_page(page)
    writer.add_metadata({k: v for k, v in (reader.metadata or {}).items()})
    out = BytesIO()
    writer.write(out)
    return out.getvalue()


def pdf_to_png(data: bytes, dpi: int = 110) -> bytes | None:
    """Rasterize for previews. PyMuPDF works on Windows, macOS and Linux; poppler is a fallback."""
    try:
        import pymupdf
        doc = pymupdf.open(stream=data, filetype="pdf")
        return doc[0].get_pixmap(dpi=dpi, alpha=False).tobytes("png")
    except Exception:
        pass
    exe = shutil.which("pdftoppm")
    if not exe:
        return None
    try:
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run([exe, "-png", "-r", str(dpi), "-singlefile", "-", str(Path(tmp) / "page")],
                           input=data, capture_output=True, timeout=30, check=True)
            out = Path(tmp) / "page.png"
            return out.read_bytes() if out.exists() else None
    except Exception:
        return None


# ---------------------------------------------------------------- gallery
def sample_values(tpl: dict, org: dict | None = None) -> dict:
    v = dict(T.SAMPLE_VALUES)
    v["TITLE"] = tpl.get("title", "")
    if org:
        for k, key in (("ORG_NAME", "org_name"), ("SIGNER1_NAME", "signer1_name"), ("SIGNER1_TITLE", "signer1_title"),
                       ("SIGNER2_NAME", "signer2_name"), ("SIGNER2_TITLE", "signer2_title")):
            if org.get(key):
                v[k] = org[key]
    return v


def thumbnail(template_id: str, org: dict | None = None, dpi: int = 48) -> bytes:
    """Cached PNG of a template with sample data (cache key covers template + branding)."""
    tpl = resolve(template_id)
    org = org or {}
    key = hashlib.sha1(json.dumps([template_id, tpl.get("fields"), org, dpi, tpl.get("background", "")],
                                  sort_keys=True, default=str).encode()).hexdigest()[:16]
    path = config.THUMB_DIR / f"{tpl['id']}_{key}.png"
    if path.exists():
        return path.read_bytes()
    assets = {k: org.get(k, "") for k in ("logo", "signer1_sig", "signer2_sig")}
    png = pdf_to_png(T.render(tpl, sample_values(tpl, org), assets, {"subject": "sample"}), dpi=dpi) or b""
    if png:
        path.write_bytes(png)
    return png


def catalog() -> list[dict]:
    out = [{"id": t["id"], "name": t["name"], "niche": t["niche"], "description": t["description"],
            "title": t["title"], "custom": False} for t in T.BUILTINS]
    for r in db.query("SELECT id, name, description, default_title, created_at FROM templates ORDER BY created_at DESC"):
        out.append({"id": r["id"], "name": r["name"], "niche": "My templates", "description": r["description"],
                    "title": r["default_title"], "custom": True, "created_at": r["created_at"]})
    return out


# ---------------------------------------------------------------- imports (Canva exports and other artwork)
def import_template(data: bytes, filename: str, name: str) -> str:
    """Store a PNG/JPG/PDF exported from Canva (or anywhere). Images are normalized to a compact
    high-quality JPEG so every certificate email stays small."""
    suffix = Path(filename).suffix.lower()
    tid = "custom-" + secrets.token_hex(5)
    if suffix == ".pdf":
        from pypdf import PdfReader
        try:
            reader = PdfReader(BytesIO(data))
            page = reader.pages[0]
        except Exception as e:
            raise ValueError(f"That PDF could not be read ({e})") from e
        if reader.is_encrypted:
            raise ValueError("That PDF is password-protected. Export it again without a password")
        w, h = float(page.mediabox.width), float(page.mediabox.height)
        stored = f"{tid}.pdf"
        (config.UPLOAD_DIR / stored).write_bytes(data)
        kind = "pdf"
    elif suffix in (".png", ".jpg", ".jpeg", ".webp"):
        from PIL import Image
        try:
            im = Image.open(BytesIO(data))
            im.load()
        except Exception as e:
            raise ValueError("That image could not be read") from e
        if im.mode in ("RGBA", "LA", "P"):
            bg = Image.new("RGB", im.size, "white")
            im = im.convert("RGBA")
            bg.paste(im, mask=im.split()[-1])
            im = bg
        im = im.convert("RGB")
        if max(im.size) > 3508:  # A4 at 300 dpi is plenty for print
            im.thumbnail((3508, 3508))
        stored = f"{tid}.jpg"
        im.save(config.UPLOAD_DIR / stored, "JPEG", quality=90, optimize=True)
        iw, ih = im.size
        w = 842.0 if iw >= ih else 595.0
        h = w * ih / iw
        kind = "image"
    else:
        raise ValueError("Upload a PNG, JPG or PDF exported from Canva")
    with db.tx() as c:
        c.execute("INSERT INTO templates (id, name, description, kind, file, page_w, page_h, fields_json, default_title, created_at) "
                  "VALUES (?,?,?,?,?,?,?,?,?,?)",
                  (tid, (name or Path(filename).stem)[:80], "Imported design", kind, stored, w, h,
                   json.dumps(T.default_imported_fields()), "Certificate of Achievement", db.now()))
    return tid


def update_template_fields(template_id: str, fields: list[dict], name: str | None = None) -> None:
    clean = []
    for f in fields[:30]:
        kind = f.get("type", "text")
        if kind not in ("text", "qr", "logo", "signature"):
            continue
        g = {"type": kind, "key": str(f.get("key", ""))[:30], "enabled": bool(f.get("enabled", True)),
             "x": min(max(float(f.get("x", 0.5)), 0), 1), "y": min(max(float(f.get("y", 0.5)), 0), 1)}
        if kind == "text":
            g.update({"text": str(f.get("text", ""))[:300], "size": min(max(float(f.get("size", 14)), 5), 120),
                      "font": f.get("font", "sans") if f.get("font") in T.FONTS else "sans",
                      "color": f.get("color", "#1a1a1a") if str(f.get("color", "")).startswith("#") else "#1a1a1a",
                      "align": f.get("align", "center") if f.get("align") in ("left", "center", "right") else "center",
                      "max_width": min(max(float(f.get("max_width", 0.8)), 0.1), 1),
                      "lines": int(min(max(int(f.get("lines", 1)), 1), 4)), "upper": bool(f.get("upper", False)),
                      "tracking": min(max(float(f.get("tracking", 0)), 0), 10)})
        elif kind == "qr":
            g["size"] = min(max(float(f.get("size", 0.1)), 0.05), 0.3)
        elif kind == "logo":
            g["h"] = min(max(float(f.get("h", 0.08)), 0.03), 0.3)
            g["align"] = "center"
        elif kind == "signature":
            g["n"] = 1 if int(f.get("n", 1)) == 1 else 2
        clean.append(g)
    with db.tx() as c:
        if name:
            c.execute("UPDATE templates SET name=? WHERE id=?", (name[:80], template_id))
        c.execute("UPDATE templates SET fields_json=? WHERE id=?", (json.dumps(clean), template_id))


def delete_template(template_id: str) -> None:
    row = db.one("SELECT file FROM templates WHERE id=?", (template_id,))
    if row:
        (config.UPLOAD_DIR / row["file"]).unlink(missing_ok=True)
        with db.tx() as c:
            c.execute("DELETE FROM templates WHERE id=?", (template_id,))
