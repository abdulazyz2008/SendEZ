"""The core workflow and its proofs.

upload -> understand columns -> categories -> template per category -> generate + sign -> seal (Merkle) ->
verify every attachment (Mapping Lock + hash + signature) -> deliver with self-healing -> ledger
"""
import json
import multiprocessing
import os
import secrets
import shutil
import threading
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import certificate, config, copilot, crypto, db, keepawake, mailer, settings, sharecard
from . import templates_lib as T
from .data import (STYLES, classify, honor_phrase, with_article, fix_email_typo, guess_event_template, normalize_email, placeholders_in, read_table,
                   render, required_in, resolve_values, safe_filename, suggest_categories, suggest_config, valid_email,
                   validate)

_jobs_lock = threading.Lock()
_running: set[int] = set()
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
ISSUED = ("Sent", "Sending", "Claim", "Claimed")  # never silently rewritten once a student has it


def new_cert_code() -> str:
    return "SS-" + "".join(secrets.choice(CODE_ALPHABET) for _ in range(10))


# ---------------------------------------------------------------- configuration
def default_config(columns: list[str], rows: list[dict], event_name: str, filename: str = "", club: str = "") -> dict:
    from . import clubs
    club = club if club in clubs.CLUB_BY_ID else ""
    cfg = suggest_config(columns, rows)
    if "EVENT" not in cfg["mapping"]:
        cfg["mapping"]["EVENT"] = {"type": "text", "value": event_name or Path(filename).stem.replace("_", " ")}
    base = clubs.CLUB_BY_ID[club]["template"] if club else guess_event_template(" ".join([event_name or "", filename or ""]
                                         + [r.get(cfg["mapping"]["EVENT"].get("value", ""), "") for r in rows[:3]
                                            if cfg["mapping"]["EVENT"]["type"] == "column"]))
    name_map = {"NAME": cfg["mapping"]["NAME"]} if cfg["mapping"].get("NAME") else {}
    names = [resolve_values(r, name_map).get("NAME", "") for r in rows]
    caps = sum(1 for n in names if len(n) > 3 and (n.isupper() or n.islower()))
    org = settings.org()
    cfg.update({
        "template_id": base,
        "qr": True,
        "email_subject": mailer.DEFAULT_SUBJECT,
        "email_body": mailer.DEFAULT_BODY,
        "filename_pattern": "{{ID}}_{{NAME}}_{{EVENT}}",
        "rules": suggest_categories(cfg["category_column"], rows, base) if cfg.get("category_column") else [],
        "auto_fix_emails": True,
        "claim_links": True,
        "fix_caps": caps > len(rows) * 0.3,
        "branding": clubs.branding_for(club) if club else {k: org.get(k, "") for k in settings.DEFAULT["org"]},
        "club": club,
        "feedback": True,  # ask students to rate the event (1-5 stars) in the certificate email
    })
    return cfg


REUSED_KEYS = ("template_id", "qr", "email_subject", "email_body", "filename_pattern", "auto_fix_emails", "claim_links", "fix_caps",
               "feedback")
RULE_KEYS = ("template", "style", "title", "email", "description", "email_subject", "email_body")


def reuse_settings(cfg: dict, old: dict, columns: list[str], club: str = "") -> dict:
    """Start a new event with an earlier event's choices: designs, titles and emails per category,
    email text, branding and signatories, clean-up switches and column choices that still apply."""
    from . import clubs
    old = normalize_config(json.loads(json.dumps(old)))
    for k in REUSED_KEYS:
        if k in old:
            cfg[k] = old[k]
    if not T.get_template(cfg["template_id"]):  # a custom design deleted since
        cfg["template_id"] = certificate.DEFAULT_TEMPLATE_ID
    for key in ("email_column", "fallback_column"):
        if old.get(key) and old[key] in columns:
            cfg[key] = old[key]
    for ph, src in (old.get("mapping") or {}).items():
        if ph == "EVENT" or not isinstance(src, dict):
            continue  # the event name always comes from the new event
        kind = src.get("type")
        used = src.get("value") if kind == "combine" else [src.get("value")] if kind == "column" else []
        if (kind in ("column", "combine") and used and all(c in columns for c in used)) or kind == "text":
            cfg["mapping"][ph] = src
    if old.get("category_column"):  # same category names get the same design, title and email
        prev = {r["label"].strip().lower(): r for r in old.get("rules", [])}
        for r in cfg.get("rules", []):
            match = prev.get(r["label"].strip().lower())
            if match:
                r.update({k: match[k] for k in RULE_KEYS if k in match})
                if not T.get_template(r["template"]):
                    r["template"] = cfg["template_id"]
    elif old.get("rules"):  # plain-English rules: keep those whose column exists here
        kept = [r for r in old["rules"] if (r.get("match") or {}).get("column") in columns]
        if kept:
            cfg["category_column"], cfg["rules"] = "", kept
    club = club or old.get("club", "")
    cfg["club"] = club if club in clubs.CLUB_BY_ID else ""
    branding = {**{k: "" for k in settings.DEFAULT["org"]}, **(old.get("branding") or {})}
    if cfg["club"]:  # fresh club logo in case it was uploaded since
        cb = clubs.branding_for(cfg["club"])
        branding.update(org_name=cb["org_name"], logo=cb["logo"])
    cfg["branding"] = branding
    return cfg


def normalize_config(cfg: dict) -> dict:
    """Upgrade configs saved by earlier versions (single theme dict) to template ids."""
    if "template_id" not in cfg:
        old = cfg.get("template") or {}
        cfg["template_id"] = T.LEGACY.get(old.get("theme", ""), certificate.DEFAULT_TEMPLATE_ID)
        cfg["qr"] = old.get("qr", True)
    cfg.setdefault("branding", {k: "" for k in settings.DEFAULT["org"]})
    cfg.setdefault("rules", [])
    cfg.setdefault("category_column", "")
    for r in cfg["rules"]:
        if "template" not in r:
            r["template"] = T.LEGACY.get(r.get("theme", ""), r.get("theme", "")) or cfg["template_id"]
    return cfg


