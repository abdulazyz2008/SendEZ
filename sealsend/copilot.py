"""AI rules copilot: plain English -> template/email rules.

"winners get gold template, others participation" becomes ordered rules such as
  1. Achievement is one of [First Place, Second Place, ...]  -> gold theme + winner email
  2. everyone else                                            -> participation theme + thank-you email

Claude (structured output) is used when ANTHROPIC_API_KEY is set; an offline parser handles
the common phrasings otherwise, so the feature works on a laptop with no network. Every
proposal is shown with match counts and must be applied by the organizer.
"""
import json
import os
import re

from . import config
from . import templates_lib as T
from .data import STYLES
from .mailer import PRESETS

OPS = ("any_of", "contains_any", "always")
THEME_WORDS = {
    "gold-excellence": ["gold", "golden", "winner template", "premium", "excellence"],
    "participation-clean": ["participation"],
    "university-classic": ["classic", "university", "academic", "navy", "standard template", "default template"],
    "mono-minimal": ["mono", "monochrome", "black and white", "black & white", "b&w", "minimal"],
    "tech-hackathon": ["tech", "hackathon", "coding"],
    "corporate-modern": ["corporate", "modern", "business"],
    "school-stars": ["school", "kids", "stars"],
    "sports-champion": ["sports", "sport", "champion"],
    "medical-cme": ["medical", "cme", "health"],
    "ngo-appreciation": ["appreciation", "volunteer template", "ngo", "green"],
    "workshop-minimal": ["workshop", "seminar"],
    "arts-ornate": ["arts", "ornate", "cultural", "burgundy"],
    "government-official": ["official", "government", "formal"],
    "internship-completion": ["internship", "completion"],
}
THEMES = {t["id"]: {"label": t["name"], "description": t["description"]} for t in T.BUILTINS}
OTHERS_WORDS = ["others", "other students", "everyone else", "everybody else", "the rest", "rest of",
                "remaining", "all other", "otherwise", "everyone", "everybody", "all students", "default"]
WINNER_GROUP = ["winner", "winners", "won", "top 3", "top three", "podium", "prize", "placed", "ranked",
                "toppers", "position holders", "medal"]
WINNER_TOKENS = ["first", "second", "third", "1st", "2nd", "3rd", "winner", "runner", "champion", "gold",
                 "silver", "bronze", "prize", "best", "award", "place", "position", "rank"]
STOP = {"place", "award", "the", "of", "in", "for", "and", "a", "an", "to", "certificate", "prize", "position"}


def column_profile(columns: list[str], rows: list[dict], limit: int = 15) -> dict:
    """Distinct values of low-cardinality columns: what rules can sensibly test."""
    prof = {}
    for col in columns:
        vals = []
        for r in rows:
            v = r.get(col, "").strip()
            if v and v not in vals:
                vals.append(v)
                if len(vals) > limit:
                    break
        if 1 <= len(vals) <= limit:
            prof[col] = vals
    return prof


def matches(rule: dict, row: dict) -> bool:
    m = rule.get("match") or {}
    op = m.get("op", "always")
    if op == "always":
        return True
    cell = row.get(m.get("column", ""), "").strip().lower()
    vals = [v.strip().lower() for v in m.get("values", []) if v.strip()]
    if op == "any_of":
        return cell in vals
    if op == "contains_any":
        return any(v in cell for v in vals)
    return False


def evaluate(rules: list[dict], row: dict) -> dict | None:
    for rule in rules or []:
        if matches(rule, row):
            return rule
    return None


def count_matches(rules: list[dict], rows: list[dict]) -> list[int]:
    counts = [0] * len(rules)
    for row in rows:
        for i, rule in enumerate(rules):
            if matches(rule, row):
                counts[i] += 1
                break
    return counts


