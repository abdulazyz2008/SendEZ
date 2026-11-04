"""End-to-end tests for SendEZ. Every promise is exercised against the real pipeline."""
import email
import importlib
import json
import smtplib
import time
from collections import Counter
from email import policy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SEALSEND_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ADMIN_PASSWORD", "test-pass")
    for var in ("SMTP_HOST", "RESEND_API_KEY", "ANTHROPIC_API_KEY", "SEALSEND_SIGNING_KEY", "PUBLIC_BASE_URL"):
        monkeypatch.delenv(var, raising=False)
    import sealsend.config as config
    importlib.reload(config)
    monkeypatch.setattr(config, "RETRY_BASE_SECONDS", 0.01)
    from sealsend import crypto, db, settings
    crypto.reset_keys()
    db.reset_connection()
    settings.reset_cache()
    import sealsend.main as main
    from fastapi.testclient import TestClient
    with TestClient(main.app) as c:
        yield c
    db.reset_connection()
    crypto.reset_keys()
    settings.reset_cache()


def login(c):
    assert c.post("/api/login", json={"password": "test-pass"}).status_code == 200


def upload(c, name="students.csv", event="AI Hackathon 2026"):
    with open(ROOT / "sample" / name, "rb") as f:
        r = c.post("/api/batches", files={"file": (name, f)}, data={"name": event})
    assert r.status_code == 200, r.text
    return r.json()


def wait_idle(c, bid, timeout=120):
    end = time.time() + timeout
    while time.time() < end:
        b = c.get(f"/api/batches/{bid}").json()
        if not b["job"]:
            return b
        time.sleep(0.1)
    raise AssertionError("job did not finish")


def records(c, bid, status=""):
    return c.get(f"/api/batches/{bid}/records", params={"status": status, "limit": 1000}).json()["records"]


def by_row(c, bid):
    return {r["row_no"]: r for r in records(c, bid)}


def send(c, bid):
    r = c.post(f"/api/batches/{bid}/send", json={"confirm": True})
    assert r.status_code == 200, r.text
    return wait_idle(c, bid)


def save(c, bid, cfg):
    r = c.put(f"/api/batches/{bid}/config", json=cfg)
    assert r.status_code == 200, r.text
    return r.json()


def outbox(tmp_path):
    return sorted((tmp_path / "outbox").glob("*.eml"))


def audit(bid):
    from sealsend import chaos
    return chaos.audit_deliveries(bid)


# ---------------------------------------------------------------- units
def test_merkle_and_signatures(client):
    from sealsend import crypto
    for n in range(1, 34):
        leaves = [crypto.leaf_hash(f"SS-{i}", "a" * 64, "lock") for i in range(n)]
        root = crypto.merkle_root(leaves)
        for i in range(n):
            assert crypto.verify_proof(leaves[i], crypto.merkle_proof(leaves, i), root)
    sig = crypto.sign(b"payload")
    assert crypto.verify(b"payload", sig) and not crypto.verify(b"payl0ad", sig) and not crypto.verify(b"payload", "xx")


def test_email_self_healing_rules():
    from sealsend.data import fix_email_typo, normalize_email, pick_email
    sheet = Counter({"college.example.edu": 9})
    for raw, want in {"a@gmial.com": "a@gmail.com", "a@gmail.co": "a@gmail.com", "a@hotmial.com": "a@hotmail.com",
                      "a@colege.example.edu": "a@college.example.edu", "a@college.example.con": "a@college.example.edu"}.items():
        assert fix_email_typo(normalize_email(raw), sheet)[0] == want, raw
    for untouched in ("a@gmx.com", "a@lime.com", "a@ucla.edu", "a@college.example.edu", "a@gmail.com"):
        assert fix_email_typo(untouched, sheet)[0] == "", untouched
    assert normalize_email(" Mailto:<Omar(at)College.Example.EDU>. ") == "Omar@college.example.edu"
    assert pick_email("bad@", "real@example.com", "P", "F", True, sheet)["email"] == "real@example.com"
    assert pick_email("me@gmial.com", "", "P", "F", True, sheet)["email"] == "me@gmail.com"
    assert pick_email("a@x.com; b@y.com", "", "P", "F", True, sheet)["email"] == "a@x.com"


def test_names_and_wording():
    from sealsend.data import check_name, classify, honor_phrase, smart_title
    for bad in ["#REF!", "N/A", "J0hn Smith", "", "a", "x@y.com", "John_Smith", "Total"]:
        assert check_name(bad)[0], bad
    for good in ["José Martínez", "Mary-Jane O'Neil", "Lakshmi Venkataraman-Subramanian"]:
        assert check_name(good) == ([], []), good
    assert smart_title("ROHAN AL-MEHTA") == "Rohan Al-Mehta" and smart_title("McDonald") == "McDonald"
    assert honor_phrase("Winner") == "being the Winner" and honor_phrase("First Place") == "securing First Place"
    assert [classify(v) for v in ("Winner", "Runner Up", "Volunteer", "Participant", "Intern", "Coordinator")] == \
        ["winner", "winner", "appreciation", "participation", "completion", "appreciation"]


def test_text_rendering_defaults_and_optional_segments():
    from sealsend.templates_lib import render_text, required_tokens
    v = {"NAME": "A", "EVENT": "Fest", "DATE": ""}
    assert render_text("for {{EVENT}}[[, held on {{DATE}}]]", v) == "for Fest"
    assert render_text("for {{EVENT}}[[, held on {{DATE}}]]", {**v, "DATE": "1 May"}) == "for Fest, held on 1 May"
    assert render_text("{{TITLE|Certificate of Merit}}", v) == "Certificate of Merit"
    assert required_tokens("{{NAME}} {{TITLE|x}} [[{{DATE}}]]") == ["NAME"]


def test_all_builtin_templates_render_deterministically_with_proof(client):
    from sealsend import certificate, templates_lib as T
    v = dict(T.SAMPLE_VALUES, NAME="Анна Иванова")  # Cyrillic falls back to a font that has the glyphs
    for t in T.BUILTINS:
        a = certificate.render_pdf(t["id"], v, "SS-1", "lock1")
        assert a == certificate.render_pdf(t["id"], v, "SS-1", "lock1"), t["id"]
        assert certificate.read_proof(a) == {"code": "SS-1", "lock": "lock1"}
        assert certificate.pdf_to_png(a, 30)[:4] == b"\x89PNG"


def test_messy_excel_is_understood(client):
    from sealsend.data import read_table, suggest_config
    t = read_table(ROOT / "sample" / "tech_fest_2026.xlsx")
    assert t["sheet"] == "Participants" and t["header_row"] == 3 and len(t["rows"]) == 16  # footer row skipped
    cfg = suggest_config(t["columns"], t["rows"])
    assert cfg["mapping"]["NAME"] == {"type": "combine", "value": ["First Name", "Last Name"]}
    assert (cfg["email_column"], cfg["fallback_column"], cfg["category_column"]) == ("College Email ID", "Personal Mail", "Award Category")
    assert cfg["mapping"]["DATE"]["value"] == "Event Date" and "EVENT" not in cfg["mapping"]


