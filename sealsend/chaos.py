"""Chaos Test: inject real faults into a real batch, run the real pipeline, then prove what happened.

Nothing here is mocked except the mail provider's behaviour (sandbox only, never real email).
Every verdict in the report is computed from the database, the event ledger and by opening
the delivered .eml files, not from what the pipeline claims.
"""
import csv
import email
import json
from email import policy
from pathlib import Path

from . import certificate, config, copilot, crypto, db, pipeline

EVENT = "Chaos Test Hackathon"
DATE = "04 November 2026"


def _rows(run: int) -> tuple[list[dict], dict, dict]:
    sid = lambda i: f"CT{run:03d}-{i:02d}"  # noqa: E731 - unique per run so earlier runs never dedupe these
    dom = "college.example.edu"
    R = []
    def add(key, name, i, email_, personal, ach, ctype="Achievement"):
        R.append((key, {"Student Name": name, "Student ID": sid(i), "Student Email": email_, "Personal Email": personal,
                        "Event": EVENT, "Certificate Type": ctype, "Achievement": ach, "Date": DATE}))
    add("swap_a", "Aisha Rahman", 1, f"aisha.rahman@{dom}", "", "First Place")
    add("swap_b", "Rohan Mehta", 2, f"rohan.mehta@{dom}", "", "Second Place")
    add("tamper", "Fatima Al-Sayed", 3, f"fatima.alsayed@{dom}", "", "Third Place")
    add("typo", "Sara Kim", 4, "sara.kim@gmial.com", "", "Participation", "Participation")
    add("fallback", "Omar Haddad", 5, "omar.haddad(at)college", "omar.h@example.com", "Participation", "Participation")
    add("claim", "Daniel Okafor", 6, "", "", "Participation", "Participation")
    add("ref_name", "#REF!", 7, f"ref.student@{dom}", "", "Participation", "Participation")
    add("digit_name", "J0hn Sm1th", 8, f"john.smith@{dom}", "", "Participation", "Participation")
    add("blank_name", "", 9, f"unknown.student@{dom}", "", "Participation", "Participation")
    R.append(("duplicate", dict(R[1][1])))  # exact copy of Rohan's row
    add("transient", "Mei Lin Chen", 11, f"mei.chen@{dom}", "", "Best Innovation Award")
    add("bounce", "Noah Williams", 12, f"noah.williams@{dom}", "noah.personal@example.com", "Participation", "Participation")
    add("clean", "Priya Natarajan", 13, f"priya.n@{dom}", "", "Participation", "Participation")
    add("clean", "José Martínez", 14, f"jose.martinez@{dom}", "", "Outstanding Participation", "Participation")
    add("clean", "Zainab Yusuf", 15, f"zainab.y@{dom}", "", "Participation", "Participation")
    add("clean", "Lakshmi Venkataraman-Subramanian", 16, f"lakshmi.vs@{dom}", "", "Best Presentation")
    rows = [r for _, r in R]
    keys = {}
    for i, (k, _) in enumerate(R):
        keys.setdefault(k, []).append(i + 2)  # spreadsheet row numbers
    faults = {"transient": [f"mei.chen@{dom}"], "reject": [f"noah.williams@{dom}"]}
    return rows, keys, faults


def create(run_label: str = "") -> int:
    run = db.one("SELECT COUNT(*) n FROM batches WHERE is_chaos=1")["n"] + 1
    rows, keys, faults = _rows(run)
    path = config.UPLOAD_DIR / f"chaos_{run}.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    batch_id = pipeline.create_batch(path, path.name, run_label or f"Chaos Test #{run}",
                                     chaos={"run": run, "rows": keys, "faults": faults, "phase": "ready"})
    b = db.get_batch(batch_id)
    from .data import load_table
    cols, data = load_table(path)
    proposal = copilot.parse_local("winners get gold template, others participation", cols, data, b["config"]["mapping"])
    rules, _ = copilot.sanitize(proposal["rules"], cols)
    cfg = b["config"]
    cfg["rules"] = rules
    db.save_config(batch_id, cfg)
    pipeline.revalidate(batch_id)
    db.log(batch_id, None, "info", "copilot", "Copilot rules applied: “winners get gold template, others participation”")
    return batch_id


def _set_phase(batch_id: int, phase: str) -> None:
    b = db.get_batch(batch_id)
    chaos = b["chaos"]
    chaos["phase"] = phase
    db.set_batch(batch_id, chaos_json=json.dumps(chaos))


