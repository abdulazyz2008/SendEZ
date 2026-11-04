"""SendEZ HTTP API, organizer UI and the public verify / claim pages."""
import csv
import hmac
import html
import io
import json
import secrets
import time
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlencode

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import assistant, certificate, chaos, clubs, config, copilot, crypto, db, gsheets, mailer, pipeline, reports, settings, sharecard, tunnel
from . import templates_lib as T
from .data import STYLES, suggest_categories

STATIC = config.PACKAGE_DIR / "static"


@asynccontextmanager
async def lifespan(_app):
    with db.tx() as c:  # a crash mid-send leaves records in "Sending": requeue them
        c.execute("UPDATE records SET status='Generated' WHERE status='Sending'")
        c.execute("UPDATE batches SET job=''")
    crypto.private_key()
    pipeline.start_scheduler()  # scheduled sends and automatic continue after the daily limit
    yield
    pipeline.stop_scheduler()
    tunnel.share.stop()  # never leave a public link open after the app quits


app = FastAPI(title="SendEZ", docs_url=None, redoc_url=None, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    return resp


# ---------------------------------------------------------------- auth
SESSIONS: dict[str, float] = {}
SESSION_TTL = 12 * 3600
_failed_logins: list[float] = []


def require_auth(request: Request) -> None:
    token = request.cookies.get("sealsend_session", "")
    exp = SESSIONS.get(token)
    if not exp or exp < time.time():
        SESSIONS.pop(token, None)
        raise HTTPException(401, "Please sign in")


def _start_session(request: Request, response: Response) -> None:
    token = secrets.token_urlsafe(32)
    SESSIONS[token] = time.time() + SESSION_TTL
    response.set_cookie("sealsend_session", token, httponly=True, samesite="lax", max_age=SESSION_TTL,
                        secure=request.url.scheme == "https")


def _is_local(request: Request) -> bool:
    """Only someone sitting at this computer. Requests relayed by a tunnel or proxy are not local."""
    if any(h in request.headers for h in ("cf-connecting-ip", "cf-ray", "x-forwarded-for", "forwarded", "x-real-ip")):
        return False
    host = request.client.host if request.client else ""
    return host in ("127.0.0.1", "::1", "localhost", "testclient")


class Login(BaseModel):
    username: str = "admin"
    password: str


@app.get("/api/setup-status")
def setup_status(request: Request):
    return {"needs_setup": not settings.admin_configured(), "local": _is_local(request), "hosted": bool(config.hosted_url())}


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.post("/api/setup")
def first_run_setup(body: Login, request: Request, response: Response):
    if settings.admin_configured():
        raise HTTPException(400, "Already set up. Sign in instead.")
    if not _is_local(request):
        raise HTTPException(403, "For safety, create the password on the computer running SendEZ")
    if len(body.password) < 8:
        raise HTTPException(400, "Use at least 8 characters")
    settings.set_admin_password(body.password)
    _start_session(request, response)
    return {"ok": True}


@app.post("/api/login")
def login(body: Login, request: Request, response: Response):
    now = time.time()
    _failed_logins[:] = [t for t in _failed_logins if now - t < 300]
    if len(_failed_logins) >= 10:
        raise HTTPException(429, "Too many failed attempts. Try again in a few minutes.")
    if not settings.admin_configured():
        raise HTTPException(400, "Create a password first")
    if not settings.check_admin_password(body.password):
        _failed_logins.append(now)
        raise HTTPException(401, "Wrong password")
    _start_session(request, response)
    return {"ok": True}


@app.post("/api/logout")
def logout(request: Request, response: Response):
    SESSIONS.pop(request.cookies.get("sealsend_session", ""), None)
    response.delete_cookie("sealsend_session")
    return {"ok": True}


class PasswordIn(BaseModel):
    current: str
    new: str


@app.put("/api/settings/password", dependencies=[Depends(require_auth)])
def change_password(body: PasswordIn):
    if config.ADMIN_PASSWORD:
        raise HTTPException(400, "The password is set by ADMIN_PASSWORD in .env; change it there")
    if not settings.check_admin_password(body.current):
        raise HTTPException(400, "Current password is wrong")
    if len(body.new) < 8:
        raise HTTPException(400, "Use at least 8 characters")
    settings.set_admin_password(body.new)
    return {"ok": True}


def _copilot_label() -> str:
    import os
    ai = config.COPILOT_ENGINE == "claude" or (config.COPILOT_ENGINE == "auto" and bool(os.environ.get("ANTHROPIC_API_KEY")))
    return f"Claude · {config.COPILOT_MODEL}" if ai else "Offline"


@app.get("/api/me", dependencies=[Depends(require_auth)])
def me():
    m2 = settings.mail2()
    return {"mail_mode": config.mail_mode(), "mail": settings.public_mail(), "quota": pipeline.quota_status(),
            "mail2": {"address": m2["address"], "provider": m2["provider"], "verified_at": m2.get("verified_at", "")} if m2 else None,
            "key_fingerprint": crypto.fingerprint(), "copilot": _copilot_label(),
            "public_base_url": config.public_base_url(), "lan_url": config.app_url(), "hosted": bool(config.hosted_url()),
            "temporary_url": config.is_temporary_url(), "lan_available": not config.LOCAL_ONLY, "desktop": config.DESKTOP, "org": settings.org(), "providers": {k: v["label"] for k, v in settings.PROVIDERS.items()},
            "presets": {k: v["label"] for k, v in mailer.PRESETS.items()},
            "styles": {k: v["title"] for k, v in STYLES.items()},
            "fonts": T.FONT_LABELS, "niches": T.NICHES + ["My templates"]}


# ---------------------------------------------------------------- helpers
def get_batch_or_404(batch_id: int) -> dict:
    b = db.get_batch(batch_id)
    if not b:
        raise HTTPException(404, "Batch not found")
    b["config"] = pipeline.normalize_config(b["config"])
    return b


def get_record_or_404(record_id: int) -> dict:
    r = db.get_record(record_id)
    if not r:
        raise HTTPException(404, "Record not found")
    return r


def ensure_idle(batch_id: int) -> None:
    if pipeline.is_running(batch_id):
        raise HTTPException(409, "Please wait: this batch is busy generating or sending")


def batch_summary(b: dict) -> dict:
    return {"id": b["id"], "name": b["name"], "filename": b["filename"], "created_at": b["created_at"],
            "job": b["job"], "counts": db.status_counts(b["id"]), "is_chaos": bool(b["is_chaos"]),
            "sealed": bool(b["seal"]), "chaos_phase": (b["chaos"] or {}).get("phase", ""),
            "pause_reason": b.get("pause_reason", ""), "sheet": b.get("sheet", ""),
            "send_at": b.get("send_at", ""), "send_kind": b.get("send_kind", "")}


async def _read_upload(upload: UploadFile, allowed: tuple[str, ...]) -> bytes:
    suffix = Path(upload.filename or "").suffix.lower()
    if suffix not in allowed:
        raise HTTPException(400, f"That file type ({suffix or 'none'}) isn't supported here. Use {', '.join(allowed)}")
    data = await upload.read(config.MAX_UPLOAD_MB * 1024 * 1024 + 1)
    if len(data) > config.MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(413, f"File is larger than {config.MAX_UPLOAD_MB} MB")
    if not data:
        raise HTTPException(400, "The file is empty")
    return data


def claim_url(rec: dict) -> str:
    return f"{config.public_base_url()}/claim/{rec['claim_token']}" if rec["claim_token"] else ""


def verify_url(rec: dict) -> str:
    return f"{config.public_base_url()}/verify/{rec['cert_code']}"


# ---------------------------------------------------------------- batches
@app.post("/api/batches", dependencies=[Depends(require_auth)])
async def create_batch(file: UploadFile = File(...), name: str = Form(""), club: str = Form(""), reuse: int = Form(0)):
    if club and club not in clubs.CLUB_BY_ID:
        raise HTTPException(400, "Unknown club")
    if reuse and not db.get_batch(reuse):
        raise HTTPException(400, "The event to copy settings from no longer exists")
    data = await _read_upload(file, (".xlsx", ".xlsm", ".csv", ".tsv", ".txt", ".xls"))
    suffix = Path(file.filename or "x.csv").suffix.lower()
    path = config.UPLOAD_DIR / f"{secrets.token_hex(8)}{suffix}"
    path.write_bytes(data)
    try:
        batch_id = pipeline.create_batch(path, file.filename or path.name, name.strip(), club=club, reuse_from=reuse or None)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(400, f"Could not read the spreadsheet: {e}")
    return batch_detail(batch_id)


class SheetLinkIn(BaseModel):
    url: str
    name: str = ""
    club: str = ""
    reuse: int = 0


@app.post("/api/batches/from-url", dependencies=[Depends(require_auth)])
def create_batch_from_sheet_link(body: SheetLinkIn):
    if body.club and body.club not in clubs.CLUB_BY_ID:
        raise HTTPException(400, "Unknown club")
    if body.reuse and not db.get_batch(body.reuse):
        raise HTTPException(400, "The event to copy settings from no longer exists")
    try:
        data, filename = gsheets.download(body.url)
    except gsheets.SheetError as e:
        raise HTTPException(400, str(e))
    path = config.UPLOAD_DIR / f"{secrets.token_hex(8)}.xlsx"
    path.write_bytes(data)
    try:
        batch_id = pipeline.create_batch(path, filename, body.name.strip(), club=body.club, reuse_from=body.reuse or None,
                                         cfg_overrides={"source_url": body.url.strip()[:500]})
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(400, f"Could not read the spreadsheet: {e}")
    return batch_detail(batch_id)


@app.get("/api/batches", dependencies=[Depends(require_auth)])
def list_batches():
    return [batch_summary(db.get_batch(r["id"])) for r in db.query("SELECT id FROM batches ORDER BY id DESC")]


@app.get("/api/batches/{batch_id}", dependencies=[Depends(require_auth)])
def batch_detail(batch_id: int):
    b = get_batch_or_404(batch_id)
    sample = db.query("SELECT data_json FROM records WHERE batch_id=? ORDER BY row_no LIMIT 3", (batch_id,))
    return {**batch_summary(b), "columns": b["columns"], "config": b["config"], "seal": b["seal"], "chaos": b["chaos"],
            "placeholders": pipeline.all_placeholders(b["config"]),
            "sample_rows": [json.loads(r["data_json"]) for r in sample]}


@app.delete("/api/batches/{batch_id}", dependencies=[Depends(require_auth)])
def delete_batch(batch_id: int):
    get_batch_or_404(batch_id)
    ensure_idle(batch_id)
    pipeline.delete_batch(batch_id)
    return {"ok": True}


class SheetIn(BaseModel):
    sheet: str


@app.post("/api/batches/{batch_id}/sheet", dependencies=[Depends(require_auth)])
def switch_sheet(batch_id: int, body: SheetIn):
    get_batch_or_404(batch_id)
    ensure_idle(batch_id)
    try:
        pipeline.reload_sheet(batch_id, body.sheet)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return batch_detail(batch_id)


class ConfigIn(BaseModel):
    mapping: dict
    email_column: str = ""
    fallback_column: str = ""
    category_column: str = ""
    template_id: str = certificate.DEFAULT_TEMPLATE_ID
    qr: bool = True
    email_subject: str
    email_body: str
    filename_pattern: str = "{{ID}}_{{NAME}}_{{EVENT}}"
    rules: list = []
    auto_fix_emails: bool = True
    claim_links: bool = True
    fix_caps: bool = False
    branding: dict = {}
    feedback: bool = False


@app.put("/api/batches/{batch_id}/config", dependencies=[Depends(require_auth)])
def save_config(batch_id: int, body: ConfigIn):
    b = get_batch_or_404(batch_id)
    ensure_idle(batch_id)
    cols = b["columns"]
    for col in (body.email_column, body.fallback_column, body.category_column):
        if col and col not in cols:
            raise HTTPException(400, f"Unknown column: {col}")
    if body.email_column and body.email_column == body.fallback_column:
        raise HTTPException(400, "The backup email column must be different from the main one")
    if not T.get_template(body.template_id):
        raise HTTPException(400, "Unknown template")
    mapping = {}
    for ph, src in body.mapping.items():
        if not isinstance(src, dict):
            continue
        kind = src.get("type")
        if kind == "column" and src.get("value") in cols:
            mapping[ph.upper()] = {"type": "column", "value": src["value"]}
        elif kind == "text" and str(src.get("value", "")).strip():
            mapping[ph.upper()] = {"type": "text", "value": str(src["value"]).strip()[:300]}
        elif kind == "combine" and isinstance(src.get("value"), list) and src["value"] and all(c in cols for c in src["value"]):
            mapping[ph.upper()] = {"type": "combine", "value": src["value"][:4]}
    rules, notes = copilot.sanitize(body.rules, cols)
    branding = {k: str(body.branding.get(k, ""))[:200] for k in settings.DEFAULT["org"]}
    cfg = {**b["config"], **body.model_dump(), "mapping": mapping, "rules": rules, "branding": branding}
    db.save_config(batch_id, cfg)
    counts = pipeline.revalidate(batch_id)
    pipeline.log_validation(batch_id, counts)
    return {"counts": counts, "placeholders": pipeline.all_placeholders(cfg), "notes": notes, "rules": rules}


class CategoryIn(BaseModel):
    column: str


def _rows(batch_id: int) -> list[dict]:
    return [json.loads(r["data_json"]) for r in db.query("SELECT data_json FROM records WHERE batch_id=? ORDER BY row_no", (batch_id,))]


@app.post("/api/batches/{batch_id}/categories", dependencies=[Depends(require_auth)])
def suggest_category_rules(batch_id: int, body: CategoryIn):
    b = get_batch_or_404(batch_id)
    if body.column and body.column not in b["columns"]:
        raise HTTPException(400, "Unknown column")
    rows = _rows(batch_id)
    rules = suggest_categories(body.column, rows, b["config"]["template_id"]) if body.column else []
    return {"rules": rules, "counts": copilot.count_matches(rules, rows), "total": len(rows)}


@app.get("/api/batches/{batch_id}/category-counts", dependencies=[Depends(require_auth)])
def category_counts(batch_id: int):
    b = get_batch_or_404(batch_id)
    rows = _rows(batch_id)
    counts = copilot.count_matches(b["config"]["rules"], rows)
    return {"counts": counts, "unmatched": len(rows) - sum(counts), "total": len(rows)}


class CopilotIn(BaseModel):
    text: str


@app.post("/api/batches/{batch_id}/copilot", dependencies=[Depends(require_auth)])
def run_copilot(batch_id: int, body: CopilotIn):
    b = get_batch_or_404(batch_id)
    return copilot.suggest(body.text, b["columns"], _rows(batch_id), b["config"]["mapping"])


RECORD_FIELDS = ("id", "row_no", "name", "student_id", "email", "email_source", "email_fix", "delivery", "theme",
                 "rule_label", "category", "title", "status", "errors", "warnings", "cert_code", "cert_file", "attempts",
                 "last_error", "sent_at", "claimed_at")


@app.get("/api/batches/{batch_id}/records", dependencies=[Depends(require_auth)])
def list_records(batch_id: int, status: str = "", q: str = "", category: str = "", limit: int = 200, offset: int = 0):
    get_batch_or_404(batch_id)
    sql, params = "SELECT * FROM records WHERE batch_id=?", [batch_id]
    if status == "Issues":
        sql += " AND (status IN ('Invalid','Failed') OR warnings!='')"
    elif status == "Ready":
        sql += " AND status IN ('Pending','Generated')"
    elif status == "Claims":
        sql += " AND status IN ('Claim','Claimed')"
    elif status:
        sql += " AND status=?"
        params.append(status)
    if category:
        sql += " AND rule_label=?"
        params.append(category)
    if q:
        sql += " AND (name LIKE ? OR email LIKE ? OR student_id LIKE ? OR cert_code LIKE ?)"
        params += [f"%{q}%"] * 4
    total = db.one(sql.replace("SELECT *", "SELECT COUNT(*) n", 1), params)["n"]
    rows = db.query(sql + " ORDER BY row_no LIMIT ? OFFSET ?", params + [min(max(limit, 1), 1000), max(offset, 0)])
    return {"total": total, "records": [{k: r[k] for k in RECORD_FIELDS} for r in rows]}


@app.get("/api/records/{record_id}", dependencies=[Depends(require_auth)])
def record_detail(record_id: int):
    rec = get_record_or_404(record_id)
    cfg = pipeline.normalize_config(db.get_batch(rec["batch_id"])["config"])
    out = {k: rec[k] for k in RECORD_FIELDS}
    out.update({
        "batch_id": rec["batch_id"], "source_row": db.record_data(rec), "values": pipeline.record_values(rec, cfg),
        "mapping_lock": rec["mapping_lock"], "pdf_sha256": rec["pdf_sha256"], "signature": rec["signature"],
        "leaf_hash": rec["leaf_hash"], "verify_url": verify_url(rec) if rec["payload_json"] else "",
        "claim_url": claim_url(rec) if rec["cert_file"] else "",
        "template_name": certificate.resolve(rec["theme"] or cfg["template_id"])["name"],
        "events": db.query("SELECT at, level, kind, message FROM events WHERE record_id=? ORDER BY id", (record_id,)),
        "proof": None,
    })
    if rec["payload_json"]:
        p = pipeline.record_proof(rec)
        out["proof"] = {k: p.get(k) for k in ("signature_valid", "record_consistent", "sealed", "in_seal",
                                              "seal_signature_valid", "valid", "leaf_index", "leaf_count", "proof")}
        if rec["cert_file"] and (config.CERT_DIR / rec["cert_file"]).exists():
            out["proof"]["lock_check"] = pipeline.check_attachment(rec, (config.CERT_DIR / rec["cert_file"]).read_bytes(), cfg)
    return out


def _preview_record(batch_id: int, record_id: int | None) -> dict:
    if record_id:
        rec = get_record_or_404(record_id)
        if rec["batch_id"] != batch_id:
            raise HTTPException(404, "Record not in this batch")
        return rec
    rec = db.one("SELECT * FROM records WHERE batch_id=? AND status!='Invalid' ORDER BY row_no LIMIT 1", (batch_id,))
    rec = rec or db.one("SELECT * FROM records WHERE batch_id=? ORDER BY row_no LIMIT 1", (batch_id,))
    if not rec:
        raise HTTPException(404, "No records")
    return rec


@app.get("/api/batches/{batch_id}/preview/certificate", dependencies=[Depends(require_auth)])
def preview_certificate(batch_id: int, record_id: int | None = None, format: str = "png"):
    cfg = get_batch_or_404(batch_id)["config"]
    rec = _preview_record(batch_id, record_id)
    values = pipeline.record_values(rec, cfg)
    pdf = certificate.render_pdf(rec["theme"] or cfg["template_id"], values, rec["cert_code"],
                                 pipeline.expected_lock(rec, cfg, values), pipeline.assets_for(cfg), cfg.get("qr", True))
    if format == "png":
        png = certificate.pdf_to_png(pdf, 110)
        if png:
            return Response(png, media_type="image/png", headers={"Cache-Control": "no-store"})
    return Response(pdf, media_type="application/pdf", headers={"Content-Disposition": "inline; filename=preview.pdf"})


@app.get("/api/batches/{batch_id}/preview/email", dependencies=[Depends(require_auth)])
def preview_email(batch_id: int, record_id: int | None = None):
    cfg = get_batch_or_404(batch_id)["config"]
    rec = _preview_record(batch_id, record_id)
    values = pipeline.record_values(rec, cfg)
    subject, body = pipeline.email_texts(rec, cfg, values)
    if cfg.get("feedback"):
        body = body.rstrip() + f"\n\n{pipeline.feedback_question(values)} ★ ★ ★ ★ ★"
    from_name, from_addr, _ = mailer.sender_identity(cfg.get("branding", {}).get("org_name", ""))
    to = f"{rec['name']} <{rec['email']}>" if rec["email"] else (
        "Claim link (no usable email)" if rec["delivery"] == "claim" else "(no valid email)")
    tpl = certificate.resolve(rec["theme"] or cfg["template_id"])
    return {"record_id": rec["id"], "row_no": rec["row_no"], "status": rec["status"], "errors": rec["errors"],
            "warnings": rec["warnings"], "template": tpl["name"], "rule_label": rec["rule_label"],
            "title": values.get("TITLE", ""), "from": f"{from_name} <{from_addr}>", "to": to, "subject": subject,
            "body": body, "attachment": pipeline.cert_filename(rec, cfg, values)}


class SendIn(BaseModel):
    confirm: bool = False
    allow_resend: bool = False


def _preflight(batch: dict, check_quota: bool = True) -> None:
    problem, message = pipeline.preflight(batch, check_quota)
    if problem:
        raise HTTPException(400, message)


class ScheduleIn(BaseModel):
    at: str  # ISO 8601 with a time zone, e.g. from the browser's Date.toISOString()
    confirm: bool = False


@app.post("/api/batches/{batch_id}/schedule", dependencies=[Depends(require_auth)])
def schedule_send(batch_id: int, body: ScheduleIn):
    from datetime import datetime, timedelta, timezone
    b = get_batch_or_404(batch_id)
    if not body.confirm:
        raise HTTPException(400, "Scheduling requires explicit confirmation")
    if b["is_chaos"]:
        raise HTTPException(400, "The Chaos Test can't be scheduled")
    ensure_idle(batch_id)
    try:
        when = datetime.fromisoformat(body.at.strip().replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(400, "Pick a date and time")
    if when.tzinfo is None:
        raise HTTPException(400, "The time needs a time zone")
    now = datetime.now(timezone.utc)
    if when < now + timedelta(minutes=1):
        raise HTTPException(400, "Pick a time in the future")
    if when > now + timedelta(days=60):
        raise HTTPException(400, "Pick a time within the next 60 days")
    waiting = db.one("SELECT COUNT(*) n FROM records WHERE batch_id=? AND status IN ('Pending','Generated','Failed')", (batch_id,))["n"]
    if not waiting:
        raise HTTPException(400, "Everyone in this event already has their certificate")
    _preflight(b, check_quota=False)  # a wrong password shows up now, not at 9 AM tomorrow
    at = pipeline._ts(when)
    db.set_batch(batch_id, send_at=at, send_kind="schedule", pause_reason="")
    db.log(batch_id, None, "info", "schedule", f"Sending scheduled for {at[:16].replace('T', ' ')} UTC ({waiting} students)")
    return batch_summary(db.get_batch(batch_id))


@app.delete("/api/batches/{batch_id}/schedule", dependencies=[Depends(require_auth)])
def cancel_schedule(batch_id: int):
    b = get_batch_or_404(batch_id)
    if b["send_at"]:
        db.set_batch(batch_id, send_at="", send_kind="")
        db.log(batch_id, None, "info", "schedule",
               "Scheduled send cancelled" if b["send_kind"] == "schedule" else "Automatic continue turned off; press Send to continue")
    return batch_summary(db.get_batch(batch_id))


@app.post("/api/batches/{batch_id}/generate", dependencies=[Depends(require_auth)])
def start_generate(batch_id: int):
    get_batch_or_404(batch_id)
    if not pipeline.run_job(batch_id, "generating", pipeline.generate):
        raise HTTPException(409, "Please wait: this batch is busy")
    return {"started": True}


@app.post("/api/batches/{batch_id}/send", dependencies=[Depends(require_auth)])
def start_send(batch_id: int, body: SendIn):
    b = get_batch_or_404(batch_id)
    if not body.confirm:
        raise HTTPException(400, "Sending requires explicit confirmation")
    ensure_idle(batch_id)
    _preflight(b)
    if not pipeline.run_job(batch_id, "sending", pipeline.send, allow_resend=body.allow_resend):
        raise HTTPException(409, "Please wait: this batch is busy")
    return {"started": True}


@app.post("/api/batches/{batch_id}/retry", dependencies=[Depends(require_auth)])
def retry_failed(batch_id: int):
    b = get_batch_or_404(batch_id)
    ids = [r["id"] for r in db.query("SELECT id FROM records WHERE batch_id=? AND status='Failed'", (batch_id,))]
    if not ids:
        return {"started": False, "message": "No failed students"}
    ensure_idle(batch_id)
    _preflight(b)
    if not pipeline.run_job(batch_id, "sending", pipeline.send, record_ids=ids):
        raise HTTPException(409, "Please wait: this batch is busy")
    return {"started": True, "count": len(ids)}


@app.post("/api/records/{record_id}/resend", dependencies=[Depends(require_auth)])
def resend_record(record_id: int):
    rec = get_record_or_404(record_id)
    if rec["status"] == "Invalid":
        raise HTTPException(400, "Fix this student's problems first")
    if rec["delivery"] == "claim":
        raise HTTPException(400, "This student has no usable email; share the claim link instead")
    ensure_idle(rec["batch_id"])
    _preflight(db.get_batch(rec["batch_id"]))
    if not pipeline.run_job(rec["batch_id"], "sending", pipeline.send, record_ids=[record_id], allow_resend=True):
        raise HTTPException(409, "Please wait: this batch is busy")
    db.log(rec["batch_id"], record_id, "info", "resend", "Organizer authorized a resend")
    return {"started": True}


@app.post("/api/batches/{batch_id}/verify-seal", dependencies=[Depends(require_auth)])
def verify_seal(batch_id: int):
    get_batch_or_404(batch_id)
    result = pipeline.verify_batch(batch_id)
    if result["sealed"]:
        db.log(batch_id, None, "ok" if result["intact"] else "block", "seal",
               "Seal verified: batch intact" if result["intact"] else f"Seal check FAILED: {len(result['problems'])} problems")
    return result


@app.get("/api/batches/{batch_id}/events", dependencies=[Depends(require_auth)])
def batch_events(batch_id: int, limit: int = 300):
    get_batch_or_404(batch_id)
    return db.query("SELECT e.id, e.at, e.level, e.kind, e.message, e.record_id, r.name, r.row_no FROM events e "
                    "LEFT JOIN records r ON r.id=e.record_id WHERE e.batch_id=? ORDER BY e.id DESC LIMIT ?",
                    (batch_id, min(max(limit, 1), 2000)))


@app.get("/api/records/{record_id}/certificate", dependencies=[Depends(require_auth)])
def download_certificate(record_id: int):
    rec = get_record_or_404(record_id)
    if not rec["cert_file"]:
        raise HTTPException(404, "Certificate not generated yet")
    path = config.CERT_DIR / rec["cert_file"]
    return FileResponse(path, media_type="application/pdf", filename=path.name)


@app.get("/api/batches/{batch_id}/report.csv", dependencies=[Depends(require_auth)])
def report(batch_id: int):
    get_batch_or_404(batch_id)
    out = io.StringIO()
    out.write("﻿")  # BOM so Excel opens UTF-8 names (José, Fatima Al-Sayed) correctly
    w = csv.writer(out)
    w.writerow(["Row", "Student ID", "Name", "Email", "Email Source", "Email Fix", "Delivery", "Category", "Template",
                "Certificate Title", "Status", "Certificate ID", "Certificate File", "SHA-256", "Attempts", "Sent At",
                "Claim Link", "Verify Link", "Errors", "Warnings", "Last Error"])
    for r in db.query("SELECT * FROM records WHERE batch_id=? ORDER BY row_no", (batch_id,)):
        w.writerow([r["row_no"], r["student_id"], r["name"], r["email"], r["email_source"], r["email_fix"],
                    r["delivery"], r["rule_label"] or r["category"], certificate.resolve(r["theme"])["name"] if r["theme"] else "",
                    r["title"], r["status"], r["cert_code"], Path(r["cert_file"]).name if r["cert_file"] else "",
                    r["pdf_sha256"], r["attempts"], r["sent_at"], claim_url(r) if r["cert_file"] else "",
                    verify_url(r) if r["payload_json"] else "", r["errors"], r["warnings"], r["last_error"]])
    return Response(out.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="sealsend_report_{batch_id}.csv"'})


@app.get("/api/batches/{batch_id}/certificates.zip", dependencies=[Depends(require_auth)])
def certificates_zip(batch_id: int):
    b = get_batch_or_404(batch_id)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        manifest = []
        for r in db.query("SELECT * FROM records WHERE batch_id=? AND cert_file!='' ORDER BY row_no", (batch_id,)):
            p = config.CERT_DIR / r["cert_file"]
            if p.exists():
                folder = (r["rule_label"] or "All").replace("/", "-").replace("\\", "-")
                z.write(p, f"{folder}/{p.name}")
                manifest.append({"file": f"{folder}/{p.name}", "code": r["cert_code"], "sha256": r["pdf_sha256"],
                                 "leaf": r["leaf_hash"], "signature": r["signature"], "payload": json.loads(r["payload_json"] or "{}")})
        z.writestr("SEAL.json", json.dumps({"seal": b["seal"], "public_key_pem": crypto.public_pem(),
                                            "certificates": manifest}, indent=2, ensure_ascii=False))
    buf.seek(0)
    return StreamingResponse(buf, media_type="application/zip",
                             headers={"Content-Disposition": f'attachment; filename="sealsend_batch_{batch_id}.zip"'})


# ---------------------------------------------------------------- templates
@app.get("/api/templates", dependencies=[Depends(require_auth)])
def list_templates():
    return certificate.catalog()


def _org_for(batch: int | None) -> dict:
    org = settings.org()
    if batch:
        b = db.get_batch(batch)
        if b:
            org = {**org, **{k: v for k, v in pipeline.normalize_config(b["config"])["branding"].items() if v}}
    return org


@app.get("/api/templates/{template_id}/thumb.png", dependencies=[Depends(require_auth)])
def template_thumb(template_id: str, batch: int | None = None, dpi: int = 48):
    if not T.get_template(template_id):
        raise HTTPException(404, "Unknown template")
    png = certificate.thumbnail(template_id, _org_for(batch), dpi=min(max(dpi, 30), 110))
    return Response(png, media_type="image/png", headers={"Cache-Control": "private, max-age=300"})


@app.get("/api/templates/{template_id}", dependencies=[Depends(require_auth)])
def template_detail(template_id: str):
    t = T.get_template(template_id)
    if not t:
        raise HTTPException(404, "Unknown template")
    w, h = T.page_size(t)
    return {"id": t["id"], "name": t["name"], "custom": bool(t.get("custom")), "fields": t["fields"], "width": w,
            "height": h, "title": t.get("title", "")}


@app.get("/api/templates/{template_id}/background.png", dependencies=[Depends(require_auth)])
def template_background(template_id: str):
    t = T.get_template(template_id)
    if not t:
        raise HTTPException(404, "Unknown template")
    png = certificate.pdf_to_png(T.render(dict(t, fields=[]), {}, {}, {}), 90)
    return Response(png or b"", media_type="image/png", headers={"Cache-Control": "no-store"})


@app.post("/api/templates/import", dependencies=[Depends(require_auth)])
async def import_template(file: UploadFile = File(...), name: str = Form("")):
    data = await _read_upload(file, (".png", ".jpg", ".jpeg", ".webp", ".pdf"))
    try:
        tid = certificate.import_template(data, file.filename or "design.png", name.strip())
    except ValueError as e:
        raise HTTPException(400, str(e))
    return template_detail(tid)


class TemplateIn(BaseModel):
    fields: list
    name: str | None = None


@app.put("/api/templates/{template_id}", dependencies=[Depends(require_auth)])
def save_template(template_id: str, body: TemplateIn):
    t = T.get_template(template_id)
    if not t or not t.get("custom"):
        raise HTTPException(400, "Only imported templates can be edited")
    certificate.update_template_fields(template_id, body.fields, body.name)
    for b in db.query("SELECT id, config_json FROM batches"):  # make certificates that use it re-render
        cfg = pipeline.normalize_config(json.loads(b["config_json"]))
        if template_id in pipeline.used_templates(cfg) and not pipeline.is_running(b["id"]):
            pipeline.revalidate(b["id"])
    return template_detail(template_id)


@app.post("/api/templates/{template_id}/preview", dependencies=[Depends(require_auth)])
def preview_template_fields(template_id: str, body: TemplateIn, batch: int | None = None):
    t = T.get_template(template_id)
    if not t:
        raise HTTPException(404, "Unknown template")
    org = _org_for(batch)
    pdf = T.render(dict(t, fields=body.fields), certificate.sample_values(t, org),
                   {k: org.get(k, "") for k in ("logo", "signer1_sig", "signer2_sig")}, {})
    return Response(certificate.pdf_to_png(pdf, 90) or b"", media_type="image/png", headers={"Cache-Control": "no-store"})


@app.delete("/api/templates/{template_id}", dependencies=[Depends(require_auth)])
def remove_template(template_id: str):
    t = T.get_template(template_id)
    if not t or not t.get("custom"):
        raise HTTPException(400, "Built-in templates can't be deleted")
    for b in db.query("SELECT id, name, config_json FROM batches"):
        if template_id in pipeline.used_templates(pipeline.normalize_config(json.loads(b["config_json"]))):
            raise HTTPException(400, f"“{b['name']}” still uses this template. Pick another one there first.")
    certificate.delete_template(template_id)
    return {"ok": True}


# ---------------------------------------------------------------- settings
@app.get("/api/settings", dependencies=[Depends(require_auth)])
def get_settings():
    return {"mail": settings.public_mail(), "mail2": settings.public_mail("mail2"), "org": settings.org(), "public_base_url": config.public_base_url(),
            "public_base_url_custom": settings.get("public_base_url", ""), "lan_url": config.app_url(), "hosted": bool(config.hosted_url()),
            "temporary_url": config.is_temporary_url(), "providers": settings.PROVIDERS, "quota": pipeline.quota_status(), "mode": config.mail_mode(),
            "admin_from_env": bool(config.ADMIN_PASSWORD)}


class MailIn(BaseModel):
    provider: str
    address: str = ""
    from_name: str = ""
    reply_to: str = ""
    password: str = ""
    host: str = ""
    port: int = 587
    security: str = "starttls"
    username: str = ""
    daily_cap: int = 0
    delay_seconds: float = 1.0
    auto_resume: bool = True


@app.put("/api/settings/mail", dependencies=[Depends(require_auth)])
def save_mail(body: MailIn):
    from .data import valid_email
    if body.provider not in settings.PROVIDERS:
        raise HTTPException(400, "Choose an email provider")
    if body.provider in ("gmail", "outlook", "smtp", "resend") and not valid_email(body.address.strip()):
        raise HTTPException(400, "Enter the email address the certificates will be sent from")
    if body.provider == "gmail" and body.password and len(body.password.replace(" ", "")) != 16:
        raise HTTPException(400, "A Gmail App Password is exactly 16 letters (spaces don't matter). "
                                 "It is not your normal Gmail password.")
    if body.provider == "smtp" and not body.host.strip():
        raise HTTPException(400, "Enter the SMTP server address")
    if body.security not in ("starttls", "ssl", "none"):
        raise HTTPException(400, "Unknown security setting")
    settings.update_mail({**body.model_dump(), "address": body.address.strip(), "host": body.host.strip(),
                          "daily_cap": max(0, min(body.daily_cap, 100000)), "delay_seconds": max(0.0, min(body.delay_seconds, 30.0))})
    return {"mail": settings.public_mail(), "mode": config.mail_mode()}


@app.put("/api/settings/mail2", dependencies=[Depends(require_auth)])
def save_mail2(body: MailIn):
    """The optional second sending account: used when the main one reaches its daily limit."""
    from .data import valid_email
    if body.provider not in ("gmail", "outlook", "smtp"):
        raise HTTPException(400, "The second account can be Gmail, Outlook or another SMTP server")
    address = body.address.strip()
    if not valid_email(address):
        raise HTTPException(400, "Enter the second account's email address")
    if address.lower() == (settings.mail().get("address") or "").lower():
        raise HTTPException(400, "That is already your main sending account. Use a different address.")
    if body.provider == "gmail" and body.password and len(body.password.replace(" ", "")) != 16:
        raise HTTPException(400, "A Gmail App Password is exactly 16 letters (spaces don't matter). "
                                 "It is not your normal Gmail password.")
    old = settings.mail("mail2")
    same = old.get("provider") == body.provider and (old.get("address") or "").lower() == address.lower()
    if not body.password and not (same and old.get("password")):
        raise HTTPException(400, "Enter the App Password for the second account" if body.provider == "gmail"
                            else "Enter the password for the second account")
    if body.provider == "smtp" and not body.host.strip():
        raise HTTPException(400, "Enter the SMTP server address")
    if body.security not in ("starttls", "ssl", "none"):
        raise HTTPException(400, "Unknown security setting")
    settings.update_mail({"provider": body.provider, "address": address, "password": body.password, "host": body.host.strip(),
                          "port": body.port, "security": body.security, "reply_to": body.reply_to.strip(),
                          "daily_cap": max(0, min(body.daily_cap, 100000))}, slot="mail2")
    return {"mail2": settings.public_mail("mail2"), "quota": pipeline.quota_status()}


@app.delete("/api/settings/mail2", dependencies=[Depends(require_auth)])
def remove_mail2():
    settings.clear_mail2()
    return {"mail2": settings.public_mail("mail2"), "quota": pipeline.quota_status()}


@app.post("/api/settings/test-connection2", dependencies=[Depends(require_auth)])
def test_connection2():
    m2 = settings.mail2()
    if not m2:
        raise HTTPException(400, "Add the second account first")
    res = mailer.check_connection(m2)
    if res["ok"]:
        settings.mark_verified(db.now(), slot="mail2")
    return res


@app.post("/api/settings/test-connection", dependencies=[Depends(require_auth)])
def test_connection():
    res = mailer.check_connection()
    if res["ok"] and res["mode"] != "sandbox":
        settings.mark_verified(db.now())
    return res


class TestEmailIn(BaseModel):
    to: str


@app.post("/api/settings/test-email", dependencies=[Depends(require_auth)])
def test_email(body: TestEmailIn):
    from .data import valid_email
    if not valid_email(body.to.strip()):
        raise HTTPException(400, "Enter a valid email address")
    org = settings.org()
    tpl = certificate.resolve(certificate.DEFAULT_TEMPLATE_ID)
    pdf = T.render(tpl, certificate.sample_values(tpl, org), {k: org.get(k, "") for k in ("logo", "signer1_sig", "signer2_sig")},
                   {"title": "Sample certificate", "subject": "SS-SAMPLE0000"})
    msg = mailer.build_message(body.to.strip(), "SendEZ test", "SendEZ test email: your setup works",
                               "Hello!\n\nThis is a test from SendEZ. If you can read this and open the attached sample "
                               "certificate, students will receive their certificates the same way.\n\nSendEZ",
                               pdf, "Sample_Certificate.pdf", "SS-SAMPLE0000")
    try:
        with mailer.Sender() as s:
            detail = s.send(msg, body.to.strip(), "TEST")
    except Exception as e:
        return {"ok": False, "message": str(e)}
    return {"ok": True, "message": detail}


class OrgIn(BaseModel):
    org_name: str = ""
    logo: str = ""
    signer1_name: str = ""
    signer1_title: str = ""
    signer1_sig: str = ""
    signer2_name: str = ""
    signer2_title: str = ""
    signer2_sig: str = ""


@app.put("/api/settings/org", dependencies=[Depends(require_auth)])
def save_org(body: OrgIn):
    settings.update_org(body.model_dump())
    return settings.org()


class UrlIn(BaseModel):
    url: str = ""


@app.put("/api/settings/public-url", dependencies=[Depends(require_auth)])
def save_public_url(body: UrlIn):
    url = body.url.strip().rstrip("/")
    if url and not (url.startswith("http://") or url.startswith("https://")):
        raise HTTPException(400, "The address must start with http:// or https://")
    data = settings.load()
    data["public_base_url"] = url
    settings.save(data)
    return {"public_base_url": config.public_base_url()}


@app.post("/api/branding/upload", dependencies=[Depends(require_auth)])
async def upload_branding(file: UploadFile = File(...), kind: str = Form("logo")):
    if kind not in ("logo", "signer1_sig", "signer2_sig"):
        raise HTTPException(400, "Unknown image kind")
    data = await _read_upload(file, (".png", ".jpg", ".jpeg", ".webp"))
    from PIL import Image
    try:
        im = Image.open(io.BytesIO(data))
        im.load()
    except Exception:
        raise HTTPException(400, "That image could not be read")
    im = im.convert("RGBA")
    im.thumbnail((1200, 1200))
    name = f"{kind.split('_')[0]}_{secrets.token_hex(6)}.png"
    im.save(config.UPLOAD_DIR / name, "PNG", optimize=True)
    return {"file": name}


@app.get("/api/branding/{name}", dependencies=[Depends(require_auth)])
def branding_image(name: str):
    path = config.UPLOAD_DIR / Path(name).name
    if not path.exists() or path.name.split("_")[0] not in ("logo", "signer1", "signer2") or path.suffix != ".png":
        raise HTTPException(404, "Not found")
    return FileResponse(path, media_type="image/png")


@app.post("/api/desktop/open-browser", dependencies=[Depends(require_auth)])
def open_in_browser(request: Request):
    """Desktop app: open SendEZ in the computer's normal browser (it signs in there separately)."""
    if not (config.DESKTOP and _is_local(request)):
        raise HTTPException(404, "Not available")
    import webbrowser
    webbrowser.open(f"http://127.0.0.1:{config.PORT}/")
    return {"ok": True}


# ---------------------------------------------------------------- help assistant
class AskIn(BaseModel):
    message: str
    history: list = []
    batch_id: int | None = None


@app.post("/api/assistant", dependencies=[Depends(require_auth)])
def ask_assistant(body: AskIn):
    q = pipeline.quota_status()
    m = settings.public_mail()
    state = {"mail_mode": config.mail_mode(), "sending_account": m.get("address", ""), "provider": m.get("provider", ""),
             "connection_tested": bool(m.get("verified_at")), "quota_left": q["left"], "events": db.one("SELECT COUNT(*) n FROM batches")["n"],
             "sharing_online": tunnel.share.status()["state"] == "on"}
    if body.batch_id:
        b = db.get_batch(body.batch_id)
        if b:
            state["open_event"] = {"name": b["name"], "counts": db.status_counts(b["id"]), "club": (b["config"] or {}).get("club", ""),
                                   "paused": b.get("pause_reason", "")}
    history = [h for h in body.history if isinstance(h, dict)][-6:]
    return assistant.ask(body.message, history, state)


# ---------------------------------------------------------------- Mahindra University clubs
@app.get("/api/clubs", dependencies=[Depends(require_auth)])
def list_clubs():
    return clubs.catalog()


@app.get("/api/clubs/{key}/logo.png", dependencies=[Depends(require_auth)])
def club_logo(key: str):
    if key != "university" and key not in clubs.CLUB_BY_ID:
        raise HTTPException(404, "Unknown club")
    p = clubs.logo_path(key)
    if p:
        return FileResponse(p, headers={"Cache-Control": "no-store"})
    return Response(clubs.placeholder_png(key), media_type="image/png", headers={"Cache-Control": "no-store"})


@app.post("/api/clubs/{key}/logo", dependencies=[Depends(require_auth)])
async def upload_club_logo(key: str, file: UploadFile = File(...)):
    data = await _read_upload(file, (".png", ".jpg", ".jpeg", ".webp"))
    try:
        clubs.set_logo(key, data)
    except ValueError as e:
        raise HTTPException(400, str(e))
    # events that use this club pick up the new combined logo
    for b in db.query("SELECT id, config_json FROM batches"):
        cfg = json.loads(b["config_json"])
        club = cfg.get("club", "")
        if club and (key == "university" or key == club) and not pipeline.is_running(b["id"]):
            cfg.setdefault("branding", {})["logo"] = clubs.composite_logo(club)
            db.save_config(b["id"], cfg)
            pipeline.revalidate(b["id"])
    return clubs.catalog()


# ---------------------------------------------------------------- share online (free public link)
def _share_status() -> dict:
    st = tunnel.share.status()
    external = bool(config.hosted_url()) and st["state"] != "on"  # already online some other way (website, run.py --online)
    return {**st, "available": not external, "external_url": config.hosted_url() if external else ""}


@app.get("/api/share", dependencies=[Depends(require_auth)])
def share_status():
    return _share_status()


@app.post("/api/share/start", dependencies=[Depends(require_auth)])
def share_start():
    st = _share_status()
    if not st["available"]:
        raise HTTPException(400, f"SendEZ is already online at {st['external_url']}")
    tunnel.share.start(config.PORT, config.DATA_DIR / "tools")
    return _share_status()


@app.post("/api/share/stop", dependencies=[Depends(require_auth)])
def share_stop():
    tunnel.share.stop()
    return _share_status()


@app.get("/api/qr.png", dependencies=[Depends(require_auth)])
def qr_png(data: str):
    """QR code image, e.g. for opening SendEZ on a phone. Generated locally, no outside service."""
    from reportlab.graphics import renderPDF
    from reportlab.graphics.barcode import qr as rl_qr
    from reportlab.graphics.shapes import Drawing
    from reportlab.pdfgen import canvas as rl_canvas
    if len(data) > 500:
        raise HTTPException(400, "Too long")
    size = 200
    widget = rl_qr.QrCodeWidget(data)
    x0, y0, x1, y1 = widget.getBounds()
    d = Drawing(size, size, transform=[size / (x1 - x0), 0, 0, size / (y1 - y0), 0, 0])
    d.add(widget)
    buf = io.BytesIO()
    c = rl_canvas.Canvas(buf, pagesize=(size + 20, size + 20))
    renderPDF.draw(d, c, 10, 10)
    c.save()
    return Response(certificate.pdf_to_png(buf.getvalue(), 144) or b"", media_type="image/png")


# ---------------------------------------------------------------- chaos test
@app.post("/api/chaos", dependencies=[Depends(require_auth)])
def start_chaos():
    batch_id = chaos.create()
    pipeline.run_job(batch_id, "chaos", chaos.run)
    return {"batch_id": batch_id}


@app.get("/api/batches/{batch_id}/chaos/forged.pdf", dependencies=[Depends(require_auth)])
def chaos_forged(batch_id: int):
    b = get_batch_or_404(batch_id)
    rep = (b["chaos"] or {}).get("report")
    if not rep:
        raise HTTPException(404, "Run the Chaos Test first")
    return FileResponse(config.DATA_DIR / "chaos" / rep["forged_file"], media_type="application/pdf",
                        filename=f"FORGED_{rep['verify_code']}.pdf")


@app.get("/sample/{name}", dependencies=[Depends(require_auth)])
def sample_file(name: str):
    path = config.SAMPLE_DIR / Path(name).name
    if not path.exists() or path.suffix not in (".csv", ".xlsx"):
        raise HTTPException(404, "Not found")
    return FileResponse(path, filename=path.name)


# ---------------------------------------------------------------- public pages
PUBLIC_CSS = """
:root{--bg:#f2f2f2;--card:#fff;--ink:#000;--sub:#6e6e73;--line:#e5e5e5;--fill:#000;--on:#fff}
@media (prefers-color-scheme:dark){:root{--bg:#000;--card:#1c1c1e;--ink:#fff;--sub:#98989f;--line:#38383a;--fill:#fff;--on:#000}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.45 -apple-system,BlinkMacSystemFont,"SF Pro Text","Segoe UI",Roboto,Arial,sans-serif;-webkit-font-smoothing:antialiased}
.wrap{max-width:520px;margin:0 auto;padding:40px 16px 60px}
.brand{font-size:13px;font-weight:600;letter-spacing:.08em;text-transform:uppercase;color:var(--sub);text-align:center;margin-bottom:28px}
.icon{width:76px;height:76px;border-radius:50%;display:flex;align-items:center;justify-content:center;margin:0 auto 16px;font-size:38px;font-weight:700}
.icon.ok{background:var(--fill);color:var(--on)}.icon.bad{border:3px solid var(--ink);color:var(--ink)}
h1{font-size:30px;line-height:1.15;margin:0 0 6px;text-align:center;letter-spacing:-.02em}
.lead{text-align:center;color:var(--sub);margin:0 0 26px}.lead b{color:var(--ink)}
.group{background:var(--card);border-radius:14px;overflow:hidden;margin:0 0 22px}
.row{display:flex;justify-content:space-between;gap:16px;padding:13px 16px;border-top:.5px solid var(--line);font-size:15px}
.row:first-child{border-top:0}.row span:first-child{color:var(--sub)}.row span:last-child{text-align:right;font-weight:500;word-break:break-word}
.cap{font-size:13px;color:var(--sub);text-transform:uppercase;letter-spacing:.04em;margin:0 16px 8px}
.btn{display:block;width:100%;border:0;border-radius:12px;padding:15px;font-family:inherit;font-size:17px;font-weight:600;line-height:1.2;background:var(--fill);color:var(--on);cursor:pointer;text-align:center;text-decoration:none}
input[type=text],input[type=file]{width:100%;font:inherit;padding:13px 14px;border-radius:12px;border:1px solid var(--line);background:var(--card);color:var(--ink);margin-bottom:12px}
.actions{display:grid;gap:10px;margin:0 0 22px}.btn.li{background:#0a66c2;color:#fff}
.btn.sec{background:var(--card);color:var(--ink);box-shadow:inset 0 0 0 1px var(--line)}
.sharealt{display:flex;gap:10px}.sharealt[hidden]{display:none}.sharealt .btn{font-size:15px;padding:12px}
.note{font-size:13px;color:var(--sub);text-align:center;margin-top:18px}.mono{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:13px}
"""


def page(title: str, body: str, head: str = "") -> HTMLResponse:
    return HTMLResponse(f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(title)} · SendEZ</title>{head}
<style>{PUBLIC_CSS}</style></head><body><div class="wrap"><div class="brand">SendEZ Verify</div>{body}</div></body></html>""")


def _upload_form() -> str:
    return """<p class="cap">Check a PDF file</p>
<form class="group" style="padding:16px" method="post" action="/verify" enctype="multipart/form-data">
<input type="file" name="file" accept="application/pdf" required><button class="btn">Verify file</button></form>"""


def _footer(issuer: str = "") -> str:
    issuer = issuer or settings.org().get("org_name") or config.ISSUER_NAME
    return (f'<p class="note">Issuer: {html.escape(issuer)}<br>Signing key '
            f'<span class="mono">{crypto.fingerprint()}</span> · <a href="/.well-known/sealsend-public-key.pem" '
            'style="color:inherit">public key</a></p>')


def _proof_rows(rec: dict, proof: dict) -> str:
    p = proof["payload"]
    tick = lambda ok: "✓ Valid" if ok else "✕ Failed"  # noqa: E731
    rows = [("Certificate", p.get("title", "")), ("Certificate ID", rec["cert_code"]), ("Issued by", p.get("issuer", "")),
            ("Digital signature", f"Ed25519 {tick(proof['signature_valid'] and proof['record_consistent'])}"),
            ("Batch seal", (f"✓ Included (#{proof.get('leaf_index', 0) + 1} of {proof.get('leaf_count', 0)})"
                            if proof["in_seal"] else "✕ Not in seal")),
            ("Seal signature", tick(proof["seal_signature_valid"])),
            ("File fingerprint", f"{rec['pdf_sha256'][:12]}…{rec['pdf_sha256'][-6:]}")]
    return "".join(f"<div class='row'><span>{html.escape(k)}</span><span>{html.escape(str(v))}</span></div>" for k, v in rows if v)


@app.get("/verify", response_class=HTMLResponse)
def verify_home():
    return page("Verify a certificate", f"""<h1>Verify a certificate</h1>
<p class="lead">Scan the QR code on a certificate, or upload the PDF to check it hasn’t been edited.</p>
<form class="group" style="padding:16px" method="get" onsubmit="location.href='/verify/'+encodeURIComponent(this.c.value.trim());return false">
<input type="text" name="c" placeholder="Certificate ID, e.g. SS-ABCD234567" required><button class="btn">Look up</button></form>
{_upload_form()}{_footer()}""")


@app.get("/verify/{code}", response_class=HTMLResponse)
def verify(code: str):
    rec = db.one("SELECT * FROM records WHERE cert_code=? AND payload_json!=''", (code,))
    if not rec:
        return page("Not found", f"""<div class="icon bad">✕</div><h1>Not verified</h1>
<p class="lead">No certificate <b>{html.escape(code)}</b> was issued by this issuer. It may be fake.</p>{_upload_form()}{_footer()}""")
    proof = pipeline.record_proof(rec)
    p = proof["payload"]
    if not proof["valid"]:
        return page("Verification failed", f"""<div class="icon bad">✕</div><h1>Verification failed</h1>
<p class="lead">The issuer’s records for <b>{html.escape(code)}</b> do not pass the cryptographic checks.</p>
<div class="group">{_proof_rows(rec, proof)}</div>{_footer()}""")
    detail = html.escape(p.get("description") or " · ".join(x for x in (p.get("achievement"), p.get("event")) if x))
    return page("Verified", f"""<div class="icon ok">✓</div><h1>Verified</h1>
<p class="lead">Issued to <b>{html.escape(p.get('name', ''))}</b><br>{detail}</p>
{_share_actions(rec, p)}<div class="group">{_proof_rows(rec, proof)}</div>{_upload_form()}{_footer(p.get('issuer', ''))}""",
                head=_share_meta(rec, p))


def _verify_link(code: str) -> str:
    return f"{config.public_base_url()}/verify/{code}"


def _share_meta(rec: dict, p: dict) -> str:
    """Open Graph tags: a shared verify link shows a proper preview card in WhatsApp, LinkedIn, X and iMessage."""
    url = _verify_link(rec["cert_code"])
    title = f"Verified certificate: {p.get('name', '')}"
    desc = " · ".join(x for x in (sharecard.certification_name(p.get("title", ""), p.get("event", "")), p.get("issuer", "")) if x)
    tags = {"og:type": "website", "og:title": title, "og:description": desc, "og:url": url,
            "og:image": f"{url}/card.png", "og:image:width": "1200", "og:image:height": "630"}
    return "".join(f'<meta property="{k}" content="{html.escape(v)}">' for k, v in tags.items()) + \
        f'<meta name="twitter:card" content="summary_large_image"><meta name="description" content="{html.escape(desc)}">'


def _share_actions(rec: dict, p: dict) -> str:
    url = _verify_link(rec["cert_code"])
    li = sharecard.linkedin_url(p.get("title", ""), p.get("event", ""), p.get("issuer", ""), url, rec["cert_code"],
                                sharecard.issued_on(rec))
    text = f"My certificate for {p.get('event') or 'the event'} is verified:"
    wa = "https://wa.me/?" + urlencode({"text": f"{text} {url}"})
    return f"""<div class="actions"><a class="btn li" href="{html.escape(li)}" target="_blank" rel="noopener">Add to LinkedIn profile</a>
<button class="btn sec" id="shareBtn" type="button">Share</button>
<div class="sharealt" id="shareAlt" hidden><a class="btn sec" href="{html.escape(wa)}" target="_blank" rel="noopener">WhatsApp</a>
<button class="btn sec" id="copyBtn" type="button">Copy link</button></div></div>
<script>(function(){{var u={json.dumps(url)},t={json.dumps(text)},b=document.getElementById('shareBtn'),a=document.getElementById('shareAlt');
b.onclick=function(){{if(navigator.share){{navigator.share({{title:document.title,text:t,url:u}}).catch(function(){{}});}}else{{b.hidden=true;a.hidden=false;}}}};
document.getElementById('copyBtn').onclick=function(){{var c=this;(navigator.clipboard?navigator.clipboard.writeText(u):Promise.reject()).then(function(){{c.textContent='Copied';}},function(){{prompt('Copy this link',u);}});}};}})();</script>"""


@app.get("/api/batches/{batch_id}/feedback", dependencies=[Depends(require_auth)])
def batch_feedback(batch_id: int):
    b = get_batch_or_404(batch_id)
    asked = db.one("SELECT COUNT(*) n FROM records WHERE batch_id=? AND feedback_token!=''", (batch_id,))["n"]
    return {**pipeline.feedback_summary(batch_id), "enabled": bool(b["config"].get("feedback")), "asked": asked}


@app.get("/api/batches/{batch_id}/feedback.csv", dependencies=[Depends(require_auth)])
def batch_feedback_csv(batch_id: int):
    b = get_batch_or_404(batch_id)
    rows = db.query("SELECT f.stars, f.comment, f.at, r.rule_label FROM feedback f JOIN records r ON r.id = f.record_id "
                    "WHERE f.batch_id=? ORDER BY f.at", (batch_id,))
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["Stars", "Comment", "Category", "Date"])  # no names: feedback is shown anonymously
    for r in rows:
        w.writerow([r["stars"], r["comment"], r["rule_label"], r["at"][:10]])
    name = "".join(ch if ch.isalnum() or ch in " -_" else "_" for ch in b["name"]).strip() or "event"
    return Response("\ufeff" + out.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name} - feedback.csv"'})


def _report(club: str, start: str, end: str) -> dict:
    from datetime import date as _date
    for d in (start, end):
        if d:
            try:
                _date.fromisoformat(d)
            except ValueError:
                raise HTTPException(400, "Dates must look like 2026-07-01")
    try:
        return reports.build(club or "all", start, end)
    except ValueError as e:
        raise HTTPException(400, str(e))


def _report_filename(rep: dict, ext: str) -> str:
    name = "".join(ch if ch.isalnum() or ch in " -_" else " " for ch in rep["title"]).split()
    return f"{' '.join(name)} - {reports.period_text(rep['start'], rep['end']).replace('–', 'to')}.{ext}".replace("  ", " ")


@app.get("/api/reports", dependencies=[Depends(require_auth)])
def club_report(club: str = "all", start: str = "", end: str = ""):
    return _report(club, start, end)


@app.get("/api/reports.pdf", dependencies=[Depends(require_auth)])
def club_report_pdf(club: str = "all", start: str = "", end: str = ""):
    rep = _report(club, start, end)
    return Response(reports.to_pdf(rep), media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{_report_filename(rep, "pdf")}"'})


@app.get("/api/reports.csv", dependencies=[Depends(require_auth)])
def club_report_csv(club: str = "all", start: str = "", end: str = ""):
    rep = _report(club, start, end)
    return Response(reports.to_csv(rep), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{_report_filename(rep, "csv")}"'})


def _feedback_rec(token: str) -> dict:
    rec = db.one("SELECT * FROM records WHERE feedback_token=?", (token,)) if token else None
    if not rec:
        raise HTTPException(404, "Unknown feedback link")
    return rec


STAR_CSS = """<style>.stars{display:flex;flex-direction:row-reverse;justify-content:center;gap:6px;margin:6px 0 18px}
.stars input{position:absolute;opacity:0;width:0;height:0}.stars label{font-size:46px;line-height:1;color:var(--line);cursor:pointer;
padding:2px}.stars input:checked~label,.stars label:hover,.stars label:hover~label{color:#f5a623}
textarea{width:100%;font:inherit;padding:12px 14px;border-radius:12px;border:1px solid var(--line);background:var(--card);
color:var(--ink);min-height:90px;margin-bottom:12px;resize:vertical}</style>"""


@app.get("/feedback/{token}", response_class=HTMLResponse)
def feedback_page(token: str, stars: int = 0):
    try:
        rec = _feedback_rec(token)
    except HTTPException:
        return page("Not found", '<div class="icon bad">✕</div><h1>Link not found</h1><p class="lead">This feedback link is not valid.</p>')
    prev = db.one("SELECT stars, comment FROM feedback WHERE record_id=?", (rec["id"],))
    chosen = stars if 1 <= stars <= 5 else (prev["stars"] if prev else 0)
    event = json.loads(rec["payload_json"] or "{}").get("event", "") or "the event"
    radios = "".join(f'<input type="radio" name="stars" id="s{n}" value="{n}" {"checked" if n == chosen else ""} required>'
                     f'<label for="s{n}" title="{n} out of 5">★</label>' for n in range(5, 0, -1))
    return page("Rate the event", f"""{STAR_CSS}<h1>How was it?</h1>
<p class="lead">Your rating for <b>{html.escape(event)}</b> helps the organizers make the next one better.</p>
<form class="group" style="padding:16px" method="post"><div class="stars">{radios}</div>
<textarea name="comment" maxlength="1000" placeholder="Anything to add? (optional)">{html.escape(prev["comment"] if prev else "")}</textarea>
<button class="btn">{"Update my rating" if prev else "Send rating"}</button></form>
<p class="note">Your feedback is shared with the organizers without your name.</p>""")


@app.post("/feedback/{token}", response_class=HTMLResponse)
def feedback_submit(token: str, stars: int = Form(0), comment: str = Form("")):
    try:
        rec = _feedback_rec(token)
    except HTTPException:
        return page("Not found", '<div class="icon bad">✕</div><h1>Link not found</h1><p class="lead">This feedback link is not valid.</p>')
    if not 1 <= stars <= 5:
        return HTMLResponse(page("Pick a rating", '<div class="icon bad">!</div><h1>Pick 1 to 5 stars</h1>'
                                 f'<a class="btn" href="/feedback/{html.escape(token)}">Back</a>').body, status_code=400)
    with db.tx() as c:
        c.execute("INSERT INTO feedback (record_id, batch_id, stars, comment, at) VALUES (?,?,?,?,?) "
                  "ON CONFLICT(record_id) DO UPDATE SET stars=excluded.stars, comment=excluded.comment, at=excluded.at",
                  (rec["id"], rec["batch_id"], stars, comment.strip()[:1000], db.now()))
    return page("Thank you", f"""<div class="icon ok">✓</div><h1>Thank you!</h1>
<p class="lead">You rated it <b>{"★" * stars}{"☆" * (5 - stars)}</b></p>
<p class="note"><a href="/feedback/{html.escape(token)}" style="color:inherit">Change my rating</a></p>""")


@app.get("/verify/{code}/card.png")
def verify_card(code: str):
    rec = db.one("SELECT * FROM records WHERE cert_code=? AND payload_json!=''", (code,))
    if not rec or not pipeline.record_proof(rec)["valid"]:
        raise HTTPException(404, "No verified certificate with that ID")
    p = json.loads(rec["payload_json"])
    path = config.THUMB_DIR / f"card_{code}_{(rec['pdf_sha256'] or '')[:10]}.png"
    if not path.exists():
        b = db.get_batch(rec["batch_id"])
        logo = (b["config"].get("branding") or {}).get("logo", "") if b else ""
        logo_path = config.UPLOAD_DIR / Path(logo).name if logo else None
        path.write_bytes(sharecard.render_card(p, logo_path if logo_path and logo_path.exists() else None))
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})


@app.post("/verify", response_class=HTMLResponse)
async def verify_upload(file: UploadFile = File(...)):
    data = await file.read(config.MAX_UPLOAD_MB * 1024 * 1024 + 1)
    if len(data) > config.MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(413, "File too large")
    r = pipeline.verify_file(data)
    ok = r["status"] == "valid"
    reasons = "".join(f"<div class='row'><span>{html.escape(x)}</span><span></span></div>" for x in r["reasons"])
    extra = f"<div class='group'>{_proof_rows(r['record'], r['proof'])}</div>" if r.get("record") and r.get("proof") else ""
    name = ""
    if ok:
        p = r["proof"]["payload"]
        name = f"<p class='lead'>Issued to <b>{html.escape(p.get('name', ''))}</b><br>{html.escape(p.get('event', ''))}</p>"
    return page(r["title"], f"""<div class="icon {'ok' if ok else 'bad'}">{'✓' if ok else '✕'}</div><h1>{html.escape(r['title'])}</h1>
{name}<div class="group">{reasons}</div>{extra}{_upload_form()}{_footer()}""")


@app.get("/.well-known/sealsend-public-key.pem", response_class=PlainTextResponse)
def public_key():
    return crypto.public_pem()


def _claim_rec(token: str) -> dict:
    rec = db.one("SELECT * FROM records WHERE claim_token=? AND cert_file!=''", (token,)) if len(token) >= 16 else None
    if not rec:
        raise HTTPException(404, "This claim link is not valid")
    return rec


@app.get("/claim/{token}", response_class=HTMLResponse)
def claim_page(token: str):
    rec = _claim_rec(token)
    p = json.loads(rec["payload_json"] or "{}")
    ask = (f'<input type="text" name="student_id" placeholder="Your student ID" autocomplete="off" required>'
           if rec["student_id"] else "")
    return page("Claim your certificate", f"""<h1>Your certificate</h1>
<p class="lead">{html.escape(p.get('event', ''))}<br>Confirm it’s you to download it.</p>
<form class="group" style="padding:16px" method="post">{ask}<button class="btn">Download certificate</button></form>
<p class="note">Certificate {html.escape(rec['cert_code'])}. Anyone can verify it at /verify.</p>""")


@app.post("/claim/{token}")
def claim_download(token: str, student_id: str = Form("")):
    rec = _claim_rec(token)
    if rec["claim_attempts"] >= 5:
        return page("Locked", '<div class="icon bad">✕</div><h1>Link locked</h1><p class="lead">Too many wrong attempts. Ask the organizer for help.</p>')
    if rec["student_id"] and not hmac.compare_digest(student_id.strip().lower().encode(), rec["student_id"].strip().lower().encode()):
        db.update_record(rec["id"], claim_attempts=rec["claim_attempts"] + 1)
        db.log(rec["batch_id"], rec["id"], "warn", "claim", "Claim attempt with a wrong student ID")
        left = 4 - rec["claim_attempts"]
        return page("Try again", f'<div class="icon bad">✕</div><h1>That ID doesn’t match</h1>'
                                 f'<p class="lead">{left} attempt{"s" if left != 1 else ""} left.</p>'
                                 f'<a class="btn" href="/claim/{html.escape(token)}">Try again</a>')
    if rec["status"] == "Claim":
        db.update_record(rec["id"], status="Claimed", claimed_at=db.now())
    db.log(rec["batch_id"], rec["id"], "ok", "claim", "Certificate downloaded through the claim link")
    path = config.CERT_DIR / rec["cert_file"]
    return FileResponse(path, media_type="application/pdf", filename=path.name)


# ---------------------------------------------------------------- organizer UI (+ installable web app)
@app.get("/manifest.webmanifest")
def manifest():
    return JSONResponse({"name": "SendEZ", "short_name": "SendEZ", "start_url": "/", "display": "standalone",
                         "background_color": "#000000", "theme_color": "#000000",
                         "icons": [{"src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png"},
                                   {"src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png"}]},
                        media_type="application/manifest+json")


@app.get("/", response_class=HTMLResponse)
def index():
    return (STATIC / "index.html").read_text(encoding="utf-8")
