"""Reading any reasonable spreadsheet, understanding its columns, validating and self-healing the data.

Built for sheets nobody prepared for us: title rows above the header, several sheets,
odd column names, names split into first/last, emails in unexpected columns, ALL CAPS names.
"""
import hashlib
import math
import re
import unicodedata
from collections import Counter
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from .templates_lib import all_tokens, render_text, required_tokens

EMAIL_RE = re.compile(
    r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)*\.[A-Za-z]{2,}$"
)
BUILTIN = {"CERT_ID", "VERIFY_URL", "LINKEDIN_URL", "ISSUE_DATE", "ISSUER", "ORG_NAME", "TITLE", "DESCRIPTION", "CATEGORY", "HONOR_PHRASE", "CATEGORY_A",
           "SIGNER1_NAME", "SIGNER1_TITLE", "SIGNER2_NAME", "SIGNER2_TITLE"}

HINTS = {
    "NAME": ["student name", "participant name", "full name", "candidate name", "name of the student", "name of participant",
             "recipient name", "attendee name", "name"],
    "ID": ["student id", "roll no", "roll number", "register number", "registration number", "reg no", "enrollment",
           "enrolment", "usn", "admission no", "employee id", "emp id", "participant id", "id"],
    "EVENT": ["event name", "event", "program", "programme", "workshop", "competition", "course name", "course"],
    "CERT_TYPE": ["certificate type", "cert type", "type of certificate"],
    "ACHIEVEMENT": ["achievement", "position", "rank", "award", "result", "prize", "place"],
    "DATE": ["event date", "date of event", "issue date", "date"],
    "DEPARTMENT": ["department", "dept", "branch", "stream"],
    "COLLEGE": ["college", "institution", "institute", "university", "school", "organisation", "organization", "company"],
    "TEAM": ["team name", "team"],
}
NOT_A_PERSON = ("father", "mother", "guardian", "parent", "team", "college", "school", "event", "institute", "company",
                "project", "mentor", "faculty", "file", "course", "program", "user", "nick", "organisation", "organization",
                "department", "university", "hospital", "city")
CATEGORY_HINTS = ["category", "certificate type", "cert type", "type", "award", "achievement", "position", "result",
                  "role", "prize", "rank", "status", "track", "level", "participation", "designation", "place"]
EMAIL_PRIMARY_HINTS = ["student email", "college email", "official email", "institute email", "university email",
                       "work email", "email id", "email address", "e mail", "email", "mail"]
EMAIL_SECONDARY_HINTS = ["personal", "alternate", "alt", "secondary", "other", "parent", "gmail", "backup"]

POPULAR_DOMAINS = [
    "gmail.com", "googlemail.com", "yahoo.com", "yahoo.co.in", "yahoo.co.uk", "ymail.com", "outlook.com",
    "hotmail.com", "live.com", "msn.com", "icloud.com", "me.com", "aol.com", "protonmail.com", "proton.me",
    "rediffmail.com", "zoho.com", "gmx.com", "mail.com",
]
TLD_FIXES = {"con": "com", "cmo": "com", "ocm": "com", "comm": "com", "coom": "com", "cm": "com", "om": "com",
             "vom": "com", "xom": "com", "ner": "net", "nte": "net", "ogr": "org", "orgg": "org", "eud": "edu"}
JUNK_NAMES = {"#n/a", "#ref!", "#value!", "#name?", "#div/0!", "#null!", "#num!", "n/a", "na", "null", "none",
              "nil", "undefined", "nan", "-", "--", "?", "test", "xxx", "name", "student name", "full name", "tbd",
              "unknown", "{{name}}", "total", "grand total"}