def used_templates(cfg: dict) -> list[str]:
    ids = [cfg["template_id"]] + [r.get("template") for r in cfg.get("rules", []) if r.get("template")]
    return list(dict.fromkeys(ids))


def all_placeholders(cfg: dict) -> dict:
    cfg = normalize_config(cfg)
    cert_texts = []
    for tid in used_templates(cfg):
        cert_texts += T.template_text_fields(certificate.resolve(tid))
    descs = [r.get("description") or STYLES.get(r.get("style", ""), STYLES["participation"])["description"]
             for r in cfg.get("rules", [])] or [STYLES["participation"]["description"]]
    email_texts = [cfg.get("email_subject", ""), cfg.get("email_body", "")]
    for r in cfg.get("rules", []):
        p = mailer.PRESETS.get(r.get("email"), {})
        email_texts += [r.get("email_subject", ""), r.get("email_body", ""), p.get("subject", ""), p.get("body", "")]
    pattern = cfg.get("filename_pattern", "")
    ordered = []
    for p in ["NAME", "ID", "EVENT"] + placeholders_in(*cert_texts, *descs) + placeholders_in(*email_texts) + placeholders_in(pattern):
        if p not in ordered:
            ordered.append(p)
    return {"all": ordered, "certificate": placeholders_in(*cert_texts, *descs), "email": placeholders_in(*email_texts),
            "required": required_in(*cert_texts, *descs)}


def create_batch(src: Path, original_name: str, batch_name: str, cfg_overrides: dict | None = None,
                 chaos: dict | None = None, sheet: str | None = None, club: str = "", reuse_from: int | None = None) -> int:
    table = read_table(src, sheet)
    cfg = default_config(table["columns"], table["rows"], batch_name, original_name, club)
    reused = db.get_batch(reuse_from) if reuse_from else None
    if reused:
        cfg = reuse_settings(cfg, reused["config"], table["columns"], club)
    cfg.update({"source_file": src.name, "sheets": table["sheets"], "header_row": table["header_row"]})
    cfg.update(cfg_overrides or {})
    with db.tx() as c:
        cur = c.execute(
            "INSERT INTO batches (name, filename, created_at, columns_json, config_json, is_chaos, chaos_json, sheet) VALUES (?,?,?,?,?,?,?,?)",
            (batch_name or Path(original_name).stem, original_name, db.now(), json.dumps(table["columns"]), json.dumps(cfg),
             1 if chaos else 0, json.dumps(chaos) if chaos else "", table["sheet"]))
        batch_id = cur.lastrowid
        _insert_rows(c, batch_id, table)
    where = f" (sheet “{table['sheet']}”, header on row {table['header_row']})" if table["sheet"] else f" (header on row {table['header_row']})"
    db.log(batch_id, None, "info", "upload", f"Read {original_name}{where}: {len(table['rows'])} rows, {len(table['columns'])} columns")
    if reused:
        db.log(batch_id, None, "info", "reuse", f"Designs, emails and certificate details copied from “{reused['name']}”")
    log_validation(batch_id, revalidate(batch_id))
    return batch_id


def _insert_rows(c, batch_id: int, table: dict) -> None:
    for row, row_no in zip(table["rows"], table["row_numbers"]):
        c.execute("INSERT INTO records (batch_id, row_no, data_json, cert_code, updated_at) VALUES (?,?,?,?,?)",
                  (batch_id, row_no, json.dumps(row), new_cert_code(), db.now()))


def reload_sheet(batch_id: int, sheet: str) -> None:
    """Switch to another sheet of the same workbook (only before anything is issued)."""
    b = db.get_batch(batch_id)
    if db.one(f"SELECT COUNT(*) n FROM records WHERE batch_id=? AND status IN ({','.join('?' * len(ISSUED))})",
              (batch_id, *ISSUED))["n"]:
        raise ValueError("Certificates were already issued from this batch; upload the file again as a new batch")
    src = config.UPLOAD_DIR / b["config"]["source_file"]
    table = read_table(src, sheet)
    cfg = default_config(table["columns"], table["rows"], b["name"], b["filename"], b["config"].get("club", ""))
    cfg.update({"source_file": src.name, "sheets": table["sheets"], "header_row": table["header_row"]})
    shutil.rmtree(config.CERT_DIR / f"batch_{batch_id}", ignore_errors=True)
    with db.tx() as c:
        c.execute("DELETE FROM records WHERE batch_id=?", (batch_id,))
        c.execute("UPDATE batches SET columns_json=?, config_json=?, sheet=?, seal_json='' WHERE id=?",
                  (json.dumps(table["columns"]), json.dumps(cfg), table["sheet"], batch_id))
        _insert_rows(c, batch_id, table)
    db.log(batch_id, None, "info", "upload", f"Switched to sheet “{table['sheet']}”: {len(table['rows'])} rows")
    log_validation(batch_id, revalidate(batch_id))


def log_validation(batch_id: int, counts: dict) -> None:
    claims = db.one("SELECT COUNT(*) n FROM records WHERE batch_id=? AND delivery='claim' AND status='Pending'", (batch_id,))["n"]
    fixed = db.one("SELECT COUNT(*) n FROM records WHERE batch_id=? AND email_fix!='' AND status='Pending'", (batch_id,))["n"]
    msg = f"Checked: {counts.get('Pending', 0)} ready, {counts.get('Invalid', 0)} need attention"
    if fixed:
        msg += f", {fixed} emails auto-fixed"
    if claims:
        msg += f", {claims} will get a claim link"
    db.log(batch_id, None, "warn" if counts.get("Invalid") else "ok", "validate", msg)


# ---------------------------------------------------------------- per-record values
def record_rule(rec: dict, cfg: dict) -> dict | None:
    return copilot.evaluate(cfg.get("rules", []), db.record_data(rec))