def _rec(batch_id: int, row_no: int) -> dict:
    return db.one("SELECT * FROM records WHERE batch_id=? AND row_no=?", (batch_id, row_no))


def _events(record_id: int, kind: str | None = None, level: str | None = None) -> list[dict]:
    sql, p = "SELECT * FROM events WHERE record_id=?", [record_id]
    if kind:
        sql += " AND kind=?"
        p.append(kind)
    if level:
        sql += " AND level=?"
        p.append(level)
    return db.query(sql, p)


def _outbox_for(batch_id: int) -> list[Path]:
    codes = {r["cert_code"] for r in db.query("SELECT cert_code FROM records WHERE batch_id=?", (batch_id,))}
    return sorted(p for p in config.OUTBOX_DIR.glob("*.eml") if p.name.split("_", 1)[0] in codes)


def audit_deliveries(batch_id: int) -> dict:
    """Open every delivered email and prove each attachment belongs to its recipient."""
    checked, wrong, details = 0, 0, []
    for p in _outbox_for(batch_id):
        msg = email.message_from_bytes(p.read_bytes(), policy=policy.default)
        code = msg["X-SealSend-Certificate"]
        rec = db.one("SELECT * FROM records WHERE cert_code=?", (code,))
        atts = list(msg.iter_attachments())
        ok = bool(rec) and len(atts) == 1
        if ok:
            data = atts[0].get_content()
            meta = certificate.read_proof(data)
            ok = (rec["email"].lower() in msg["To"].lower() and meta["code"] == rec["cert_code"]
                  and meta["lock"] == rec["mapping_lock"] and certificate.sha256(data) == rec["pdf_sha256"]
                  and rec["name"] in msg.get_body(("plain",)).get_content())
        checked += 1
        if not ok:
            wrong += 1
            details.append(p.name)
    return {"emails_checked": checked, "wrong": wrong, "wrong_files": details}