def sanitize(rules: list[dict], columns: list[str]) -> tuple[list[dict], list[str]]:
    """Validate rules from any source (user, offline parser, Claude). Drops anything malformed."""
    clean, notes = [], []
    for r in rules if isinstance(rules, list) else []:
        if not isinstance(r, dict):
            continue
        theme = str(r.get("template") or r.get("theme") or "").lower()
        theme = T.LEGACY.get(theme, theme)
        if not T.get_template(theme):
            notes.append(f"Ignored a rule with unknown template “{theme}”")
            continue
        m = r.get("match") or {}
        op = m.get("op", "always")
        if op not in OPS:
            notes.append(f"Ignored a rule with unknown condition “{op}”")
            continue
        column = m.get("column", "") if op != "always" else ""
        values = [str(v)[:120] for v in (m.get("values") or []) if str(v).strip()][:50]
        if op != "always" and (column not in columns or not values):
            notes.append(f"Ignored a rule on unknown column “{column}”" if column not in columns
                         else "Ignored a rule with no values")
            continue
        email = r.get("email", "standard")
        if email not in PRESETS and email != "custom":
            email = "standard"
        style = r.get("style") if r.get("style") in STYLES else {"winner": "winner", "appreciation": "appreciation",
                                                                    "completion": "completion"}.get(email, "participation")
        clean.append({
            "label": str(r.get("label", ""))[:80] or (f"{column} rule" if column else "Everyone else"),
            "match": {"column": column, "op": op, "values": values},
            "template": theme, "email": email, "style": style,
            "title": str(r.get("title") or STYLES[style]["title"])[:80],
            "description": str(r.get("description", ""))[:300],
            "email_subject": str(r.get("email_subject", ""))[:300] if email == "custom" else "",
            "email_body": str(r.get("email_body", ""))[:5000] if email == "custom" else "",
        })
    # An "always" rule ends evaluation; anything after it is unreachable.
    for i, r in enumerate(clean):
        if r["match"]["op"] == "always" and i < len(clean) - 1:
            notes.append("Moved the catch-all rule to the end so the specific rules can apply")
            clean.append(clean.pop(i))
            break
    return clean, notes


# ---------------------------------------------------------------- offline parser
def _theme_in(text: str) -> str:
    best, pos = "", 10 ** 6
    for theme, words in THEME_WORDS.items():
        for w in words:
            i = text.find(w)
            if i != -1 and i < pos:
                best, pos = theme, i
    return best


def _email_in(text: str, theme: str) -> str:
    if re.search(r"thank|appreciat", text):
        return "participation"
    if re.search(r"congrat|celebrat|winner email", text):
        return "winner"
    return {"gold-excellence": "winner", "participation-clean": "participation", "ngo-appreciation": "appreciation",
            "internship-completion": "completion"}.get(theme, "standard")


def _words(s: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", s.lower()))


def parse_local(text: str, columns: list[str], rows: list[dict], mapping: dict) -> dict:
    profile = column_profile(columns, rows)
    preferred = [mapping.get(k, {}).get("value") for k in ("ACHIEVEMENT", "CERT_TYPE")
                 if mapping.get(k, {}).get("type") == "column"]
    search_cols = [c for c in preferred if c in profile] + [c for c in profile if c not in preferred]
    group_col = next((c for c in preferred if c in columns), "")

    clauses = re.split(r"[;\n]|,|\.\s+|\bwhile\b|\bbut\b|\bwhereas\b|\band\s+(?=(?:the\s+)?(?:others|everyone|everybody|rest|all|remaining))",
                       text.lower())
    rules, notes = [], []
    for clause in (c.strip(" .") for c in clauses):
        if not clause:
            continue
        split = re.split(r"\b(?:gets?|receives?|uses?|should get|will get|get the|=>|->|:)\b|→|=>|->|:", clause, maxsplit=1)
        group, action = (split[0], split[1]) if len(split) == 2 else (clause, clause)
        theme = _theme_in(action) or _theme_in(clause)
        if not theme:
            notes.append(f"No template named in “{clause}” (try gold, classic, participation or mono)")
            continue
        email = _email_in(clause, theme)
        g = group.strip()
        if any(re.search(rf"\b{re.escape(w)}\b", g) for w in OTHERS_WORDS) or not g or g == clause and _theme_in(g) and len(_words(g)) <= 2:
            rules.append({"label": "Everyone else", "match": {"op": "always"}, "theme": theme, "email": email})
            continue
        # 1) words that name actual values in the sheet ("first place", "second and third")
        found = None
        gw = _words(g)
        for col in search_cols:
            vals = [v for v in profile[col] if (_words(v) - STOP) and (_words(v) - STOP) <= gw]
            if vals:
                found = (col, vals)
                break
        if found:
            rules.append({"label": f"{found[0]}: {', '.join(found[1])}"[:80],
                          "match": {"column": found[0], "op": "any_of", "values": found[1]},
                          "theme": theme, "email": email})
            continue
        # 2) "winners" -> every achievement value that reads like a placing or award
        if any(re.search(rf"\b{re.escape(w)}\b", g) for w in WINNER_GROUP):
            col = group_col if group_col in profile else next(iter(search_cols), "")
            vals = [v for v in profile.get(col, []) if _words(v) & set(WINNER_TOKENS) and "participation" not in v.lower()]
            if col and vals:
                rules.append({"label": "Winners", "match": {"column": col, "op": "any_of", "values": vals},
                              "theme": theme, "email": email})
            elif col:
                rules.append({"label": "Winners", "match": {"column": col, "op": "contains_any",
                              "values": ["first", "second", "third", "winner", "1st", "2nd", "3rd"]},
                              "theme": theme, "email": email})
            else:
                notes.append("Could not find an Achievement column to identify winners")
            continue
        # 3) "participants"
        if re.search(r"particip", g):
            col = group_col if group_col in profile else next(iter(search_cols), "")
            vals = [v for v in profile.get(col, []) if "particip" in v.lower()]
            if col and vals:
                rules.append({"label": "Participants", "match": {"column": col, "op": "any_of", "values": vals},
                              "theme": theme, "email": email})
                continue
        notes.append(f"Couldn’t tell which students “{g.strip()}” means. Name a value from the sheet, e.g. “First Place”")
    return {"rules": rules, "notes": notes, "engine": "offline"}


# ---------------------------------------------------------------- Claude
RULES_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["rules", "notes"],
    "properties": {
        "rules": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["label", "match", "theme", "email", "email_subject", "email_body"],
            "properties": {
                "label": {"type": "string"},
                "match": {"type": "object", "additionalProperties": False, "required": ["column", "op", "values"],
                          "properties": {"column": {"type": "string"},
                                         "op": {"type": "string", "enum": list(OPS)},
                                         "values": {"type": "array", "items": {"type": "string"}}}},
                "theme": {"type": "string"},
                "email": {"type": "string", "enum": list(PRESETS) + ["custom"]},
                "email_subject": {"type": "string"},
                "email_body": {"type": "string"},
            }}},
        "notes": {"type": "array", "items": {"type": "string"}},
    },
}