def record_values(rec: dict, cfg: dict) -> dict:
    cfg = normalize_config(cfg)
    row = db.record_data(rec)
    values = resolve_values(row, cfg.get("mapping", {}), cfg.get("fix_caps", False))
    rule = copilot.evaluate(cfg.get("rules", []), row)
    cat_col = cfg.get("category_column", "")
    category = (row.get(cat_col, "") if cat_col else "") or values.get("ACHIEVEMENT", "") or values.get("CERT_TYPE", "")
    values["CATEGORY"] = category.strip()
    style = (rule or {}).get("style") or classify(values["CATEGORY"] or values.get("ACHIEVEMENT", ""))
    honor = values.get("ACHIEVEMENT", "").strip() or values["CATEGORY"]
    if style == "winner" and not honor:
        style = "participation"
    tpl = certificate.resolve(rec["theme"] or (rule or {}).get("template") or cfg["template_id"])
    values["TITLE"] = certificate_title(rule, values, style, tpl)
    b = cfg.get("branding", {})
    values.update({"ORG_NAME": b.get("org_name", ""), "SIGNER1_NAME": b.get("signer1_name", ""),
                   "SIGNER1_TITLE": b.get("signer1_title", ""), "SIGNER2_NAME": b.get("signer2_name", ""),
                   "SIGNER2_TITLE": b.get("signer2_title", "")})
    values["CERT_ID"] = rec["cert_code"]
    values["VERIFY_URL"] = f"{config.public_base_url()}/verify/{rec['cert_code']}"
    values["ISSUE_DATE"] = date.today().strftime("%d %B %Y")
    values["ISSUER"] = b.get("org_name") or config.ISSUER_NAME
    values["LINKEDIN_URL"] = sharecard.linkedin_url(values["TITLE"], values.get("EVENT", ""), values["ISSUER"],
                                                    values["VERIFY_URL"], rec["cert_code"], sharecard.issued_on(rec))
    values["HONOR_PHRASE"] = honor_phrase(honor) if honor else "outstanding achievement"
    values["CATEGORY_A"] = with_article(values["CATEGORY"])
    desc = (rule or {}).get("description") or STYLES[style]["description"]
    values["DESCRIPTION"] = render(desc, values)
    return values


def certificate_title(rule: dict | None, values: dict, style: str, tpl: dict) -> str:
    if rule and rule.get("title"):
        return rule["title"]
    ctype = values.get("CERT_TYPE", "").strip()
    if ctype:
        return ctype if "certificate" in ctype.lower() else f"Certificate of {ctype}"
    if values.get("CATEGORY"):
        return STYLES[style]["title"]
    return tpl.get("title", "") or "Certificate of Achievement"


def assets_for(cfg: dict) -> dict:
    b = cfg.get("branding", {})
    return {"logo": b.get("logo", ""), "signer1_sig": b.get("signer1_sig", ""), "signer2_sig": b.get("signer2_sig", "")}


def feedback_link(rec: dict) -> str:
    """The student's private "rate this event" link (the token is created the first time it's needed)."""
    token = rec.get("feedback_token") or ""
    if not token:
        token = secrets.token_urlsafe(12)
        db.update_record(rec["id"], feedback_token=token)
        rec["feedback_token"] = token
    return f"{config.public_base_url()}/feedback/{token}"


def feedback_question(values: dict) -> str:
    return f"How was {values.get('EVENT') or 'the event'}? Tap a star to rate it."


def feedback_summary(batch_id: int) -> dict:
    rows = db.query("SELECT f.stars, f.comment, f.at, r.rule_label FROM feedback f JOIN records r ON r.id = f.record_id "
                    "WHERE f.batch_id=? ORDER BY f.at DESC", (batch_id,))
    dist = [sum(1 for r in rows if r["stars"] == n) for n in range(1, 6)]
    return {"count": len(rows), "average": round(sum(r["stars"] for r in rows) / len(rows), 2) if rows else None,
            "distribution": dist,
            "comments": [{"stars": r["stars"], "comment": r["comment"], "category": r["rule_label"], "at": r["at"]}
                         for r in rows if r["comment"].strip()][:100]}


def email_texts(rec: dict, cfg: dict, values: dict) -> tuple[str, str]:
    rule = record_rule(rec, cfg)
    subject, body = cfg["email_subject"], cfg["email_body"]
    if rule:
        if rule.get("email") == "custom" and rule.get("email_subject"):
            subject, body = rule["email_subject"], rule.get("email_body") or body
        elif rule.get("email") in mailer.PRESETS and rule["email"] != "standard":
            p = mailer.PRESETS[rule["email"]]
            subject, body = p["subject"], p["body"]
    return render(subject, values), render(body, values)


def cert_filename(rec: dict, cfg: dict, values: dict) -> str:
    stem = safe_filename(render(cfg.get("filename_pattern") or "{{ID}}_{{NAME}}_{{EVENT}}", values))
    if not values.get("ID"):
        stem = f"row{rec['row_no']:04d}_{stem}"
    return f"{stem}.pdf"


def expected_lock(rec: dict, cfg: dict, values: dict | None = None) -> str:
    values = values or record_values(rec, cfg)
    return crypto.mapping_lock(rec["student_id"], rec["email"], values.get("EVENT", ""), rec["cert_code"])


def _delete_cert(rec: dict) -> None:
    if rec.get("cert_file"):
        (config.CERT_DIR / rec["cert_file"]).unlink(missing_ok=True)


# ---------------------------------------------------------------- validation
def revalidate(batch_id: int) -> dict:
    batch = db.get_batch(batch_id)
    cfg = normalize_config(batch["config"])
    recs = db.query("SELECT * FROM records WHERE batch_id=? ORDER BY row_no", (batch_id,))
    rows = [db.record_data(r) for r in recs]
    results = validate(rows, cfg, all_placeholders(cfg)["required"], [r["row_no"] for r in recs])
    dropped_seal = False
    cat_col = cfg.get("category_column", "")
    for rec, row, res in zip(recs, rows, results):
        if rec["status"] in ISSUED:
            continue
        warnings = list(res["warnings"])
        if res["dedupe_key"] and not res["errors"]:
            prev = db.one("SELECT at, email FROM send_log WHERE dedupe_key=? AND result='sent' AND record_id!=? "
                          "ORDER BY at DESC LIMIT 1", (res["dedupe_key"], rec["id"]))
            if prev:
                warnings.append(f"Already sent to {prev['email']} on {prev['at'][:10]}; will be skipped unless resent")
        if rec["cert_file"]:
            _delete_cert(rec)  # template, mapping or data changed: the old PDF is stale
            dropped_seal = True
        rule = copilot.evaluate(cfg.get("rules", []), row) if not res["errors"] else None
        db.update_record(
            rec["id"], name=res["name"], student_id=res["student_id"], email=res["email"],
            email_source=res["email_source"], email_fix=res["email_fix"], delivery=res["delivery"],
            dedupe_key=res["dedupe_key"], errors="; ".join(res["errors"]), warnings="; ".join(warnings),
            theme=(rule or {}).get("template") or cfg["template_id"], rule_label=(rule or {}).get("label", ""),
            category=(row.get(cat_col, "") if cat_col else "").strip(),
            status="Invalid" if res["errors"] else "Pending",
            cert_file="", pdf_sha256="", mapping_lock="", payload_json="", signature="", leaf_hash="",
            last_error="", attempts=0,
        )
    if dropped_seal and batch["seal"]:
        db.set_batch(batch_id, seal_json="")
        db.log(batch_id, None, "info", "seal", "Seal cleared: certificates changed and will be re-sealed")
    return db.status_counts(batch_id)