# ---------------------------------------------------------------- reading
def _clean_cell(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        if math.isnan(v):
            return ""
        if v.is_integer():
            return str(int(v))
    if isinstance(v, (pd.Timestamp, datetime)):
        return v.strftime("%d %B %Y")
    if isinstance(v, date):
        return v.strftime("%d %B %Y")
    s = str(v).replace(" ", " ")
    return re.sub(r"\s+", " ", s).strip()


def _grid_from_file(path: Path, sheet: str | None) -> tuple[list[list[str]], list[dict], str]:
    suffix = path.suffix.lower()
    if suffix in (".xlsx", ".xlsm"):
        books = pd.read_excel(path, sheet_name=None, header=None, dtype=object)
        sheets = []
        for name, df in books.items():
            grid = [[_clean_cell(v) for v in row] for row in df.itertuples(index=False)]
            filled = sum(1 for r in grid if any(r))
            emails = sum(1 for r in grid for v in r if "@" in v)
            sheets.append({"name": str(name), "rows": filled, "emails": emails, "grid": grid})
        if not sheets:
            raise ValueError("The workbook has no sheets")
        if sheet:
            chosen = next((s for s in sheets if s["name"] == sheet), None)
            if not chosen:
                raise ValueError(f"No sheet named “{sheet}”")
        else:  # the sheet with the most email addresses, then the most rows
            chosen = max(sheets, key=lambda s: (s["emails"] > 0, s["rows"]))
        return chosen["grid"], [{"name": s["name"], "rows": s["rows"]} for s in sheets], chosen["name"]
    if suffix == ".xls":
        raise ValueError("Old .xls format: open it in Excel or Google Sheets and save as .xlsx or .csv")
    if suffix in (".csv", ".txt", ".tsv"):
        for enc in ("utf-8-sig", "cp1252", "latin-1"):
            try:
                df = pd.read_csv(path, dtype=str, keep_default_na=False, header=None, encoding=enc, sep=None,
                                 engine="python", skip_blank_lines=False)
                grid = [[_clean_cell(v) for v in row] for row in df.itertuples(index=False)]
                return grid, [], ""
            except UnicodeDecodeError:
                continue
        raise ValueError("Could not decode the CSV file")
    raise ValueError("Unsupported file type. Upload an Excel (.xlsx) or CSV file")


def _looks_like_header_cell(v: str) -> bool:
    return bool(v) and "@" not in v and len(v) <= 60 and not re.fullmatch(r"[\d\s.,/:-]+", v) and any(ch.isalpha() for ch in v)


def detect_header(grid: list[list[str]]) -> int:
    """Index of the header row: skips title rows like 'AI Hackathon 2026 – Participants'."""
    width = max((sum(1 for v in r if v) for r in grid[:30]), default=0)
    if width == 0:
        raise ValueError("The sheet is empty")
    hint_words = ("name", "email", "mail", "id", "roll", "category", "event", "date", "type", "position", "phone", "department")
    for i, row in enumerate(grid[:20]):
        cells = [v for v in row if v]
        if len(cells) < max(2, math.ceil(width * 0.5)):
            continue
        if sum(_looks_like_header_cell(v) for v in cells) / len(cells) < 0.7:
            continue
        nxt = grid[i + 1] if i + 1 < len(grid) else []
        hinted = any(any(h in v.lower() for h in hint_words) for v in cells)
        if hinted or any("@" in v for v in nxt) or i == 0:
            return i
    return 0


def read_table(path: Path, sheet: str | None = None) -> dict:
    grid, sheets, chosen = _grid_from_file(path, sheet)
    while grid and not any(grid[-1]):
        grid.pop()
    if not grid:
        raise ValueError("The file has no data")
    h = detect_header(grid)
    header = grid[h]
    ncols = max(len(r) for r in grid)
    header = header + [""] * (ncols - len(header))
    body = [r + [""] * (ncols - len(r)) for r in grid[h + 1:]]
    keep = [j for j in range(ncols) if header[j] or any(r[j] for r in body)]
    columns, seen = [], {}
    for j in keep:
        name = header[j] or f"Column {j + 1}"
        if name.lower().startswith("unnamed:"):
            name = f"Column {j + 1}"
        if name in seen:
            seen[name] += 1
            name = f"{name} ({seen[name]})"
        else:
            seen[name] = 1
        columns.append(name)
    rows, row_numbers = [], []
    header_norm = [header[j].strip().lower() for j in keep]
    for i, r in enumerate(body):
        vals = [r[j] for j in keep]
        if not any(vals):
            continue
        if [v.strip().lower() for v in vals] == header_norm:
            continue  # header repeated (pasted twice / page breaks)
        filled = [v.strip().lower() for v in vals if v.strip()]
        if len(filled) <= 2 and any(v in ("total", "grand total", "sum", "count") or v.startswith("total ") for v in filled):
            continue  # a "Total" footer, not a student
        rows.append(dict(zip(columns, vals)))
        row_numbers.append(h + i + 2)  # spreadsheet numbering: 1-based plus header row
    if not rows:
        raise ValueError("Found a header row but no student rows below it")
    return {"columns": columns, "rows": rows, "row_numbers": row_numbers, "sheets": sheets, "sheet": chosen,
            "header_row": h + 1}


def load_table(path: Path, sheet: str | None = None) -> tuple[list[str], list[dict]]:
    t = read_table(path, sheet)
    return t["columns"], t["rows"]


# ---------------------------------------------------------------- understanding columns
def _norm(c: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", c.lower())).strip()


def _best_column(columns, hints, exclude=(), reject=()):
    norm = {c: _norm(c) for c in columns if c not in exclude and not any(r in _norm(c) for r in reject)}
    for hint in hints:
        for c, n in norm.items():
            if n == hint:
                return c
    for hint in hints:
        for c, n in norm.items():
            if re.search(rf"\b{re.escape(hint)}\b", n):
                return c
    return ""


def _values(rows, col):
    return [r.get(col, "") for r in rows if r.get(col, "")]


def email_columns(columns: list[str], rows: list[dict]) -> list[str]:
    found = []
    for c in columns:
        vals = _values(rows, c)
        if vals and sum("@" in v or "(at)" in v.lower() for v in vals) / len(vals) >= 0.4:
            found.append(c)
    for c in columns:  # a header that says email even if the column is sparse
        if c not in found and any(h in _norm(c) for h in ("email", "e mail", "mail id")):
            found.append(c)
    return found


def detect_category(columns: list[str], rows: list[dict], exclude: set) -> str:
    best, best_score = "", 0
    n = max(len(rows), 1)
    for c in columns:
        if c in exclude:
            continue
        distinct = {v.strip().lower() for v in _values(rows, c)}
        if not distinct or len(distinct) > 15 or (len(distinct) > 0.6 * n and n > 5):
            continue
        name = _norm(c)
        score = 0
        for rank, h in enumerate(CATEGORY_HINTS):
            if re.search(rf"\b{re.escape(h)}\b", name):
                score = 100 - rank
                break
        if score and score > best_score:
            best, best_score = c, score
    return best


def suggest_config(columns: list[str], rows: list[dict] | None = None) -> dict:
    rows = rows or []
    emails = email_columns(columns, rows) if rows else [c for c in columns if "mail" in c.lower()]
    primary = next((c for h in EMAIL_PRIMARY_HINTS for c in emails
                    if h in _norm(c) and not any(s in _norm(c) for s in EMAIL_SECONDARY_HINTS)), "")
    primary = primary or next((c for c in emails if not any(s in _norm(c) for s in EMAIL_SECONDARY_HINTS)), "") or (emails[0] if emails else "")
    fallback = next((c for c in emails if c != primary), "")
    used = {primary, fallback}
    mapping = {}

    first = _best_column(columns, ["first name", "given name", "firstname"], used)
    last = _best_column(columns, ["last name", "surname", "family name", "lastname"], used)
    name = _best_column(columns, HINTS["NAME"], used | {first, last}, NOT_A_PERSON)
    if name:
        mapping["NAME"] = {"type": "column", "value": name}
        used.add(name)
    elif first and last:
        mapping["NAME"] = {"type": "combine", "value": [first, last]}
        used |= {first, last}
    elif first:
        mapping["NAME"] = {"type": "column", "value": first}
        used.add(first)

    reject = {"ID": ("email", "mail", "phone", "mobile"), "EVENT": ("date", "mail", "venue", "time"),
              "DATE": ("mail", "birth", "dob"), "COLLEGE": ("mail",)}
    for ph in ["ID", "DATE", "EVENT", "CERT_TYPE", "ACHIEVEMENT", "DEPARTMENT", "COLLEGE", "TEAM"]:
        col = _best_column(columns, HINTS[ph], used, reject.get(ph, ("email", "mail")))
        if col:
            mapping[ph] = {"type": "column", "value": col}
            used.add(col)
    category = detect_category(columns, rows, {primary, fallback, mapping.get("ID", {}).get("value"),
                                              mapping.get("DATE", {}).get("value"), mapping.get("EVENT", {}).get("value")}
                               | ({name} if name else set())) if rows else ""
    if category and "ACHIEVEMENT" not in mapping and "CERT_TYPE" not in mapping:
        mapping["ACHIEVEMENT"] = {"type": "column", "value": category}
    return {"mapping": mapping, "email_column": primary, "fallback_column": fallback, "category_column": category}


# ---------------------------------------------------------------- categories
STYLES = {
    "winner": {"title": "Certificate of Excellence", "email": "winner",
               "description": "for {{HONOR_PHRASE}} at {{EVENT}}[[, held on {{DATE}}]]"},
    "participation": {"title": "Certificate of Participation", "email": "participation",
                      "description": "for participating in {{EVENT}}[[, held on {{DATE}}]]"},
    "appreciation": {"title": "Certificate of Appreciation", "email": "appreciation",
                     "description": "in appreciation of valuable contribution to {{EVENT}}[[, held on {{DATE}}]]"},
    "completion": {"title": "Certificate of Completion", "email": "completion",
                   "description": "for successfully completing {{EVENT}}[[ on {{DATE}}]]"},
}
STYLE_WORDS = [
    ("winner", r"first|second|third|1st|2nd|3rd|winner|won|runner|champion|gold|silver|bronze|prize|best|award|topper|achiev|"
               r"merit|distinction|excellence|finalist|position|place|rank|honou?r"),
    ("appreciation", r"volunteer|organi[sz]er|organi[sz]ing|coordinator|co-ordinator|mentor|judge|jury|speaker|guest|"
                     r"sponsor|staff|faculty|host|committee|core|lead|ambassador|anchor|support|crew"),
    ("completion", r"intern|trainee|completion|completed|course|graduate|certified"),
    ("participation", r"particip|attend|delegate|member|registered|player|contestant|competitor|entrant"),
]


def honor_phrase(honor: str) -> str:
    """'Winner' -> 'being the Winner'; 'First Place' -> 'securing First Place'. Reads right on a certificate."""
    h = honor.strip()
    if re.match(r"(?i)(the\s+)?(winner|runner[\s-]*up|champion|finalist|topper|semi[\s-]*finalist)", h):
        return "being the " + re.sub(r"(?i)^the\s+", "", h)
    return f"securing {h}"


def with_article(word: str) -> str:
    """'Volunteer' -> 'a Volunteer', 'Organizer' -> 'an Organizer'; plurals and 'the …' stay as they are."""
    w = word.strip()
    if not w or re.match(r"(?i)^(a|an|the)\s", w) or (w.lower().endswith("s") and not w.lower().endswith("ss")):
        return w
    low = w.lower()
    vowel = low[0] in "aeio" or (low[0] == "u" and not re.match(r"u(ni|se|su|ra|re|ro|t)", low)) or low.startswith(("hono", "hour"))
    return ("an " if vowel else "a ") + w


def classify(value: str) -> str:
    v = value.lower()
    if re.search(r"particip|attend", v):
        return "participation"
    for style, pattern in STYLE_WORDS:
        if re.search(rf"\b(?:{pattern})", v):
            return style
    return "participation"


def guess_event_template(text: str) -> str:
    from .templates_lib import BUILTINS
    t = (text or "").lower()
    for tpl in BUILTINS:
        if any(re.search(rf"\b{re.escape(k)}", t) for k in tpl.get("keywords", [])) and tpl["id"] not in (
                "gold-excellence", "participation-clean", "mono-minimal"):
            return tpl["id"]
    return "university-classic"


def suggest_categories(column: str, rows: list[dict], base_template: str) -> list[dict]:
    """One rule per category value with a sensible template, title, wording and email."""
    counts = Counter(r.get(column, "").strip() for r in rows if r.get(column, "").strip())
    winner_tpl = base_template if base_template in ("sports-champion", "school-stars") else "gold-excellence"
    order = {"winner": 0, "completion": 1, "appreciation": 2, "participation": 3}

    def podium(v):
        v = v.lower()
        for rank, pat in enumerate((r"\b(first|1st|winner|gold|champion)", r"\b(second|2nd|runner|silver)", r"\b(third|3rd|bronze)")):
            if re.search(pat, v):
                return rank
        return 3

    rules = []
    for value, n in sorted(counts.items(), key=lambda kv: (order[classify(kv[0])], podium(kv[0]), -kv[1])):
        style = classify(value)
        rules.append({"label": value, "match": {"column": column, "op": "any_of", "values": [value]},
                      "template": winner_tpl if style == "winner" else base_template, "style": style,
                      "title": STYLES[style]["title"], "email": STYLES[style]["email"], "description": "",
                      "email_subject": "", "email_body": ""})
    return rules


# ---------------------------------------------------------------- values
def resolve_values(row: dict, mapping: dict, fix_caps: bool = False) -> dict:
    values = {}
    for ph, src in mapping.items():
        if not src:
            continue
        kind = src.get("type")
        if kind == "text":
            values[ph.upper()] = src.get("value", "")
        elif kind == "combine":
            values[ph.upper()] = " ".join(row.get(c, "").strip() for c in src.get("value", []) if row.get(c, "").strip())
        else:
            values[ph.upper()] = row.get(src.get("value", ""), "")
    if fix_caps and values.get("NAME"):
        values["NAME"] = smart_title(values["NAME"])
    return values


def smart_title(name: str) -> str:
    """AISHA AL-RAHMAN -> Aisha Al-Rahman. Only applied to all-caps or all-lowercase names."""
    if not (name.isupper() or name.islower()):
        return name
    def cap(word):
        return "-".join("'".join(p[:1].upper() + p[1:].lower() for p in part.split("'")) for part in word.split("-"))
    return " ".join(cap(w) for w in name.split())


def render(text: str, values: dict) -> str:
    return render_text(text, values)


def placeholders_in(*texts: str) -> list[str]:
    found = []
    for t in texts:
        for m in all_tokens(t):
            if m not in found:
                found.append(m)
    return found


def required_in(*texts: str) -> list[str]:
    found = []
    for t in texts:
        for m in required_tokens(t):
            if m not in found:
                found.append(m)
    return found


# ---------------------------------------------------------------- names
def check_name(name: str) -> tuple[list[str], list[str]]:
    errors, warnings = [], []
    if not name:
        return ["Missing name"], []
    low = name.lower().strip()
    if low in JUNK_NAMES or low.startswith("#"):
        errors.append(f"Name looks like a placeholder or spreadsheet error: “{name}”")
    elif "@" in name:
        errors.append(f"Name looks like an email address: “{name}”")
    elif any(ch.isdigit() for ch in name):
        errors.append(f"Name contains digits: “{name}”")
    elif len(name) < 2:
        errors.append(f"Name is too short: “{name}”")
    elif len(name) > 70:
        errors.append(f"Name is too long ({len(name)} characters)")
    else:
        bad = {ch for ch in name if not (unicodedata.category(ch)[0] in "LM" or ch in " .'-,’")}
        if bad:
            errors.append(f"Name has unexpected characters {' '.join(sorted(bad))}: “{name}”")
    if not errors and len(name) > 3 and name == name.upper() and any(c.isalpha() for c in name):
        warnings.append("Name is ALL CAPS")
    elif not errors and name == name.lower():
        warnings.append("Name is all lowercase")
    return errors, warnings


# ---------------------------------------------------------------- emails
def valid_email(addr: str) -> bool:
    return bool(addr) and len(addr) <= 254 and ".." not in addr and bool(EMAIL_RE.match(addr))


def normalize_email(raw: str) -> str:
    """Safe, lossless cleanup: whitespace, mailto:, <>, trailing punctuation, (at), domain case."""
    s = (raw or "").strip()
    s = re.sub(r"^mailto:", "", s, flags=re.I).strip()
    s = s.strip("<>").rstrip(".,;:").strip("<>").strip()
    s = re.sub(r"\s*[\(\[]\s*at\s*[\)\]]\s*", "@", s, flags=re.I)
    s = re.sub(r"\s*@\s*", "@", s)
    if s.count("@") == 1:
        local, domain = s.split("@")
        domain = domain.strip().lower().replace(",", ".")
        s = f"{local.strip()}@{domain}"
    return s


def split_multi(raw: str) -> list[str]:
    """'a@x.com; b@y.com' -> both. Only the first is used for delivery; the rest is reported."""
    return [p for p in re.split(r"[;,/\s]+", raw or "") if "@" in p]


def _osa(a: str, b: str) -> int:
    d = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i in range(len(a) + 1):
        d[i][0] = i
    for j in range(len(b) + 1):
        d[0][j] = j
    for i in range(1, len(a) + 1):
        for j in range(1, len(b) + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                d[i][j] = min(d[i][j], d[i - 2][j - 2] + 1)
    return d[-1][-1]


def _risky_substitution(a: str, b: str) -> bool:
    """A single swapped letter in a short domain (lime.com vs live.com) is often a different real domain."""
    return len(a) == len(b) and len(a) < 9 and sum(x != y for x, y in zip(a, b)) == 1


def fix_email_typo(addr: str, sheet_domains: Counter) -> tuple[str, str]:
    """Return (fixed_address, description) or ("", "") if no confident fix exists."""
    if addr.count("@") != 1:
        return "", ""
    local, domain = addr.split("@")
    if not local or not domain:
        return "", ""
    known = set(POPULAR_DOMAINS) | {d for d, n in sheet_domains.items() if n >= 3}
    if domain in known or sheet_domains.get(domain, 0) >= 2:
        return "", ""  # a domain several students use is real, not a typo
    candidate = ""
    scored = sorted((_osa(domain, k), k) for k in known)
    if scored:
        dist = scored[0][0]
        best = [k for d, k in scored if d == dist]
        limit = 2 if len(domain) >= 10 else 1
        if dist <= limit and len(best) == 1 and not _risky_substitution(domain, best[0]):
            candidate = best[0]
    base, _, tld = domain.rpartition(".")
    if not candidate and base and tld not in ("com", "org", "net", "edu", "in", "co", "uk", "io", "ac", "gov"):
        same_base = [k for k in known if k.rpartition(".")[0] == base]
        if len(same_base) == 1:
            candidate = same_base[0]
    if not candidate and base and tld in TLD_FIXES:
        candidate = f"{base}.{TLD_FIXES[tld]}"
    if not candidate:
        return "", ""
    fixed = f"{local}@{candidate}"
    if candidate == domain or not valid_email(fixed):
        return "", ""
    return fixed, f"{domain} → {candidate}"


def dedupe_key(values: dict, email: str) -> str:
    identity = (values.get("ID") or email or values.get("NAME", "")).strip().lower()
    parts = [identity] + [values.get(k, "").strip().lower() for k in ("EVENT", "CERT_TYPE", "ACHIEVEMENT")]
    return hashlib.sha1("|".join(parts).encode()).hexdigest()


def pick_email(primary_raw: str, fallback_raw: str, primary_col: str, fallback_col: str,
               auto_fix: bool, sheet_domains: Counter) -> dict:
    """Best deliverable address. Real addresses first, typo guesses last."""
    notes = []
    cands = []
    for raw, col in ((primary_raw, primary_col), (fallback_raw, fallback_col)):
        parts = split_multi(raw)
        if len(parts) > 1:
            notes.append(f"{col} has {len(parts)} addresses; using the first")
            raw = parts[0]
        cands.append((raw, col))
    for raw, col in cands:
        norm = normalize_email(raw)
        if raw and valid_email(norm):
            if auto_fix:
                fixed, desc = fix_email_typo(norm, sheet_domains)
                if fixed:
                    return {"email": fixed, "source": col, "fix": f"typo {desc}", "notes": notes}
            fix = f"cleaned “{raw}”" if norm != raw.strip() else ""
            return {"email": norm, "source": col, "fix": fix, "notes": notes}
    if auto_fix:
        for raw, col in cands:
            norm = normalize_email(raw)
            if raw:
                fixed, desc = fix_email_typo(norm, sheet_domains)
                if fixed:
                    return {"email": fixed, "source": col, "fix": f"typo {desc}", "notes": notes}
    return {"email": "", "source": "", "fix": "", "notes": notes}


def validate(rows: list[dict], cfg: dict, required: list[str], row_numbers: list[int] | None = None) -> list[dict]:
    mapping = cfg.get("mapping", {})
    email_col = cfg.get("email_column", "")
    fallback_col = cfg.get("fallback_column", "")
    auto_fix = cfg.get("auto_fix_emails", True)
    claim_missing = cfg.get("claim_links", True)
    fix_caps = cfg.get("fix_caps", False)
    required = [p for p in required if p not in BUILTIN and p != "NAME"]
    row_numbers = row_numbers or [i + 2 for i in range(len(rows))]

    sheet_domains = Counter()
    for row in rows:
        for col in (email_col, fallback_col):
            norm = normalize_email(row.get(col, "")) if col else ""
            if valid_email(norm):
                sheet_domains[norm.split("@")[1]] += 1

    results, seen_id, seen_email = [], {}, {}
    for row, sheet_row in zip(rows, row_numbers):
        values = resolve_values(row, mapping, fix_caps)
        errors, warnings = [], []
        name = values.get("NAME", "").strip()
        if not mapping.get("NAME"):
            errors.append("Choose which column holds the student's name")
        else:
            e, w = check_name(name)
            errors += e
            warnings += w
        for ph in required:
            if not mapping.get(ph):
                errors.append(f"{{{{{ph}}}}} is used but not connected to a column")
            elif not values.get(ph, "").strip():
                errors.append(f"Missing {ph.replace('_', ' ').title()}")

        primary = row.get(email_col, "").strip() if email_col else ""
        fallback = row.get(fallback_col, "").strip() if fallback_col else ""
        picked = pick_email(primary, fallback, email_col, fallback_col, auto_fix, sheet_domains)
        email, delivery = picked["email"], "email"
        warnings += picked["notes"]
        if picked["fix"]:
            warnings.append(f"Auto-fixed email: {picked['fix']}")
        if email and picked["source"] == fallback_col and fallback_col:
            warnings.append(f"Using fallback email ({'invalid' if primary else 'missing'} {email_col})")
        if not email_col:
            errors.append("Choose which column holds the email address")
        elif not email:
            problem = f"Invalid email “{primary or fallback}”" if (primary or fallback) else "Missing email"
            if claim_missing and not errors:
                delivery = "claim"
                warnings.append(f"{problem}: certificate will be issued through a claim link")
            else:
                errors.append(problem)

        sid = values.get("ID", "").strip()
        if sid:
            if sid.lower() in seen_id:
                errors.append(f"Duplicate of row {seen_id[sid.lower()]} (same ID {sid})")
            else:
                seen_id[sid.lower()] = sheet_row
        if email and not any(e.startswith("Duplicate") for e in errors):
            ekey = (email.lower(), name.lower())
            if ekey in seen_email:
                errors.append(f"Duplicate of row {seen_email[ekey]} (same name and email)")
            else:
                seen_email[ekey] = sheet_row

        if errors:
            delivery = "email"
        results.append({
            "name": name, "student_id": sid, "email": email, "email_source": picked["source"],
            "email_fix": picked["fix"], "delivery": delivery, "errors": errors, "warnings": warnings,
            "dedupe_key": dedupe_key(values, email), "values": values,
        })
    return results


def safe_filename(text: str, max_len: int = 120) -> str:
    text = re.sub(r"[^\w\s.-]", "", text, flags=re.UNICODE)
    text = re.sub(r"\s+", "_", text.strip())
    text = re.sub(r"_+", "_", text).strip("._")
    return text[:max_len] or "certificate"
