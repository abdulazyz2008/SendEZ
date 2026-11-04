"""SQLite storage: batches, student records, the send log and the event ledger."""
import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone

from . import config

_lock = threading.RLock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    filename TEXT NOT NULL,
    created_at TEXT NOT NULL,
    columns_json TEXT NOT NULL,
    config_json TEXT NOT NULL DEFAULT '{}',
    job TEXT NOT NULL DEFAULT '',
    seal_json TEXT NOT NULL DEFAULT '',
    is_chaos INTEGER NOT NULL DEFAULT 0,
    chaos_json TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id INTEGER NOT NULL REFERENCES batches(id),
    row_no INTEGER NOT NULL,
    data_json TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    student_id TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL DEFAULT '',
    email_source TEXT NOT NULL DEFAULT '',
    email_fix TEXT NOT NULL DEFAULT '',
    delivery TEXT NOT NULL DEFAULT 'email',
    theme TEXT NOT NULL DEFAULT '',
    rule_label TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'Pending',
    errors TEXT NOT NULL DEFAULT '',
    warnings TEXT NOT NULL DEFAULT '',
    cert_code TEXT NOT NULL DEFAULT '',
    cert_file TEXT NOT NULL DEFAULT '',
    pdf_sha256 TEXT NOT NULL DEFAULT '',
    mapping_lock TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL DEFAULT '',
    signature TEXT NOT NULL DEFAULT '',
    leaf_hash TEXT NOT NULL DEFAULT '',
    claim_token TEXT NOT NULL DEFAULT '',
    claim_attempts INTEGER NOT NULL DEFAULT 0,
    claimed_at TEXT NOT NULL DEFAULT '',
    dedupe_key TEXT NOT NULL DEFAULT '',
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT NOT NULL DEFAULT '',
    sent_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_records_batch ON records(batch_id, status);
CREATE UNIQUE INDEX IF NOT EXISTS idx_records_code ON records(cert_code) WHERE cert_code != '';
CREATE UNIQUE INDEX IF NOT EXISTS idx_records_claim ON records(claim_token) WHERE claim_token != '';
CREATE TABLE IF NOT EXISTS send_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    record_id INTEGER NOT NULL,
    dedupe_key TEXT NOT NULL,
    email TEXT NOT NULL,
    attachment TEXT NOT NULL,
    result TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_send_log_key ON send_log(dedupe_key, result);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id INTEGER NOT NULL,
    record_id INTEGER,
    at TEXT NOT NULL,
    level TEXT NOT NULL,
    kind TEXT NOT NULL,
    message TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_batch ON events(batch_id, id);
CREATE INDEX IF NOT EXISTS idx_events_record ON events(record_id, id);
CREATE TABLE IF NOT EXISTS feedback (
    record_id INTEGER PRIMARY KEY,
    batch_id INTEGER NOT NULL,
    stars INTEGER NOT NULL,
    comment TEXT NOT NULL DEFAULT '',
    at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_feedback_batch ON feedback(batch_id);
CREATE TABLE IF NOT EXISTS templates (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL,
    file TEXT NOT NULL,
    page_w REAL NOT NULL,
    page_h REAL NOT NULL,
    fields_json TEXT NOT NULL,
    default_title TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
"""

# Columns added after the first release: added in place so existing data folders keep working.
MIGRATIONS = {
    "batches": {"sheet": "TEXT NOT NULL DEFAULT ''", "pause_reason": "TEXT NOT NULL DEFAULT ''",
                "seal_json": "TEXT NOT NULL DEFAULT ''", "is_chaos": "INTEGER NOT NULL DEFAULT 0",
                "chaos_json": "TEXT NOT NULL DEFAULT ''", "send_at": "TEXT NOT NULL DEFAULT ''",
                "send_kind": "TEXT NOT NULL DEFAULT ''"},
    "records": {"category": "TEXT NOT NULL DEFAULT ''", "title": "TEXT NOT NULL DEFAULT ''",
                "feedback_token": "TEXT NOT NULL DEFAULT ''"},
    "send_log": {"account": "TEXT NOT NULL DEFAULT ''"},
}


def _migrate(c: sqlite3.Connection) -> None:
    for table, cols in MIGRATIONS.items():
        have = {r[1] for r in c.execute(f"PRAGMA table_info({table})")}
        for col, ddl in cols.items():
            if col not in have:
                c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
    c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_records_feedback ON records(feedback_token) WHERE feedback_token != ''")
    c.commit()


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


_conn = None


def conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(config.DB_PATH, check_same_thread=False, timeout=30)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA foreign_keys=ON")
        _conn.executescript(SCHEMA)
        _migrate(_conn)
    return _conn


def reset_connection() -> None:
    """Used by tests that point SEALSEND_DATA_DIR somewhere new."""
    global _conn
    if _conn is not None:
        _conn.close()
    _conn = None


@contextmanager
def tx():
    with _lock:
        c = conn()
        try:
            yield c
            c.commit()
        except Exception:
            c.rollback()
            raise


def query(sql: str, params=()) -> list[dict]:
    with _lock:
        return [dict(r) for r in conn().execute(sql, params).fetchall()]


def one(sql: str, params=()):
    rows = query(sql, params)
    return rows[0] if rows else None


def get_batch(batch_id: int):
    b = one("SELECT * FROM batches WHERE id=?", (batch_id,))
    if b:
        b["columns"] = json.loads(b.pop("columns_json"))
        b["config"] = json.loads(b.pop("config_json"))
        b["seal"] = json.loads(b.pop("seal_json") or "null")
        b["chaos"] = json.loads(b.pop("chaos_json") or "null")
    return b


def get_record(record_id: int):
    return one("SELECT * FROM records WHERE id=?", (record_id,))


def save_config(batch_id: int, cfg: dict) -> None:
    with tx() as c:
        c.execute("UPDATE batches SET config_json=? WHERE id=?", (json.dumps(cfg), batch_id))


def set_batch(batch_id: int, **fields) -> None:
    cols = ", ".join(f"{k}=?" for k in fields)
    with tx() as c:
        c.execute(f"UPDATE batches SET {cols} WHERE id=?", (*fields.values(), batch_id))


def update_record(record_id: int, **fields) -> None:
    fields["updated_at"] = now()
    cols = ", ".join(f"{k}=?" for k in fields)
    with tx() as c:
        c.execute(f"UPDATE records SET {cols} WHERE id=?", (*fields.values(), record_id))


def record_data(rec: dict) -> dict:
    return json.loads(rec["data_json"])


def log(batch_id: int, record_id, level: str, kind: str, message: str) -> None:
    """Append-only ledger. level: info | ok | warn | block | heal | error."""
    with tx() as c:
        c.execute("INSERT INTO events (batch_id, record_id, at, level, kind, message) VALUES (?,?,?,?,?,?)",
                  (batch_id, record_id, now(), level, kind, message))


def status_counts(batch_id: int) -> dict:
    rows = query("SELECT status, COUNT(*) n FROM records WHERE batch_id=? GROUP BY status", (batch_id,))
    counts = {r["status"]: r["n"] for r in rows}
    counts["Total"] = sum(counts.values())
    return counts