# ---------------------------------------------------------------- generation + signing
PARALLEL_THRESHOLD = 40
CHUNK = 100


def _job(rec: dict, cfg: dict):
    values = record_values(rec, cfg)
    lock = expected_lock(rec, cfg, values)
    job = (rec["theme"] or cfg["template_id"], values, rec["cert_code"], lock, assets_for(cfg), cfg.get("qr", True),
           f"Certificate - {values.get('NAME', '')}")
    return job, values, lock


def _render_all(jobs: list[tuple]):
    """Yield PDF bytes or the exception, in order. Large batches use every CPU core."""
    done = 0
    if len(jobs) >= PARALLEL_THRESHOLD and config.WORKERS > 1:
        try:
            ctx = multiprocessing.get_context("spawn")  # the only start method on Windows; safe with threads
            with ProcessPoolExecutor(max_workers=config.WORKERS, mp_context=ctx) as pool:
                for start in range(0, len(jobs), CHUNK):
                    futures = [pool.submit(certificate.render_job, j) for j in jobs[start:start + CHUNK]]
                    for fut in futures:
                        try:
                            out = fut.result()
                        except BrokenProcessPool:
                            raise
                        except Exception as e:
                            out = e
                        done += 1
                        yield out
        except (BrokenProcessPool, OSError):
            pass  # worker processes unavailable: finish the remainder in-process
    for job in jobs[done:]:
        try:
            yield certificate.render_job(job)
        except Exception as e:
            yield e


def finalize(rec: dict, cfg: dict, pdf: bytes, values: dict, lock: str) -> dict:
    """Store the PDF, sign it and compute its Merkle leaf."""
    folder = config.CERT_DIR / f"batch_{rec['batch_id']}"
    folder.mkdir(parents=True, exist_ok=True)
    fname = cert_filename(rec, cfg, values)
    taken = db.one("SELECT id FROM records WHERE batch_id=? AND cert_file=? AND id!=?",
                   (rec["batch_id"], f"batch_{rec['batch_id']}/{fname}", rec["id"]))
    if taken:
        fname = f"{Path(fname).stem}_row{rec['row_no']}.pdf"
    (folder / fname).write_bytes(pdf)
    sha = certificate.sha256(pdf)
    payload = {
        "v": 2, "code": rec["cert_code"], "name": values.get("NAME", ""), "event": values.get("EVENT", ""),
        "title": values.get("TITLE", ""), "description": values.get("DESCRIPTION", ""),
        "cert_type": values.get("CERT_TYPE", ""), "achievement": values.get("ACHIEVEMENT", ""),
        "category": values.get("CATEGORY", ""), "date": values.get("DATE", ""), "issuer": values.get("ISSUER", ""),
        "batch": rec["batch_id"], "pdf_sha256": sha, "key": crypto.fingerprint(),
    }
    db.update_record(
        rec["id"], status="Generated" if rec["status"] not in ISSUED else rec["status"],
        cert_file=f"batch_{rec['batch_id']}/{fname}", pdf_sha256=sha, mapping_lock=lock, title=values.get("TITLE", ""),
        payload_json=json.dumps(payload, ensure_ascii=False), signature=crypto.sign(crypto.canonical(payload)),
        leaf_hash=crypto.leaf_hash(rec["cert_code"], sha, lock),
        claim_token=rec["claim_token"] or secrets.token_urlsafe(16), last_error="",
    )
    return db.get_record(rec["id"])


def regenerate(rec: dict, cfg: dict) -> dict:
    job, values, lock = _job(rec, cfg)
    return finalize(rec, cfg, certificate.render_job(job), values, lock)


def _generate_records(recs: list[dict], cfg: dict) -> tuple[int, int]:
    prepared = [_job(r, cfg) for r in recs]
    ok = failed = 0
    for rec, (job, values, lock), out in zip(recs, prepared, _render_all([p[0] for p in prepared])):
        try:
            if isinstance(out, Exception):
                raise out
            finalize(rec, cfg, out, values, lock)
            ok += 1
        except Exception as e:  # one bad record never stops the batch
            failed += 1
            db.update_record(rec["id"], status="Failed", last_error=f"Generation failed: {e}")
            db.log(rec["batch_id"], rec["id"], "error", "generate", f"Generation failed: {e}")
    return ok, failed


def generate(batch_id: int) -> None:
    cfg = normalize_config(db.get_batch(batch_id)["config"])
    recs = db.query("SELECT * FROM records WHERE batch_id=? AND status='Pending' ORDER BY row_no", (batch_id,))
    t = time.time()
    ok, failed = _generate_records(recs, cfg)
    db.log(batch_id, None, "ok" if not failed else "warn", "generate",
           f"Generated and signed {ok} certificates in {time.time() - t:.1f}s" + (f"; {failed} failed" if failed else ""))
    seal_batch(batch_id)


# ---------------------------------------------------------------- batch seal
def _sealed_records(batch_id: int) -> list[dict]:
    return db.query("SELECT * FROM records WHERE batch_id=? AND leaf_hash!='' ORDER BY row_no", (batch_id,))