# ---------------------------------------------------------------- auth + setup
def test_auth_and_public_pages(client):
    assert client.get("/api/batches").status_code == 401
    assert client.post("/api/login", json={"password": "nope"}).status_code == 401
    assert client.get("/verify").status_code == 200
    assert "BEGIN PUBLIC KEY" in client.get("/.well-known/sealsend-public-key.pem").text
    assert "Not verified" in client.get("/verify/SS-FAKE000000").text
    assert client.get("/manifest.webmanifest").json()["display"] == "standalone"


def test_first_run_setup_is_local_only(client, monkeypatch):
    from sealsend import config, main, settings
    monkeypatch.setattr(config, "ADMIN_PASSWORD", "")
    settings.reset_cache()
    assert client.get("/api/setup-status").json()["needs_setup"]
    assert client.post("/api/setup", json={"password": "short"}).status_code == 400
    assert client.post("/api/setup", json={"password": "long-enough-pw"}).status_code == 200
    assert client.post("/api/setup", json={"password": "another-password"}).status_code == 400  # only once
    assert client.post("/api/login", json={"password": "long-enough-pw"}).status_code == 200
    assert "long-enough-pw" not in config.SETTINGS_PATH.read_text()
    req = lambda host, headers: type("R", (), {"client": type("C", (), {"host": host})(), "headers": headers})()  # noqa: E731
    assert not main._is_local(req("192.168.1.50", {}))
    assert main._is_local(req("127.0.0.1", {}))
    assert not main._is_local(req("127.0.0.1", {"cf-connecting-ip": "203.0.113.9"}))  # came through the public tunnel
    assert not main._is_local(req("127.0.0.1", {"x-forwarded-for": "203.0.113.9"}))


# ---------------------------------------------------------------- workflow
def test_upload_validation_and_categories(client):
    login(client)
    b = upload(client, "tech_fest_2026.xlsx", "National Tech Fest 2026")
    cfg = b["config"]
    assert cfg["template_id"] == "tech-hackathon" and cfg["mapping"]["EVENT"] == {"type": "text", "value": "National Tech Fest 2026"}
    assert [r["label"] for r in cfg["rules"]] == ["Winner", "Runner Up", "Best Paper Award", "Volunteer", "Coordinator", "Participant"]
    assert [r["template"] for r in cfg["rules"]][:3] == ["gold-excellence"] * 3
    r = by_row(client, b["id"])
    assert r[9]["email"] == "omar.h@example.com" and r[10]["email"] == "sara.kim@gmail.com"
    assert r[11]["delivery"] == "claim" and r[16]["status"] == "Invalid" and "Duplicate of row 5" in r[17]["errors"]
    assert b["counts"] == {"Invalid": 2, "Pending": 14, "Total": 16}
    pv = client.get(f"/api/batches/{b['id']}/preview/email", params={"record_id": r[4]["id"]}).json()
    assert pv["title"] == "Certificate of Excellence" and "being the Winner" in pv["body"]
    vol = client.get(f"/api/batches/{b['id']}/preview/email", params={"record_id": r[12]["id"]}).json()
    assert vol["title"] == "Certificate of Appreciation" and vol["subject"].startswith("Thank you for your contribution")
    cfg["fix_caps"] = True
    save(client, b["id"], cfg)
    assert by_row(client, b["id"])[5]["name"] == "Rohan Mehta"
    sw = client.post(f"/api/batches/{b['id']}/sheet", json={"sheet": "Instructions"})
    assert sw.status_code == 200 and sw.json()["counts"]["Total"] == 0 or sw.status_code == 400


def test_full_flow_lock_signatures_seal_dedupe(client, tmp_path):
    login(client)
    b = upload(client)
    bid = b["id"]
    assert client.post(f"/api/batches/{bid}/send", json={"confirm": False}).status_code == 400
    done = send(client, bid)
    assert done["counts"] == {"Invalid": 2, "Sent": 11, "Claim": 2, "Total": 15} and done["sealed"]
    assert audit(bid) == {"emails_checked": 11, "wrong": 0, "wrong_files": []}
    seal = client.post(f"/api/batches/{bid}/verify-seal").json()
    assert seal["intact"] and seal["count"] == 13
    sent = records(client, bid, "Sent")
    detail = client.get(f"/api/records/{sent[0]['id']}").json()
    assert detail["proof"]["valid"] and detail["proof"]["lock_check"] == []
    page = client.get(f"/verify/{sent[0]['cert_code']}").text
    assert "Verified" in page and sent[0]["name"] in page
    pdf = client.get(f"/api/records/{sent[0]['id']}/certificate").content
    assert "Authentic certificate" in client.post("/verify", files={"file": ("c.pdf", pdf)}).text
    from sealsend import certificate
    assert "This file has been altered" in client.post("/verify", files={"file": ("f.pdf", certificate.forge_copy(pdf, "Mallory"))}).text
    n = len(outbox(tmp_path))
    send(client, bid)
    assert len(outbox(tmp_path)) == n
    b2 = upload(client)
    send(client, b2["id"])
    assert len(records(client, b2["id"], "Skipped")) == 11 and len(outbox(tmp_path)) == n
    assert client.post(f"/api/records/{sent[0]['id']}/resend").status_code == 200
    wait_idle(client, bid)
    assert len(outbox(tmp_path)) == n + 1
    csv_text = client.get(f"/api/batches/{bid}/report.csv").content.decode("utf-8")
    assert csv_text.startswith("﻿") and "Missing name" in csv_text and "José Martínez" in csv_text
    z = client.get(f"/api/batches/{bid}/certificates.zip")
    assert z.status_code == 200 and b"SEAL.json" in z.content


def test_claim_links(client):
    login(client)
    bid = upload(client)["id"]
    send(client, bid)
    claim = by_row(client, bid)[12]
    assert claim["status"] == "Claim"
    token = client.get(f"/api/records/{claim['id']}").json()["claim_url"].rsplit("/", 1)[1]
    assert "Your certificate" in client.get(f"/claim/{token}").text
    assert "doesn’t match" in client.post(f"/claim/{token}", data={"student_id": "WRONG"}).text
    assert client.post(f"/claim/{token}", data={"student_id": "stu011"}).headers["content-type"] == "application/pdf"
    assert by_row(client, bid)[12]["status"] == "Claimed"
    for _ in range(5):
        client.post(f"/claim/{token}", data={"student_id": "nope"})
    assert "Link locked" in client.post(f"/claim/{token}", data={"student_id": "stu011"}).text