SYSTEM = """You configure a certificate-issuing tool. Convert the organizer's instruction into ordered rules.
Rules are checked top to bottom; the first match decides the student's certificate theme and email.
- match.op "any_of": the cell in match.column equals one of match.values (case-insensitive). Prefer this, using exact values from the sheet.
- match.op "contains_any": the cell contains any of match.values.
- match.op "always": catch-all for "others / everyone else"; column "" and values []. Put it last.
Templates (use the id as theme): {themes}.
Email presets: {presets}. Use "custom" only if the organizer dictates wording; then fill email_subject/email_body using placeholders like {{{{NAME}}}}, {{{{EVENT}}}}, {{{{ACHIEVEMENT}}}}, {{{{VERIFY_URL}}}}, {{{{CERT_ID}}}}. Otherwise leave them "".
Only use column names and values that appear in the sheet profile. If part of the instruction is ambiguous, say so in notes instead of guessing."""


def parse_claude(text: str, columns: list[str], rows: list[dict], mapping: dict) -> dict:
    import anthropic

    profile = column_profile(columns, rows)
    themes = "; ".join(f"{k} ({v['description']})" for k, v in THEMES.items())
    from . import db
    themes += "".join(f"; {r['id']} (imported design “{r['name']}”)" for r in db.query("SELECT id, name FROM templates"))
    presets = "; ".join(f"{k} ({v['label']})" for k, v in PRESETS.items())
    mapped = {k: v.get("value") for k, v in mapping.items() if v.get("type") == "column"}
    client = anthropic.Anthropic(timeout=45.0, max_retries=1)
    response = client.beta.messages.create(
        model=config.COPILOT_MODEL,
        max_tokens=4000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": RULES_SCHEMA}},
        system=SYSTEM.format(themes=themes, presets=presets),
        messages=[{"role": "user", "content": json.dumps({
            "instruction": text, "columns": columns, "placeholder_mapping": mapped,
            "sheet_profile_distinct_values": profile, "student_count": len(rows)}, ensure_ascii=False)}],
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("The model declined this request")
    if response.stop_reason == "max_tokens":
        raise RuntimeError("The model's answer was cut off")
    body = next((b.text for b in response.content if b.type == "text"), "")
    data = json.loads(body)
    return {"rules": data.get("rules", []), "notes": data.get("notes", []), "engine": f"Claude ({config.COPILOT_MODEL})"}


def suggest(text: str, columns: list[str], rows: list[dict], mapping: dict) -> dict:
    text = (text or "").strip()[:2000]
    if not text:
        return {"rules": [], "notes": ["Describe who gets which template"], "engine": "offline", "counts": []}
    use_claude = config.COPILOT_ENGINE == "claude" or (
        config.COPILOT_ENGINE == "auto" and os.environ.get("ANTHROPIC_API_KEY"))
    result, extra = None, []
    if use_claude:
        try:
            result = parse_claude(text, columns, rows, mapping)
        except Exception as e:  # network, auth, refusal, bad JSON: fall back, and say so
            extra.append(f"Claude unavailable ({type(e).__name__}); used the offline parser")
    if result is None:
        result = parse_local(text, columns, rows, mapping)
    rules, notes = sanitize(result["rules"], columns)
    return {"rules": rules, "notes": extra + list(result.get("notes", [])) + notes,
            "engine": result["engine"], "counts": count_matches(rules, rows)}