def seal_batch(batch_id: int) -> dict | None:
    recs = _sealed_records(batch_id)
    if not recs:
        return None
    body = {"v": 1, "batch": batch_id, "root": crypto.merkle_root([r["leaf_hash"] for r in recs]),
            "count": len(recs), "sealed_at": db.now(), "key": crypto.fingerprint(),
            "algo": "SHA-256 Merkle tree, Ed25519 signature"}
    seal = {**body, "signature": crypto.sign(crypto.canonical(body))}
    db.set_batch(batch_id, seal_json=json.dumps(seal))
    db.log(batch_id, None, "ok", "seal", f"Batch sealed: {len(recs)} certificates, Merkle root {body['root'][:16]}…")
    return seal


def verify_batch(batch_id: int) -> dict:
    """Recompute everything from the files on disk and check it against the signed seal."""
    seal = db.get_batch(batch_id)["seal"]
    if not seal:
        return {"sealed": False, "intact": False, "problems": [], "checked": 0}
    body = {k: v for k, v in seal.items() if k != "signature"}
    sig_ok = crypto.verify(crypto.canonical(body), seal["signature"])
    problems, leaves = [], []
    for rec in _sealed_records(batch_id):
        path = config.CERT_DIR / rec["cert_file"]
        sha_now = certificate.sha256(path.read_bytes()) if path.exists() else "missing"
        leaves.append(crypto.leaf_hash(rec["cert_code"], sha_now, rec["mapping_lock"]))
        who = f"Row {rec['row_no']} · {rec['name']}"
        if sha_now == "missing":
            problems.append({"record_id": rec["id"], "who": who, "reason": "Certificate file is missing"})
        elif sha_now != rec["pdf_sha256"]:
            problems.append({"record_id": rec["id"], "who": who, "reason": "File changed after it was signed"})
        try:
            payload = json.loads(rec["payload_json"])
        except ValueError:
            payload = {}
        if not crypto.verify(crypto.canonical(payload), rec["signature"]) or payload.get("pdf_sha256") != rec["pdf_sha256"]:
            problems.append({"record_id": rec["id"], "who": who, "reason": "Signed record does not match"})
    root_now = crypto.merkle_root(leaves)
    root_ok = root_now == seal["root"] and len(leaves) == seal["count"]
    return {"sealed": True, "intact": sig_ok and root_ok and not problems, "signature_valid": sig_ok,
            "root_matches": root_ok, "sealed_root": seal["root"], "recomputed_root": root_now,
            "count": seal["count"], "checked": len(leaves), "problems": problems, "checked_at": db.now()}


def record_proof(rec: dict) -> dict:
    """Signature + Merkle inclusion status for one certificate (used by the public verify page)."""
    try:
        payload = json.loads(rec["payload_json"] or "{}")
    except ValueError:
        payload = {}
    sig_ok = bool(payload) and crypto.verify(crypto.canonical(payload), rec["signature"])
    consistent = sig_ok and payload.get("pdf_sha256") == rec["pdf_sha256"] and payload.get("code") == rec["cert_code"]
    batch = db.get_batch(rec["batch_id"])
    seal = batch["seal"] if batch else None
    out = {"payload": payload, "signature_valid": sig_ok, "record_consistent": consistent,
           "sealed": bool(seal), "in_seal": False, "seal_signature_valid": False, "proof": [], "seal": seal}
    if seal:
        body = {k: v for k, v in seal.items() if k != "signature"}
        out["seal_signature_valid"] = crypto.verify(crypto.canonical(body), seal["signature"])
        leaves = [r["leaf_hash"] for r in _sealed_records(rec["batch_id"])]
        if rec["leaf_hash"] in leaves:
            idx = leaves.index(rec["leaf_hash"])
            proof = crypto.merkle_proof(leaves, idx)
            out["proof"] = proof
            out["in_seal"] = crypto.verify_proof(rec["leaf_hash"], proof, seal["root"])
            out["leaf_index"], out["leaf_count"] = idx, len(leaves)
    out["valid"] = consistent and out["in_seal"] and out["seal_signature_valid"]
    return out


# ---------------------------------------------------------------- send-time checks
def check_attachment(rec: dict, data: bytes, cfg: dict) -> list[str]:
    """Every reason this file must NOT be attached to this student's email (empty list = safe)."""
    problems = []
    meta = certificate.read_proof(data)
    if meta["code"] and meta["code"] != rec["cert_code"]:
        problems.append(f"Attachment belongs to certificate {meta['code']}, not {rec['cert_code']}")
    if meta["lock"] != expected_lock(rec, cfg):
        problems.append("Mapping Lock mismatch: student, email and event do not match this file")
    if certificate.sha256(data) != rec["pdf_sha256"]:
        problems.append("File differs from the signed original")
    try:
        payload = json.loads(rec["payload_json"] or "{}")
    except ValueError:
        payload = {}
    if not crypto.verify(crypto.canonical(payload), rec["signature"]) or payload.get("pdf_sha256") != rec["pdf_sha256"]:
        problems.append("Signature check failed")
    return problems


def verified_attachment(rec: dict, cfg: dict) -> tuple[dict, bytes, bool]:
    """Return (record, pdf bytes, healed). Blocks wrong files; self-heals by regenerating once."""
    path = config.CERT_DIR / rec["cert_file"] if rec["cert_file"] else None
    data = path.read_bytes() if path and path.exists() else b""
    problems = check_attachment(rec, data, cfg) if data else ["Certificate file is missing"]
    if not problems:
        return rec, data, False
    db.log(rec["batch_id"], rec["id"], "block", "lock", "Blocked before sending: " + "; ".join(problems))
    rec = regenerate(rec, cfg)
    data = (config.CERT_DIR / rec["cert_file"]).read_bytes()
    problems = check_attachment(rec, data, cfg)
    if problems:
        raise RuntimeError("Still failing after regeneration: " + "; ".join(problems))
    db.log(rec["batch_id"], rec["id"], "heal", "lock", "Self-healed: regenerated from source data, re-signed, verified")
    return rec, data, True