def test_swapped_files_blocked_and_healed(client, tmp_path):
    login(client)
    bid = upload(client)["id"]
    client.post(f"/api/batches/{bid}/generate")
    wait_idle(client, bid)
    a, z = [r for r in records(client, bid, "Generated") if r["email"]][:2]
    pa, pz = tmp_path / "certificates" / a["cert_file"], tmp_path / "certificates" / z["cert_file"]
    da, dz = pa.read_bytes(), pz.read_bytes()
    pa.write_bytes(dz)
    pz.write_bytes(da)
    assert not client.post(f"/api/batches/{bid}/verify-seal").json()["intact"]
    send(client, bid)
    for rid in (a["id"], z["id"]):
        ev = client.get(f"/api/records/{rid}").json()["events"]
        assert any(e["level"] == "block" for e in ev) and any(e["level"] == "heal" for e in ev)
    assert audit(bid)["wrong"] == 0 and client.post(f"/api/batches/{bid}/verify-seal").json()["intact"]


def test_database_tampering_breaks_verification(client):
    login(client)
    bid = upload(client)["id"]
    send(client, bid)
    rec = records(client, bid, "Sent")[0]
    from sealsend import db
    payload = json.loads(db.get_record(rec["id"])["payload_json"])
    payload["name"] = "Someone Else"
    db.update_record(rec["id"], payload_json=json.dumps(payload))
    assert "Verification failed" in client.get(f"/verify/{rec['cert_code']}").text
    assert not client.post(f"/api/batches/{bid}/verify-seal").json()["intact"]


# ---------------------------------------------------------------- templates
def test_template_gallery_import_and_editor(client, tmp_path):
    login(client)
    cat = client.get("/api/templates").json()
    assert len([t for t in cat if not t["custom"]]) == 14
    assert client.get("/api/templates/gold-excellence/thumb.png").content[:4] == b"\x89PNG"
    from PIL import Image
    import io
    buf = io.BytesIO()
    Image.new("RGBA", (2000, 1414), (240, 230, 210, 255)).save(buf, "PNG")
    t = client.post("/api/templates/import", files={"file": ("canva.png", buf.getvalue())}, data={"name": "Canva Gala"}).json()
    assert t["custom"] and t["fields"][0]["key"] == "name"
    fields = t["fields"]
    fields[0]["x"], fields[0]["y"], fields[0]["font"] = 0.4, 0.55, "script"
    assert client.put(f"/api/templates/{t['id']}", json={"fields": fields, "name": "Gala"}).status_code == 200
    assert client.get(f"/api/templates/{t['id']}").json()["fields"][0]["x"] == 0.4
    from sealsend import templates_lib as T
    pdf_bg = T.render(dict(T.BUILTIN_BY_ID["arts-ornate"], fields=[]), {}, {}, {})
    t2 = client.post("/api/templates/import", files={"file": ("canva.pdf", pdf_bg)}, data={"name": "PDF"}).json()
    b = upload(client)
    cfg = b["config"]
    cfg["template_id"] = t2["id"]
    save(client, b["id"], cfg)
    assert client.delete(f"/api/templates/{t2['id']}").status_code == 400  # still in use
    done = send(client, b["id"])
    assert done["counts"]["Sent"] == 11 and audit(b["id"])["wrong"] == 0
    assert client.post("/api/templates/import", files={"file": ("x.txt", b"hi")}).status_code == 400
    assert client.delete("/api/templates/gold-excellence").status_code == 400


def test_branding_appears_on_certificates(client):
    login(client)
    from PIL import Image
    import io
    buf = io.BytesIO()
    Image.new("RGBA", (400, 200), (0, 0, 0, 255)).save(buf, "PNG")
    logo = client.post("/api/branding/upload", files={"file": ("logo.png", buf.getvalue())}, data={"kind": "logo"}).json()["file"]
    b = upload(client)
    cfg = b["config"]
    cfg["branding"].update({"org_name": "ABC College", "logo": logo, "signer1_name": "Dr. Priya Sharma"})
    save(client, b["id"], cfg)
    r = records(client, b["id"])[0]
    assert client.get(f"/api/records/{r['id']}").json()["values"]["ORG_NAME"] == "ABC College"
    png = client.get(f"/api/batches/{b['id']}/preview/certificate", params={"record_id": r["id"]})
    assert png.headers["content-type"] == "image/png"


# ---------------------------------------------------------------- delivery
def test_settings_mail_encrypted_and_validated(client):
    login(client)
    assert client.put("/api/settings/mail", json={"provider": "gmail", "address": "me@gmail.com", "password": "short"}).status_code == 400
    assert client.put("/api/settings/mail", json={"provider": "gmail", "address": "nope", "password": ""}).status_code == 400
    r = client.put("/api/settings/mail", json={"provider": "gmail", "address": "me@gmail.com", "password": "abcd efgh ijkl mnop"})
    assert r.status_code == 200 and r.json()["mode"] == "smtp"
    from sealsend import config, settings
    assert "abcd" not in config.SETTINGS_PATH.read_text() and settings.mail()["password"] == "abcdefghijklmnop"
    s = client.get("/api/settings").json()
    assert s["mail"]["has_password"] and "password" not in s["mail"]