def run(batch_id: int) -> None:
    b = db.get_batch(batch_id)
    rows = b["chaos"]["rows"]
    _set_phase(batch_id, "generating")
    pipeline.generate(batch_id)

    # ---- inject runtime faults into the generated, signed, sealed certificates
    _set_phase(batch_id, "injecting")
    a, bb, t = _rec(batch_id, rows["swap_a"][0]), _rec(batch_id, rows["swap_b"][0]), _rec(batch_id, rows["tamper"][0])
    pa, pb, pt = (config.CERT_DIR / r["cert_file"] for r in (a, bb, t))
    da, dbytes = pa.read_bytes(), pb.read_bytes()
    pa.write_bytes(dbytes)
    pb.write_bytes(da)
    db.log(batch_id, None, "block", "chaos", f"CHAOS: swapped the PDFs of {a['name']} and {bb['name']} on disk")
    pt.write_bytes(certificate.forge_copy(pt.read_bytes(), "Mallory Impostor"))
    db.log(batch_id, None, "block", "chaos", f"CHAOS: edited the name inside {t['name']}'s signed PDF")
    tampered_seal = pipeline.verify_batch(batch_id)

    # ---- the real delivery run
    _set_phase(batch_id, "sending")
    pipeline.send(batch_id)
    before = len(_outbox_for(batch_id))
    _set_phase(batch_id, "double-send")
    pipeline.send(batch_id)  # accidental second click
    after = len(_outbox_for(batch_id))

    # ---- forged certificate presented to the public verifier
    _set_phase(batch_id, "auditing")
    victim = _rec(batch_id, rows["swap_a"][0])
    forged = certificate.forge_copy((config.CERT_DIR / victim["cert_file"]).read_bytes(), "Mallory Impostor")
    forged_path = config.DATA_DIR / "chaos" / f"forged_batch_{batch_id}.pdf"
    forged_path.parent.mkdir(exist_ok=True)
    forged_path.write_bytes(forged)
    forged_result = pipeline.verify_file(forged)
    genuine_result = pipeline.verify_file((config.CERT_DIR / victim["cert_file"]).read_bytes())
    seal_after = pipeline.verify_batch(batch_id)
    audit = audit_deliveries(batch_id)

    R = lambda k: _rec(batch_id, rows[k][0])  # noqa: E731
    def item(fault, defense, passed, observed):
        return {"fault": fault, "defense": defense, "passed": bool(passed), "observed": observed}

    typo, fb, cl = R("typo"), R("fallback"), R("claim")
    tr, bo, dup = R("transient"), R("bounce"), _rec(batch_id, rows["duplicate"][0])
    sa, sb, tp = R("swap_a"), R("swap_b"), R("tamper")
    items = [
        item("Spreadsheet error as a name (#REF!)", "Validation", R("ref_name")["status"] == "Invalid",
             R("ref_name")["errors"]),
        item("Digits inside a name (J0hn Sm1th)", "Validation", R("digit_name")["status"] == "Invalid",
             R("digit_name")["errors"]),
        item("Blank name", "Validation", R("blank_name")["status"] == "Invalid", R("blank_name")["errors"]),
        item("Duplicate row of the same student", "Validation", dup["status"] == "Invalid" and "Duplicate" in dup["errors"],
             dup["errors"]),
        item("Email domain typo (gmial.com)", "Self-healing: typo fix",
             typo["status"] == "Sent" and typo["email"] == "sara.kim@gmail.com",
             f"{typo['status']} to {typo['email']} ({typo['email_fix']})"),
        item("Broken primary email “omar.haddad(at)college”", "Self-healing: fallback email",
             fb["status"] == "Sent" and fb["email_source"] == "Personal Email", f"{fb['status']} to {fb['email']}"),
        item("No email address at all", "Self-healing: claim link",
             cl["status"] == "Claim" and bool(cl["claim_token"]), f"{cl['status']}, claim link issued"),
        item("Two students' PDFs swapped on disk", "Mapping Lock",
             sa["status"] == sb["status"] == "Sent" and _events(sa["id"], "lock", "block") and _events(sb["id"], "lock", "block")
             and audit["wrong"] == 0,
             f"Blocked {len(_events(sa['id'], 'lock', 'block')) + len(_events(sb['id'], 'lock', 'block'))} wrong attachments, "
             f"regenerated, both delivered correctly"),
        item("Name edited inside a signed PDF", "Hash + signature check",
             tp["status"] == "Sent" and _events(tp["id"], "lock", "block") and _events(tp["id"], "lock", "heal"),
             "Blocked, regenerated from source, delivered the genuine file"),
        item("Seal detects the tampering before sending", "Merkle batch seal",
             not tampered_seal["intact"] and len(tampered_seal["problems"]) >= 3,
             f"{len(tampered_seal['problems'])} problems found before delivery"),
        item("Mail server hiccup (421 try again)", "Self-healing: retry with backoff",
             tr["status"] == "Sent" and tr["attempts"] >= 2, f"{tr['status']} after {tr['attempts']} attempts"),
        item("Mailbox does not exist (550 bounce)", "Self-healing: re-bind to fallback email",
             bo["status"] == "Sent" and bo["email"] == "noah.personal@example.com" and _events(bo["id"], "bounce", "heal"),
             f"{bo['status']} to {bo['email']}"),
        item("Send clicked twice", "Duplicate prevention", after == before,
             f"{before} emails after first run, {after} after second"),
        item("Forged copy shown to the verifier", "Ed25519 + file fingerprint",
             forged_result["status"] == "modified" and genuine_result["status"] == "valid",
             f"Forged: {forged_result['title']}. Genuine: {genuine_result['title']}"),
        item("Whole batch after healing", "Merkle batch seal", seal_after["intact"],
             f"Seal intact over {seal_after.get('count', 0)} certificates"),
    ]
    counts = db.status_counts(batch_id)
    unfinished = sum(counts.get(s, 0) for s in ("Pending", "Generated", "Sending"))
    report = {
        "items": items,
        "passed": sum(i["passed"] for i in items), "total": len(items),
        "audit": audit, "batch_finished": unfinished == 0, "counts": counts,
        "forged_file": forged_path.name, "verify_code": victim["cert_code"],
        "wrong_attachments_delivered": audit["wrong"],
    }
    chaos = db.get_batch(batch_id)["chaos"]
    chaos.update({"phase": "done", "report": report})
    db.set_batch(batch_id, chaos_json=json.dumps(chaos))
    db.log(batch_id, None, "ok" if report["passed"] == report["total"] else "error", "chaos",
           f"Chaos Test: {report['passed']}/{report['total']} faults contained, "
           f"{audit['emails_checked']} delivered emails audited, {audit['wrong']} wrong")