def alternate_email(rec: dict, cfg: dict) -> tuple[str, str]:
    row = db.record_data(rec)
    for col in (cfg.get("email_column"), cfg.get("fallback_column")):
        if not col:
            continue
        raw = normalize_email(row.get(col, ""))
        cand = raw if valid_email(raw) else ""
        if not cand and cfg.get("auto_fix_emails", True):
            cand = fix_email_typo(raw, Counter())[0]
        if cand and cand.lower() != rec["email"].lower():
            return cand, col
    return "", ""


def _log_send(rec: dict, attachment: str, result: str, detail: str, account: str = "") -> None:
    with db.tx() as c:
        c.execute("INSERT INTO send_log (record_id, dedupe_key, email, attachment, result, detail, at, account) VALUES (?,?,?,?,?,?,?,?)",
                  (rec["id"], rec["dedupe_key"], rec["email"], attachment, result, detail, db.now(), account))


def sent_last_24h(account: dict | None = None) -> int:
    """Real (non-sandbox) emails accepted in the last 24 hours, across all batches; for one account if given.
    Rows from before accounts were recorded count for the main account."""
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
    sql, params = "SELECT COUNT(*) n FROM send_log WHERE result='sent' AND at>=? AND detail NOT LIKE 'Sandbox%'", [since]
    if account is not None:
        addr = (account.get("address") or "").lower()
        sql += " AND account=?" if account.get("slot") == "mail2" else " AND account IN ('', ?)"
        params.append(addr)
    return db.one(sql, params)["n"]


def quota_status() -> dict:
    mode = config.mail_mode()
    accts = []
    for a in settings.accounts():
        used = sent_last_24h(a)
        accts.append({"address": a.get("address", ""), "provider": a.get("provider", ""), "slot": a["slot"],
                      "cap": a["daily_cap"], "used": used, "left": max(a["daily_cap"] - used, 0)})
    cap = sum(a["cap"] for a in accts)
    return {"mode": mode, "cap": cap, "used": sum(a["used"] for a in accts),
            "left": sum(a["left"] for a in accts) if cap else None, "accounts": accts}