def _fake_smtp(monkeypatch, behaviour):
    from sealsend import mailer
    log = {"sent": [], "connects": []}

    class FakeSMTP:
        def __init__(self, host, port, timeout=20, context=None):
            log["connects"].append((host, port))
            behaviour.get("connect", lambda h, p: None)(host, port)
        def ehlo(self): pass
        def starttls(self, context=None): pass
        def login(self, u, p): behaviour.get("login", lambda u, p: None)(u, p)
        def send_message(self, msg):
            behaviour.get("send", lambda m: None)(msg)
            log["sent"].append(msg)
        def quit(self): pass

    monkeypatch.setattr(mailer.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(mailer.smtplib, "SMTP_SSL", FakeSMTP)
    return log


def _gmail(client, cap=450):
    assert client.put("/api/settings/mail", json={"provider": "gmail", "address": "me@gmail.com", "password": "abcdefghijklmnop",
                                                  "daily_cap": cap, "delay_seconds": 0}).status_code == 200


def test_gmail_bounce_rebinds_and_connection_reused(client, monkeypatch):
    def send_(msg):
        if "<rohan.mehta@college.example.edu>" in msg["To"]:
            raise smtplib.SMTPRecipientsRefused({"rohan.mehta@college.example.edu": (550, b"no such user")})
    log = _fake_smtp(monkeypatch, {"send": send_})
    login(client)
    _gmail(client)
    bid = upload(client)["id"]
    send(client, bid)
    assert len(log["sent"]) == 11 and log["connects"].count(("smtp.gmail.com", 587)) == 2  # preflight + batch
    rohan = by_row(client, bid)[3]
    assert rohan["status"] == "Sent" and rohan["email"] == "rohan.m@example.com"
    from sealsend import certificate, db
    att = next(m for m in log["sent"] if "rohan.m@example.com" in m["To"])
    assert certificate.read_proof(next(att.iter_attachments()).get_content())["lock"] == db.get_record(rohan["id"])["mapping_lock"]
    assert att["From"].endswith("<me@gmail.com>")


def test_wrong_gmail_password_never_starts_or_fails_students(client, monkeypatch):
    def bad_login(u, p):
        raise smtplib.SMTPAuthenticationError(535, b"5.7.8 Username and Password not accepted")
    _fake_smtp(monkeypatch, {"login": bad_login})
    login(client)
    _gmail(client)
    bid = upload(client)["id"]
    r = client.post(f"/api/batches/{bid}/send", json={"confirm": True})
    assert r.status_code == 400 and "App Password" in r.json()["detail"]
    assert not records(client, bid, "Failed") and not records(client, bid, "Sent")
    assert not client.post("/api/settings/test-connection").json()["ok"]


def test_port_587_blocked_falls_back_to_465(client, monkeypatch):
    def connect(host, port):
        if port == 587:
            raise OSError("Connection refused")
    log = _fake_smtp(monkeypatch, {"connect": connect})
    login(client)
    _gmail(client)
    assert client.post("/api/settings/test-connection").json()["ok"]
    assert ("smtp.gmail.com", 465) in log["connects"]


def test_daily_cap_pauses_and_resumes_without_loss(client, monkeypatch):
    log = _fake_smtp(monkeypatch, {})
    login(client)
    _gmail(client, cap=5)
    bid = upload(client)["id"]
    done = send(client, bid)
    assert len(log["sent"]) == 5 and "Daily sending limit" in done["pause_reason"]
    assert done["counts"].get("Failed", 0) == 0
    from sealsend import db

    def next_day():
        with db.tx() as c:
            c.execute("UPDATE send_log SET at='2000-01-01T00:00:00Z'")
    next_day()
    done = send(client, bid)  # 11 deliverable students, 5 per day: 5, 5, then 1
    assert done["counts"]["Sent"] == 10 and "Daily sending limit" in done["pause_reason"]
    next_day()
    done = send(client, bid)
    assert done["counts"]["Sent"] == 11 and done["pause_reason"] == ""
    assert len(log["sent"]) == 11 and len({m["To"] for m in log["sent"]}) == 11  # nobody emailed twice


def test_scheduled_send(client):
    from datetime import datetime, timedelta, timezone
    from sealsend import pipeline
    now = datetime.now(timezone.utc)
    iso = lambda dt: dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    assert client.post("/api/batches/1/schedule", json={"at": iso(now), "confirm": True}).status_code == 401
    login(client)
    bid = upload(client)["id"]
    url = f"/api/batches/{bid}/schedule"
    assert client.post(url, json={"at": iso(now + timedelta(hours=1))}).status_code == 400  # needs confirmation
    assert client.post(url, json={"at": iso(now - timedelta(hours=1)), "confirm": True}).status_code == 400
    assert client.post(url, json={"at": "2030-01-01T09:00:00", "confirm": True}).status_code == 400  # no time zone
    assert client.post(url, json={"at": iso(now + timedelta(days=90)), "confirm": True}).status_code == 400
    r = client.post(url, json={"at": iso(now + timedelta(hours=1)), "confirm": True})
    assert r.status_code == 200 and r.json()["send_kind"] == "schedule" and r.json()["send_at"]
    assert client.delete(url).json()["send_at"] == ""
    client.post(url, json={"at": iso(now + timedelta(hours=1)), "confirm": True})
    pipeline.scheduler_tick(now + timedelta(minutes=30))  # not due yet
    assert client.get(f"/api/batches/{bid}").json()["counts"].get("Sent", 0) == 0
    pipeline.scheduler_tick(now + timedelta(hours=1, minutes=1))
    done = wait_idle(client, bid)
    assert done["counts"]["Sent"] == 11 and done["send_at"] == "" and done["pause_reason"] == ""
    ev = client.get(f"/api/batches/{bid}/events", params={"limit": 100}).json()
    assert any("Scheduled send started" in e["message"] for e in ev)

    late = upload(client, event="Missed Event")["id"]
    client.post(f"/api/batches/{late}/schedule", json={"at": iso(now + timedelta(hours=1)), "confirm": True})
    pipeline.scheduler_tick(now + timedelta(hours=14))  # SendEZ was closed for half a day: wait for a person
    b = client.get(f"/api/batches/{late}").json()
    assert "missed" in b["pause_reason"] and b["counts"].get("Sent", 0) == 0 and b["send_at"] == ""


def test_auto_continue_after_daily_limit(client, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from sealsend import db, pipeline
    log = _fake_smtp(monkeypatch, {})
    login(client)
    _gmail(client, cap=5)
    bid = upload(client)["id"]
    done = send(client, bid)
    assert len(log["sent"]) == 5 and "continues automatically" in done["pause_reason"] and done["send_kind"] == "limit"
    at = datetime.strptime(done["send_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    assert timedelta(hours=23) < at - datetime.now(timezone.utc) < timedelta(hours=25)
    pipeline.scheduler_tick(at - timedelta(hours=1))
    assert not pipeline.is_running(bid) and len(log["sent"]) == 5  # not yet
    with db.tx() as c:  # a day later the allowance is free again
        c.execute("UPDATE send_log SET at='2000-01-01T00:00:00Z'")
    pipeline.scheduler_tick(at + timedelta(minutes=1))
    done = wait_idle(client, bid)
    assert done["counts"]["Sent"] == 10 and done["send_kind"] == "limit"  # 5 more, then waits again
    assert client.delete(f"/api/batches/{bid}/schedule").json()["send_at"] == ""  # "Don't continue"
    ev = client.get(f"/api/batches/{bid}/events", params={"limit": 200}).json()
    assert any("continuing automatically" in e["message"] for e in ev)

    # switched off in Settings: pauses and waits for the organizer, as before
    assert client.put("/api/settings/mail", json={"provider": "gmail", "address": "me@gmail.com", "daily_cap": 5,
                                                  "delay_seconds": 0, "auto_resume": False}).status_code == 200
    with db.tx() as c:
        c.execute("UPDATE send_log SET at='2000-01-01T00:00:00Z'")
    other = upload(client, name="tech_fest_2026.xlsx", event="Second")["id"]  # different students
    done = send(client, other)
    assert done["send_at"] == "" and "press Send again" in done["pause_reason"]


def test_second_sending_account(client, monkeypatch):
    from sealsend import db, pipeline
    log = _fake_smtp(monkeypatch, {})
    login(client)
    _gmail(client, cap=3)
    m2 = {"provider": "gmail", "address": "second@gmail.com", "password": "bcdefghijklmnopq", "daily_cap": 2}
    assert client.put("/api/settings/mail2", json={**m2, "password": "short"}).status_code == 400  # not an App Password
    assert client.put("/api/settings/mail2", json={**m2, "address": "me@gmail.com"}).status_code == 400  # same as main
    assert client.put("/api/settings/mail2", json={**m2, "password": ""}).status_code == 400  # needs a password first time
    r = client.put("/api/settings/mail2", json=m2)
    assert r.status_code == 200 and r.json()["quota"]["cap"] == 5 and len(r.json()["quota"]["accounts"]) == 2
    assert client.post("/api/settings/test-connection2").json()["ok"]
    assert client.get("/api/me").json()["mail2"]["address"] == "second@gmail.com"

    bid = upload(client)["id"]
    done = send(client, bid)
    froms = [m["From"] for m in log["sent"]]
    assert len(froms) == 5 and sum("me@gmail.com" in f for f in froms) == 3 and sum("second@gmail.com" in f for f in froms) == 2
    assert froms.index(next(f for f in froms if "second@" in f)) == 3  # the main account fills up first
    second = [m for m in log["sent"] if "second@" in m["From"]]
    assert all(m["Reply-To"] == "me@gmail.com" for m in second)  # replies still reach the main inbox
    assert "Daily sending limit reached (5 emails" in done["pause_reason"]
    accts = {r["account"]: r["n"] for r in db.query("SELECT account, COUNT(*) n FROM send_log WHERE result='sent' GROUP BY account")}
    assert accts == {"me@gmail.com": 3, "second@gmail.com": 2}
    q = client.get("/api/me").json()["quota"]
    assert (q["cap"], q["left"]) == (5, 0) and [a["left"] for a in q["accounts"]] == [0, 0]

    # Gmail says "limit reached" early on the main account: the rest go from the second account
    with db.tx() as c:
        c.execute("UPDATE send_log SET at='2000-01-01T00:00:00Z'")
    client.put("/api/settings/mail2", json={**m2, "password": "", "daily_cap": 100})  # keeps the saved password

    def send_(msg):
        if "me@gmail.com" in msg["From"]:
            raise smtplib.SMTPDataError(550, b"5.4.5 Daily user sending quota exceeded.")
    _fake_smtp(monkeypatch, {"send": send_})
    done = send(client, bid)
    assert done["counts"]["Sent"] == 11 and done["pause_reason"] == "" and not done["counts"].get("Failed")
    ev = client.get(f"/api/batches/{bid}/events", params={"limit": 200}).json()
    assert any("reached its sending limit; continuing from second@gmail.com" in e["message"] for e in ev)

    assert client.delete("/api/settings/mail2").json()["quota"]["cap"] == 3
    assert client.get("/api/me").json()["mail2"] is None


def test_provider_quota_error_pauses(client, monkeypatch):
    calls = {"n": 0}

    def send_(msg):
        calls["n"] += 1
        if calls["n"] > 3:
            raise smtplib.SMTPDataError(550, b"5.4.5 Daily user sending quota exceeded.")
    _fake_smtp(monkeypatch, {"send": send_})
    login(client)
    _gmail(client)
    bid = upload(client)["id"]
    done = send(client, bid)
    assert done["counts"]["Sent"] == 3 and "limit" in done["pause_reason"].lower() and not done["counts"].get("Failed")
    assert done["send_kind"] == "limit" and done["send_at"]  # the provider said "tomorrow": try again in 24 hours


def test_transient_failure_isolated_then_retried(client, monkeypatch):
    login(client)
    bid = upload(client)["id"]
    from sealsend import config, mailer
    monkeypatch.setattr(config, "MAX_SEND_ATTEMPTS", 2)
    real = mailer.Sender.send

    def flaky(self, msg, to_addr, name):
        if "rohan" in to_addr:
            raise mailer.TransientFailure("421 busy")
        return real(self, msg, to_addr, name)

    monkeypatch.setattr(mailer.Sender, "send", flaky)
    done = send(client, bid)
    assert done["counts"]["Sent"] == 10 and done["counts"]["Failed"] == 1
    monkeypatch.setattr(mailer.Sender, "send", real)
    assert client.post(f"/api/batches/{bid}/retry").json()["started"]
    assert wait_idle(client, bid)["counts"]["Sent"] == 11


def test_resend_api_request_shape(client, monkeypatch):
    from sealsend import config, mailer
    captured = {}

    class Resp:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def read(self): return b'{"id":"re_123"}'

    def fake_urlopen(req, timeout=30):
        captured["body"], captured["auth"] = json.loads(req.data), req.headers["Authorization"]
        return Resp()

    monkeypatch.setattr(config, "RESEND_API_KEY", "re_key")
    monkeypatch.setattr(mailer.urllib.request, "urlopen", fake_urlopen)
    msg = mailer.build_message("a@b.co", "A", "Hi", "Body", b"%PDF", "x.pdf", "SS-1")
    assert mailer.Sender(mode="resend").send(msg, "a@b.co", "n").endswith("re_123")
    assert captured["auth"] == "Bearer re_key" and captured["body"]["attachments"][0]["filename"] == "x.pdf"


# ---------------------------------------------------------------- copilot + chaos + scale
def test_copilot_offline_and_claude(client, monkeypatch):
    login(client)
    b = upload(client)
    prop = client.post(f"/api/batches/{b['id']}/copilot", json={"text": "winners get gold template, others participation"}).json()
    assert prop["engine"] == "offline" and [r["template"] for r in prop["rules"]] == ["gold-excellence", "participation-clean"]
    import anthropic
    from sealsend import copilot

    class Block:
        type = "text"
        text = json.dumps({"rules": [{"label": "Winners", "match": {"column": "Achievement", "op": "any_of", "values": ["First Place"]},
                                      "theme": "gold-excellence", "email": "winner", "email_subject": "", "email_body": ""}], "notes": []})

    class Fake:
        def __init__(self, **kw):
            self.beta = self
            self.messages = self
        def create(self, **kw):
            return type("R", (), {"stop_reason": "end_turn", "content": [Block()]})()

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(anthropic, "Anthropic", Fake)
    rows = [{"Achievement": "First Place"}, {"Achievement": "Participation"}]
    out = copilot.suggest("first place gets gold", ["Achievement"], rows, {})
    assert out["engine"].startswith("Claude") and out["counts"] == [1] and out["rules"][0]["style"] == "winner"
    monkeypatch.setattr(Fake, "create", lambda self, **kw: (_ for _ in ()).throw(anthropic.APIConnectionError(request=None)))
    out = copilot.suggest("first place gets gold", ["Achievement"], rows, {})
    assert out["engine"] == "offline" and "Claude unavailable" in out["notes"][0]


def test_club_placeholder_survives_missing_font(tmp_path, monkeypatch):
    from sealsend import clubs, config
    monkeypatch.setattr(config, "PACKAGE_DIR", tmp_path)  # no fonts folder here
    png = clubs.placeholder_png("coding-ninjas", 64)
    assert png[:4] == b"\x89PNG"


def test_reuse_settings_from_a_past_event(client):
    login(client)
    a = upload(client, event="Hackathon 2025")
    cfg = client.get(f"/api/batches/{a['id']}").json()["config"]
    assert cfg["rules"], "sample sheet should have categories"
    first = cfg["rules"][0]["label"]
    cfg["rules"][0].update(template="corporate-modern", title="Champion of the Year")
    cfg.update(template_id="mono-minimal", email_subject="Well done {{NAME}}!", qr=False, filename_pattern="{{NAME}}_cert",
               branding={**cfg["branding"], "signer1_name": "Dr. Reuse", "org_name": "Reuse College"})
    assert client.put(f"/api/batches/{a['id']}/config", json=cfg).status_code == 200

    with open(ROOT / "sample" / "students.csv", "rb") as f:
        r = client.post("/api/batches", files={"file": ("students.csv", f)}, data={"name": "Hackathon 2026", "reuse": str(a["id"])})
    assert r.status_code == 200, r.text
    b = r.json()["config"]
    assert (b["template_id"], b["email_subject"], b["qr"], b["filename_pattern"]) == ("mono-minimal", "Well done {{NAME}}!", False, "{{NAME}}_cert")
    assert b["branding"]["signer1_name"] == "Dr. Reuse" and b["branding"]["org_name"] == "Reuse College"
    rule = next(x for x in b["rules"] if x["label"] == first)
    assert (rule["template"], rule["title"]) == ("corporate-modern", "Champion of the Year")
    assert b["mapping"]["EVENT"] == {"type": "column", "value": "Event"}  # event name comes from the new sheet, not the old event
    ev = client.get(f"/api/batches/{r.json()['id']}/events", params={"limit": 50}).json()
    assert any("copied from “Hackathon 2025”" in e["message"] for e in ev)

    # a very different sheet: rules for categories it doesn't have are simply not used
    with open(ROOT / "sample" / "tech_fest_2026.xlsx", "rb") as f:
        r = client.post("/api/batches", files={"file": ("tech_fest_2026.xlsx", f)}, data={"name": "Tech Fest", "reuse": str(a["id"])})
    assert r.status_code == 200 and r.json()["config"]["template_id"] == "mono-minimal"
    assert client.post("/api/batches", files={"file": ("students.csv", b"Name,Email\nA,a@b.co\n")},
                       data={"reuse": "9999"}).status_code == 400


def test_google_sheets_link_import(client, monkeypatch):
    import urllib.error
    from sealsend import gsheets
    sid = "1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"
    for url in (f"https://docs.google.com/spreadsheets/d/{sid}/edit#gid=0", f"https://docs.google.com/spreadsheets/d/{sid}/edit?usp=sharing",
                f"https://docs.google.com/spreadsheets/d/{sid}"):
        assert gsheets.sheet_id(url) == sid
    for bad in ("https://evil.example/spreadsheets/d/" + sid, "https://docs.google.com/document/d/" + sid, "file:///etc/passwd", ""):
        with pytest.raises(gsheets.SheetError):
            gsheets.sheet_id(bad)
    assert client.post("/api/batches/from-url", json={"url": "x"}).status_code == 401
    login(client)
    xlsx = (ROOT / "sample" / "tech_fest_2026.xlsx").read_bytes()
    fetched = []

    def fake_fetch(url, limit):
        fetched.append(url)
        return xlsx, "attachment; filename*=UTF-8''Tech%20Fest%20Registrations.xlsx"

    monkeypatch.setattr(gsheets, "_fetch", fake_fetch)
    r = client.post("/api/batches/from-url", json={"url": f"https://docs.google.com/spreadsheets/d/{sid}/edit#gid=0", "name": "Tech Fest"})
    assert r.status_code == 200, r.text
    b = r.json()
    assert fetched == [f"https://docs.google.com/spreadsheets/d/{sid}/export?format=xlsx"]  # built by the app, not taken from the link
    assert b["filename"] == "Tech Fest Registrations.xlsx" and b["counts"] == {"Invalid": 2, "Pending": 14, "Total": 16}
    assert b["config"]["source_url"].startswith("https://docs.google.com/")

    monkeypatch.setattr(gsheets, "_fetch", lambda url, limit: (b"<!doctype html><title>Sign in</title>", ""))
    r = client.post("/api/batches/from-url", json={"url": f"https://docs.google.com/spreadsheets/d/{sid}/edit"})
    assert r.status_code == 400 and "Anyone with the link" in r.json()["detail"]

    def http_404(url, limit):
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
    monkeypatch.setattr(gsheets, "_fetch", http_404)
    assert "doesn't exist" in client.post("/api/batches/from-url", json={"url": f"https://docs.google.com/spreadsheets/d/{sid}"}).json()["detail"]
    r = client.post("/api/batches/from-url", json={"url": "http://169.254.169.254/latest/meta-data"})
    assert r.status_code == 400 and "Google Sheets link" in r.json()["detail"]


def test_linkedin_button_and_share_card(client):
    import io as _io
    from urllib.parse import parse_qs, urlparse
    from PIL import Image
    login(client)
    bid = upload(client)["id"]
    send(client, bid)
    rec = records(client, bid, "Sent")[0]
    client.post("/api/logout")  # the verify page is public
    page_html = client.get(f"/verify/{rec['cert_code']}").text
    import html as _html, re as _re
    link = _html.unescape(_re.search(r'href="(https://www.linkedin.com/profile/add\?[^"]+)"', page_html).group(1))
    q = {k: v[0] for k, v in parse_qs(urlparse(link).query).items()}
    assert q["startTask"] == "CERTIFICATION_NAME" and q["certId"] == rec["cert_code"]
    assert q["certUrl"].endswith(f"/verify/{rec['cert_code']}") and "AI Hackathon 2026" in q["name"] and q["issueYear"].isdigit()
    assert f'/verify/{rec["cert_code"]}/card.png' in page_html and 'property="og:title"' in page_html and "navigator.share" in page_html
    card = client.get(f"/verify/{rec['cert_code']}/card.png")
    assert card.status_code == 200 and Image.open(_io.BytesIO(card.content)).size == (1200, 630)
    fake = client.get("/verify/SS-NOTAREAL123").text
    assert "linkedin.com" not in fake and client.get("/verify/SS-NOTAREAL123/card.png").status_code == 404
    login(client)
    b = client.get(f"/api/batches/{bid}").json()
    cfg = b["config"]
    cfg["email_body"] = "Add it to LinkedIn: {{LINKEDIN_URL}}"
    for r in cfg["rules"]:
        r["email"] = "standard"
    save(client, bid, cfg)
    e = client.get(f"/api/batches/{bid}/preview/email", params={"record_id": rec["id"]}).json()
    assert "https://www.linkedin.com/profile/add?startTask=CERTIFICATION_NAME" in e["body"]


def test_event_feedback_stars(client):
    import re as _re
    from sealsend import config as _config
    login(client)
    b = upload(client)
    bid = b["id"]
    assert b["config"]["feedback"] is True  # on for new events
    send(client, bid)
    emls = sorted(Path(_config.OUTBOX_DIR).glob("*.eml"))
    msg = email.message_from_bytes(emls[-1].read_bytes(), policy=policy.default)
    text = msg.get_body(("plain",)).get_content()
    html_part = msg.get_body(("html",)).get_content()
    link = _re.search(r"(http\S+/feedback/[\w-]+)", text).group(1)
    assert "Tap a star to rate it" in text and html_part.count("?stars=") == 5
    token = link.rsplit("/", 1)[1]
    client.post("/api/logout")  # students aren't signed in
    assert "AI Hackathon 2026" in client.get(f"/feedback/{token}?stars=4").text
    assert client.post(f"/feedback/{token}", data={"stars": "6"}).status_code == 400
    assert "Thank you" in client.post(f"/feedback/{token}", data={"stars": "5", "comment": "Loved the robots"}).text
    assert client.post(f"/feedback/{token}", data={"stars": "3", "comment": "Changed my mind"}).status_code == 200  # replaces
    assert client.get("/feedback/not-a-real-token").status_code == 200 and "not valid" in client.get("/feedback/nope").text
    login(client)
    f = client.get(f"/api/batches/{bid}/feedback").json()
    assert f["count"] == 1 and f["average"] == 3 and f["distribution"] == [0, 0, 1, 0, 0] and f["asked"] >= 10
    assert f["comments"][0]["comment"] == "Changed my mind"
    csv_text = client.get(f"/api/batches/{bid}/feedback.csv").text
    names = [r["name"] for r in records(client, bid)]
    assert "Changed my mind" in csv_text and not any(n and n in csv_text for n in names)  # anonymous

    cfg = client.get(f"/api/batches/{bid}").json()["config"]
    cfg["feedback"] = False
    save(client, bid, cfg)
    rec = records(client, bid, "Sent")[0]
    assert client.post(f"/api/records/{rec['id']}/resend").status_code == 200
    wait_idle(client, bid)
    newest = max(Path(_config.OUTBOX_DIR).glob("*.eml"), key=lambda p: p.stat().st_mtime)
    assert "/feedback/" not in newest.read_text(errors="ignore")


def test_club_report(client):
    from datetime import date, timedelta
    login(client)

    def event(csv_text, name, club):
        r = client.post("/api/batches", files={"file": ("list.csv", csv_text.encode())}, data={"name": name, "club": club})
        assert r.status_code == 200, r.text
        send(client, r.json()["id"])
        return r.json()["id"]

    robo = event("Name,Student ID,Email,Department,Category\nAsha Rao,MU101,asha@mu.example.edu,CSE,Winner\n"
                 "Ben Thomas,MU102,ben@mu.example.edu,CSE,Participant\nChitra Iyer,MU103,chitra@mu.example.edu,ECE,Participant\n",
                 "Robo Day", "roboverse")
    event("Name,Student ID,Email,Department,Category\nAsha Rao,MU101,asha@mu.example.edu,CSE,Volunteer\n"
          "Dev Patel,MU104,dev@mu.example.edu,Mechanical,Participant\n", "Kala Night", "kalakriti")
    event_tf = upload(client, name="tech_fest_2026.xlsx", event="Tech Fest")["id"]
    send(client, event_tf)
    from sealsend import db as _db
    token = _db.one("SELECT feedback_token FROM records WHERE batch_id=? AND feedback_token!='' LIMIT 1", (robo,))["feedback_token"]
    client.post(f"/feedback/{token}", data={"stars": "4"})

    r = client.get("/api/reports").json()
    assert r["totals"]["events"] == 3 and r["totals"]["repeat"] == 1  # Asha came to two club events
    assert r["totals"]["participants"] == 4 + 14 and r["totals"]["certificates"] == 3 + 2 + 14
    one = client.get("/api/reports", params={"club": "roboverse"}).json()
    assert one["title"] == "Mahindra University · Roboverse" and [e["name"] for e in one["events"]] == ["Robo Day"]
    assert one["totals"]["rating"] == 4 and {d["name"]: d["count"] for d in one["departments"]} == {"CSE": 2, "ECE": 1}
    assert {c["key"] for c in one["categories"]} == {"winner", "participation"}
    assert [e["name"] for e in client.get("/api/reports", params={"club": "other"}).json()["events"]] == ["Tech Fest"]
    later = (date.today() + timedelta(days=1)).isoformat()
    assert client.get("/api/reports", params={"start": later}).json()["totals"]["events"] == 0
    assert client.get("/api/reports", params={"club": "chess"}).status_code == 400
    assert client.get("/api/reports", params={"start": "1st July"}).status_code == 400
    pdf = client.get("/api/reports.pdf", params={"club": "roboverse"})
    assert pdf.content[:4] == b"%PDF" and "Roboverse" in pdf.headers["content-disposition"]
    rows = client.get("/api/reports.csv").text.strip().splitlines()
    assert len(rows) == 1 + 3 and rows[0].lstrip("\ufeff").startswith("Date,Event,Club")
    client.post("/api/logout")
    assert client.get("/api/reports").status_code == 401


def test_help_assistant_offline_state_and_claude(client, monkeypatch):
    assert client.post("/api/assistant", json={"message": "hi"}).status_code == 401
    login(client)
    ask = lambda m, **kw: client.post("/api/assistant", json={"message": m, **kw}).json()
    r = ask("what is the app passweord")  # typo on purpose
    assert "App Password" in r["answer"] and [a["id"] for a in r["actions"]] == ["open_settings"] and r["engine"] == "offline"
    r = ask("conect gmial account")
    assert r["answer"].startswith("Right now **no email account is connected**")  # knows the app is in test mode
    assert ask("how many emails can I send per day")["answer"].startswith("You’re in **test mode**")
    r = ask("how do I add the Roboverse logo")
    assert "Mahindra University logos" in r["answer"] and "open_settings" in [a["id"] for a in r["actions"]]
    unknown = ask("tell me about the weather")
    assert unknown["actions"] == [] and unknown["suggestions"]
    assert ask("")["suggestions"]
    b = upload(client)
    assert client.post("/api/assistant", json={"message": "how do I send", "batch_id": b["id"],
                                               "history": [{"role": "user", "content": "hello"}, "junk"]}).status_code == 200

    import anthropic
    seen = {}

    class Block:
        type = "text"
        text = json.dumps({"answer": "Open **Settings** and choose Gmail.", "actions": ["open_settings", "not_a_real_action"]})

    class Fake:
        def __init__(self, **kw):
            self.beta = self
            self.messages = self
        def create(self, **kw):
            seen.update(kw)
            return type("R", (), {"stop_reason": "end_turn", "content": [Block()]})()

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(anthropic, "Anthropic", Fake)
    r = client.post("/api/assistant", json={"message": "how do I connect", "batch_id": b["id"],
                                            "history": [{"role": "assistant", "content": "Hi"}, {"role": "user", "content": "hello"},
                                                        {"role": "assistant", "content": "Hello!"}]}).json()
    assert r["engine"] == "claude" and [a["id"] for a in r["actions"]] == ["open_settings"]
    assert seen["messages"][0]["role"] == "user" and seen["messages"][-1]["content"].endswith("how do I connect")
    assert '"mail_mode": "sandbox"' in seen["system"] and "AI Hackathon 2026" in seen["system"]
    monkeypatch.setattr(Fake, "create", lambda self, **kw: (_ for _ in ()).throw(anthropic.APIConnectionError(request=None)))
    r = ask("what is the app password")
    assert r["engine"] == "offline" and "App Password" in r["answer"]


@pytest.mark.skipif(not hasattr(__import__("os"), "fork"), reason="fake tunnel tool is a shell script")
def test_share_online_with_fake_tunnel_tool(client, tmp_path, monkeypatch):
    import os
    from sealsend import tunnel
    assert client.get("/api/share").status_code == 401 and client.post("/api/share/start").status_code == 401
    login(client)
    assert client.get("/api/share").json() == {"state": "off", "url": "", "error": "", "available": True, "external_url": ""}
    fake = tmp_path / "bin"
    fake.mkdir()
    tool = fake / "cloudflared"
    tool.write_text("#!/bin/sh\necho 'INF Requesting new quick Tunnel on trycloudflare.com...'\n"
                    "echo 'INF |  https://fuzzy-cats-dance.trycloudflare.com  |'\nexec sleep 60\n")
    tool.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake}{os.pathsep}{os.environ['PATH']}")

    def wait_for(state):
        for _ in range(100):
            st = client.get("/api/share").json()
            if st["state"] == state:
                return st
            time.sleep(0.05)
        raise AssertionError(st)

    assert client.post("/api/share/start").json()["state"] in ("starting", "on")
    st = wait_for("on")
    assert st["url"] == "https://fuzzy-cats-dance.trycloudflare.com"
    me = client.get("/api/me").json()
    assert me["public_base_url"] == st["url"] and me["temporary_url"]  # QR codes now point at the public link
    proc = tunnel.share._proc
    assert client.post("/api/share/stop").json()["state"] == "off"
    assert proc.wait(timeout=5) is not None  # the tunnel process is gone
    assert client.get("/api/me").json()["public_base_url"] != st["url"]

    tool.write_text("#!/bin/sh\necho 'ERR failed to connect'\nexit 1\n")  # no link this time
    client.post("/api/share/start")
    assert "Couldn't open the public link" in wait_for("error")["error"]
    tunnel.share.stop()


def test_chaos_test_contains_every_fault(client):
    login(client)
    bid = client.post("/api/chaos").json()["batch_id"]
    wait_idle(client, bid, timeout=150)
    rep = client.get(f"/api/batches/{bid}").json()["chaos"]["report"]
    assert not [i for i in rep["items"] if not i["passed"]], rep["items"]
    assert rep["passed"] == rep["total"] == 15 and rep["wrong_attachments_delivered"] == 0 and rep["batch_finished"]


def test_bulk_parallel_generation(client, tmp_path):
    import csv
    login(client)
    p = tmp_path / "big.csv"
    with open(p, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Student Name", "Student ID", "Student Email", "Event", "Category"])
        for i in range(120):
            w.writerow([f"Student {chr(65 + i % 26)}{chr(97 + i // 26)}", f"B{i:04d}", f"s{i}@example.edu", "Bulk Fest",
                        "Winner" if i < 3 else "Participant"])
    with open(p, "rb") as f:
        bid = client.post("/api/batches", files={"file": ("big.csv", f)}, data={"name": "Bulk"}).json()["id"]
    done = send(client, bid)
    assert done["counts"] == {"Sent": 120, "Total": 120}
    assert audit(bid) == {"emails_checked": 120, "wrong": 0, "wrong_files": []}
    assert client.post(f"/api/batches/{bid}/verify-seal").json()["intact"]


def _png(color, size=(400, 200)):
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGBA", size, color).save(buf, "PNG")
    return buf.getvalue()


def test_mahindra_university_clubs(client):
    login(client)
    cat = client.get("/api/clubs").json()
    assert cat["university"]["name"] == "Mahindra University" and len(cat["clubs"]) == 10
    assert [c["name"] for c in cat["clubs"]][:3] == ["Kalakriti", "Roboverse", "Synolo"]
    assert client.get("/api/clubs/roboverse/logo.png").content[:4] == b"\x89PNG"  # placeholder before upload
    with open(ROOT / "sample" / "students.csv", "rb") as f:
        assert client.post("/api/batches", files={"file": ("s.csv", f)}, data={"name": "X", "club": "nope"}).status_code == 400
    # an event created before logos exist still works, then picks up the logos once uploaded
    with open(ROOT / "sample" / "students.csv", "rb") as f:
        b = client.post("/api/batches", files={"file": ("s.csv", f)}, data={"name": "RoboWars 2026", "club": "roboverse"}).json()
    cfg = b["config"]
    assert cfg["club"] == "roboverse" and cfg["template_id"] == "tech-hackathon"
    assert cfg["branding"]["org_name"] == "Mahindra University · Roboverse"
    for key, color in (("university", (200, 0, 0, 255)), ("roboverse", (0, 0, 200, 255))):
        r = client.post(f"/api/clubs/{key}/logo", files={"file": (f"{key}.png", _png(color))})
        assert r.status_code == 200, r.text
    assert client.post("/api/clubs/unknown/logo", files={"file": ("x.png", _png((0, 0, 0, 255)))}).status_code == 400
    cfg = client.get(f"/api/batches/{b['id']}").json()["config"]
    from sealsend import config
    from PIL import Image
    combo = Image.open(config.UPLOAD_DIR / cfg["branding"]["logo"])
    assert combo.height == 300 and combo.width > 600  # both logos side by side
    done = send(client, b["id"])
    assert done["counts"]["Sent"] == 11 and audit(b["id"])["wrong"] == 0
    rec = records(client, b["id"], "Sent")[0]
    vals = client.get(f"/api/records/{rec['id']}").json()["values"]
    assert vals["ORG_NAME"] == "Mahindra University · Roboverse"
    pv = client.get(f"/api/batches/{b['id']}/preview/email", params={"record_id": rec["id"]}).json()
    assert "Mahindra University · Roboverse" in pv["from"] and "Mahindra University · Roboverse" in pv["body"]