def _ts(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_ts(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def quota_reset_at() -> str:
    """When the whole daily allowance is free again: 24 hours after the newest counted email (+2 min)."""
    row = db.one("SELECT MAX(at) at FROM send_log WHERE result='sent' AND detail NOT LIKE 'Sandbox%'")
    now = datetime.now(timezone.utc)
    base = _parse_ts(row["at"]) if row and row["at"] else now
    return _ts(max(base + timedelta(hours=24, minutes=2), now + timedelta(minutes=2)))


def preflight(batch: dict, check_quota: bool = True) -> tuple[str, str]:
    """('', '') when a send can start, else (kind, message) with kind 'quota' or 'connection'."""
    if batch["is_chaos"] or config.mail_mode() == "sandbox":
        return "", ""
    q = quota_status()
    if check_quota and q["cap"] and q["left"] == 0:
        return "quota", f"The daily sending limit ({q['cap']}) is used up. Try again later or raise it in Settings."
    res = mailer.check_connection()
    if not res["ok"]:
        return "connection", f"Can't send yet: {res['message']}"
    extra = settings.mail2()
    if extra and config.mail_mode() == "smtp":
        res = mailer.check_connection(extra)
        if not res["ok"]:
            return "connection", f"Can't send yet: second account {extra['address']}: {res['message']}"
    return "", ""


class _Pause(Exception):
    pass


def send(batch_id: int, record_ids: list[int] | None = None, allow_resend: bool = False) -> None:
    batch = db.get_batch(batch_id)
    cfg = normalize_config(batch["config"])
    chaos = batch["chaos"] or {}
    db.set_batch(batch_id, pause_reason="", send_at="", send_kind="")
    statuses = ["Pending", "Generated", "Failed"] + (["Sent", "Skipped", "Claim", "Claimed"] if allow_resend else [])
    sql = f"SELECT * FROM records WHERE batch_id=? AND status IN ({','.join('?' * len(statuses))})"
    params: list = [batch_id, *statuses]
    if record_ids is not None:
        sql += f" AND id IN ({','.join('?' * len(record_ids))})"
        params += record_ids
    targets = db.query(sql + " ORDER BY row_no", params)

    missing = [r for r in targets if not r["cert_file"] or not (config.CERT_DIR / r["cert_file"]).exists()]
    if missing:
        ok, _ = _generate_records(missing, cfg)
        db.log(batch_id, None, "ok", "generate", f"Generated and signed {ok} certificates before sending")
    if missing or not db.get_batch(batch_id)["seal"]:
        seal_batch(batch_id)
    mode = "sandbox" if batch["is_chaos"] else config.mail_mode()
    real = mode != "sandbox"
    accounts = settings.accounts() if real else []
    cap = sum(a["daily_cap"] for a in accounts)
    auto_resume = real and settings.mail().get("auto_resume", True) is not False
    delay = float(settings.mail().get("delay_seconds") or 0) if real else 0.0
    delay = max(delay, config.SEND_DELAY_SECONDS) if real else 0.0
    db.log(batch_id, None, "info", "send", f"Delivery started for {len(targets)} students via {mode}"
           + (" (chaos faults armed)" if chaos else "") + (f"; daily limit {cap}, {sent_last_24h()} used" if cap else "")
           + (f"; accounts: {', '.join(a['address'] for a in accounts)}" if len(accounts) > 1 else ""))

    exhausted: set[str] = set()
    senders: dict[str, mailer.Sender] = {}

    def pick_account():
        """The first account with room left today (the main one fills up first)."""
        for a in accounts:
            if a["slot"] not in exhausted and (not a["daily_cap"] or sent_last_24h(a) < a["daily_cap"]):
                return a
        return None

    def sender_for(acct):
        key = acct["slot"] if acct else "default"
        if key not in senders:
            senders[key] = mailer.Sender(mode=mode, faults=chaos.get("faults"), account=acct)
        return senders[key]

    reseal = False
    sent = failed = skipped = claim = 0
    try:
        if True:
            for rec in targets:
                rec = db.get_record(rec["id"])
                if rec["status"] == "Failed" and rec["last_error"].startswith("Generation failed"):
                    try:
                        rec = regenerate(rec, cfg)
                        reseal = True
                    except Exception as e:
                        db.update_record(rec["id"], last_error=f"Generation failed: {e}")
                        failed += 1
                        continue
                if not allow_resend:
                    prev = db.one("SELECT at, email FROM send_log WHERE dedupe_key=? AND result='sent' LIMIT 1",
                                  (rec["dedupe_key"],))
                    if prev:
                        db.update_record(rec["id"], status="Skipped",
                                         last_error=f"Already sent to {prev['email']} on {prev['at'][:10]}")
                        db.log(batch_id, rec["id"], "warn", "dedupe", f"Duplicate prevented: already sent to {prev['email']}")
                        skipped += 1
                        continue
                if rec["delivery"] == "claim":
                    db.update_record(rec["id"], status="Claim", last_error="")
                    db.log(batch_id, rec["id"], "heal", "claim", "No usable email: claim link issued instead of failing")
                    claim += 1
                    continue
                acct = pick_account() if real else None
                if real and acct is None:
                    raise _Pause(f"Daily sending limit reached ({cap} emails in 24 hours). The rest are queued"
                                 + ("; SendEZ continues automatically when the limit resets (keep it open)." if auto_resume else
                                    ": press Send again after the limit resets, or raise the limit in Settings if your account allows it."))
                try:
                    rec, pdf, healed = verified_attachment(rec, cfg)
                    reseal |= healed
                except Exception as e:
                    db.update_record(rec["id"], status="Failed", last_error=str(e), attempts=rec["attempts"] + 1)
                    db.log(batch_id, rec["id"], "error", "lock", str(e))
                    _log_send(rec, rec["cert_file"], "failed", str(e))
                    failed += 1
                    continue
                while True:
                    try:
                        result = _deliver(sender_for(acct), rec, cfg, pdf)
                        break
                    except mailer.QuotaFailure:
                        if acct is not None:  # this account hit its provider limit early: use the next one
                            exhausted.add(acct["slot"])
                            nxt = pick_account()
                            if nxt is not None:
                                db.log(batch_id, None, "warn", "send", f"{acct['address']} reached its sending limit; "
                                                                        f"continuing from {nxt['address']}")
                                acct, rec = nxt, db.get_record(rec["id"])
                                continue
                        raise
                    except mailer.AuthFailure as e:
                        if acct is not None and acct["slot"] == "mail2":
                            raise mailer.AuthFailure(f"Second account {acct['address']}: {e}") from e
                        raise
                reseal |= result["rebound"]
                if result["ok"]:
                    sent += 1
                else:
                    failed += 1
                if delay:
                    time.sleep(delay)
    except (mailer.AuthFailure, mailer.QuotaFailure, _Pause) as e:
        kind = {"AuthFailure": "Email login failed", "QuotaFailure": "Sending limit reached"}.get(type(e).__name__, "Paused")
        resume = ""
        if auto_resume and not isinstance(e, mailer.AuthFailure):  # a wrong password needs a person; a limit just needs time
            resume = quota_reset_at() if isinstance(e, _Pause) else _ts(datetime.now(timezone.utc) + timedelta(hours=24, minutes=2))
            db.log(batch_id, None, "info", "schedule", f"Will continue automatically at {resume[:16].replace('T', ' ')} UTC")
        db.set_batch(batch_id, pause_reason=f"{kind}: {e}", send_at=resume, send_kind="limit" if resume else "")
        with db.tx() as c:
            c.execute("UPDATE records SET status='Generated' WHERE batch_id=? AND status='Sending'", (batch_id,))
        db.log(batch_id, None, "error" if isinstance(e, mailer.AuthFailure) else "warn", "send",
               f"Delivery paused. {kind}: {e}. Nothing was lost; unsent students stay queued.")
    finally:
        for snd in senders.values():
            snd.close()
        if reseal:
            seal_batch(batch_id)
    db.log(batch_id, None, "ok" if not failed else "warn", "send",
           f"Delivery finished: {sent} sent, {claim} claim links, {skipped} duplicates skipped, {failed} failed")


def _deliver(sender: mailer.Sender, rec: dict, cfg: dict, pdf: bytes) -> dict:
    batch_id = rec["batch_id"]
    acct_key = ((sender.account or {}).get("address") or "").lower()  # which account's daily count this uses
    rebound, attempts = False, rec["attempts"]
    db.update_record(rec["id"], status="Sending")
    attempt = 0
    while attempt < config.MAX_SEND_ATTEMPTS:
        values = record_values(rec, cfg)
        subject, body = email_texts(rec, cfg, values)
        fname = Path(rec["cert_file"]).name
        feedback = (feedback_question(values), feedback_link(rec)) if cfg.get("feedback") else None
        msg = mailer.build_message(rec["email"], rec["name"], subject, body, pdf, fname, rec["cert_code"],
                                   cfg.get("branding", {}).get("org_name", ""), account=sender.account, feedback=feedback)
        attempt += 1
        attempts += 1
        try:
            detail = sender.send(msg, rec["email"], f"{rec['cert_code']}_{Path(fname).stem}")
            db.update_record(rec["id"], status="Sent", sent_at=db.now(), attempts=attempts, last_error="")
            _log_send(rec, fname, "sent", detail, account=acct_key)
            db.log(batch_id, rec["id"], "ok", "send", f"Sent to {rec['email']} with {fname}"
                   + (f" (attempt {attempt})" if attempt > 1 else ""))
            return {"ok": True, "rebound": rebound}
        except (mailer.AuthFailure, mailer.QuotaFailure):
            db.update_record(rec["id"], status="Generated", attempts=attempts)
            raise
        except mailer.PermanentFailure as e:
            _log_send(rec, fname, "failed", str(e), account=acct_key)
            alt, col = alternate_email(rec, cfg) if not rebound else ("", "")
            if alt:
                db.log(batch_id, rec["id"], "heal", "bounce", f"{rec['email']} bounced ({e}); switching to {col}: {alt}")
                db.update_record(rec["id"], email=alt, email_source=col)
                rec = regenerate(db.get_record(rec["id"]), cfg)  # Mapping Lock is re-bound to the new address
                rec, pdf, _ = verified_attachment(rec, cfg)
                rebound, attempt = True, 0
                continue
            db.update_record(rec["id"], status="Failed", attempts=attempts,
                             last_error=f"{e}. Share the claim link instead.")
            db.log(batch_id, rec["id"], "error", "bounce", f"Permanent failure, no alternative address: {e}")
            return {"ok": False, "rebound": rebound}
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
            db.log(batch_id, rec["id"], "warn", "retry", f"Attempt {attempt} failed ({last})"
                   + ("; retrying" if attempt < config.MAX_SEND_ATTEMPTS else ""))
            sender.close()
            if attempt < config.MAX_SEND_ATTEMPTS:
                time.sleep(min(config.RETRY_BASE_SECONDS * 2 ** (attempt - 1), 8))
            else:
                db.update_record(rec["id"], status="Failed", attempts=attempts, last_error=last)
                _log_send(rec, fname, "failed", last, account=acct_key)
                return {"ok": False, "rebound": rebound}
    return {"ok": False, "rebound": rebound}


# ---------------------------------------------------------------- scheduled and automatic sends
_sched_lock = threading.Lock()
_sched_stop: threading.Event | None = None
MISSED_AFTER = timedelta(hours=12)


def scheduler_tick(now: datetime | None = None) -> None:
    """Start every send whose time has come: scheduled sends, and batches waiting for the daily limit to reset."""
    now = now or datetime.now(timezone.utc)
    with _sched_lock:
        due = db.query("SELECT id FROM batches WHERE send_at!='' AND send_at<=?", (_ts(now),))
        for row in due:
            b = db.get_batch(row["id"])
            if is_running(b["id"]):
                continue
            kind, when = b["send_kind"], b["send_at"]
            db.set_batch(b["id"], send_at="", send_kind="")
            if kind == "schedule" and now - _parse_ts(when) > MISSED_AFTER:
                db.set_batch(b["id"], pause_reason="Scheduled send missed: SendEZ was closed at the chosen time. Press Send to send now.")
                db.log(b["id"], None, "warn", "schedule", "Scheduled send missed because SendEZ was closed; waiting for you")
                continue
            problem, message = preflight(b)
            if problem == "quota" and kind == "limit":  # other events used the allowance meanwhile: wait again
                db.set_batch(b["id"], send_at=quota_reset_at(), send_kind="limit")
                continue
            if problem:
                db.set_batch(b["id"], pause_reason=f"{'Scheduled send' if kind == 'schedule' else 'Automatic continue'} couldn't start: {message}")
                db.log(b["id"], None, "error", "schedule", f"Couldn't start automatically: {message}")
                continue
            db.log(b["id"], None, "info", "schedule",
                   "Scheduled send started" if kind == "schedule" else "Daily limit reset: continuing automatically")
            run_job(b["id"], "sending", send)


def _wants_awake() -> bool:
    return bool(db.one("SELECT COUNT(*) n FROM batches WHERE job!='' OR send_at!=''")["n"])


def start_scheduler(interval: float | None = None) -> None:
    global _sched_stop
    stop = threading.Event()
    _sched_stop = stop
    every = interval if interval is not None else config.SCHEDULER_SECONDS

    def loop():
        wait = min(3.0, every)  # catch anything that came due while the app was closed
        while not stop.wait(wait):
            wait = every
            try:
                scheduler_tick()
                keepawake.set_awake(_wants_awake())
            except Exception:
                pass  # never let the timer die; the next tick tries again
        keepawake.set_awake(False)

    threading.Thread(target=loop, name="sendez-scheduler", daemon=True).start()


def stop_scheduler() -> None:
    if _sched_stop is not None:
        _sched_stop.set()


# ---------------------------------------------------------------- jobs
def run_job(batch_id: int, kind: str, fn, *args, **kwargs) -> bool:
    with _jobs_lock:
        if batch_id in _running:
            return False
        _running.add(batch_id)
    db.set_batch(batch_id, job=kind)

    def worker():
        try:
            fn(batch_id, *args, **kwargs)
        except Exception as e:
            db.log(batch_id, None, "error", kind, f"Job stopped unexpectedly: {type(e).__name__}: {e}")
        finally:
            db.set_batch(batch_id, job="")
            with _jobs_lock:
                _running.discard(batch_id)

    threading.Thread(target=worker, daemon=True).start()
    return True


def is_running(batch_id: int) -> bool:
    with _jobs_lock:
        return batch_id in _running


def delete_batch(batch_id: int) -> None:
    shutil.rmtree(config.CERT_DIR / f"batch_{batch_id}", ignore_errors=True)
    with db.tx() as c:
        c.execute("DELETE FROM events WHERE batch_id=?", (batch_id,))
        c.execute("DELETE FROM records WHERE batch_id=?", (batch_id,))
        c.execute("DELETE FROM batches WHERE id=?", (batch_id,))


# ---------------------------------------------------------------- public verification of a file
def verify_file(data: bytes) -> dict:
    """What the public verifier says about an uploaded PDF."""
    meta = certificate.read_proof(data)
    if not meta["code"]:
        return {"status": "unknown", "title": "Not a SendEZ certificate",
                "reasons": ["This PDF carries no SendEZ certificate ID."], "record": None}
    rec = db.one("SELECT * FROM records WHERE cert_code=?", (meta["code"],))
    if not rec or not rec["payload_json"]:
        return {"status": "not_found", "title": "No such certificate",
                "reasons": [f"Certificate {meta['code']} was never issued by this issuer."], "record": None}
    proof = record_proof(rec)
    signed = proof["payload"]
    if certificate.sha256(data) != rec["pdf_sha256"]:
        return {"status": "modified", "title": "This file has been altered",
                "reasons": ["Its fingerprint does not match the signed original.",
                            f"The genuine certificate {rec['cert_code']} was issued to {signed.get('name', '?')}."],
                "record": rec, "proof": proof}
    if not proof["valid"]:
        return {"status": "invalid", "title": "Signature checks failed",
                "reasons": ["The issuer's records for this certificate fail verification."], "record": rec, "proof": proof}
    return {"status": "valid", "title": "Authentic certificate",
            "reasons": ["Byte-for-byte identical to the signed original."], "record": rec, "proof": proof}
