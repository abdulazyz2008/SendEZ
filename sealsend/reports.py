"""Club report: what a club (or all clubs) did in a period, for club heads and faculty.

Counts events, certificates issued, students reached, unique and repeat participants, categories,
departments and event feedback, and exports them as a PDF and a CSV. Chaos Test batches are left out.
"""
import csv
import io
import json
from collections import Counter, defaultdict
from datetime import date

from . import clubs, config, db, pipeline
from .data import classify, resolve_values

ISSUED = ("Sent", "Claim", "Claimed")
CATEGORY_NAMES = {"winner": "Winners & awards", "participation": "Participants", "appreciation": "Volunteers & helpers",
                  "completion": "Completion"}


def club_label(club: str) -> str:
    if club in ("", "all"):
        return f"{clubs.UNIVERSITY} · All clubs"
    if club == "other":
        return "Events without a club"
    return f"{clubs.UNIVERSITY} · {clubs.CLUB_BY_ID[club]['name']}"


def _event_date(cfg: dict, recs: list[dict], created: str) -> str:
    src = (cfg.get("mapping") or {}).get("DATE") or {}
    if src.get("type") == "text" and src.get("value"):
        return str(src["value"])
    if src.get("type") == "column":
        for r in recs:
            v = json.loads(r["data_json"] or "{}").get(src.get("value", ""), "").strip()
            if v:
                return v
    return date.fromisoformat(created[:10]).strftime("%d %b %Y")


def build(club: str = "all", start: str = "", end: str = "") -> dict:
    """club: a club id, 'all' or 'other' (events not linked to a club). start/end: YYYY-MM-DD, inclusive."""
    if club not in ("all", "other", "") and club not in clubs.CLUB_BY_ID:
        raise ValueError("Unknown club")
    events, people = [], defaultdict(set)
    cats, depts = Counter(), Counter()
    rating_sum = rating_n = 0
    for b in db.query("SELECT * FROM batches WHERE is_chaos=0 ORDER BY created_at, id"):
        cfg = json.loads(b["config_json"] or "{}")
        bclub = cfg.get("club", "") or ""
        if (club == "other" and bclub) or (club not in ("all", "other", "") and bclub != club):
            continue
        created = b["created_at"][:10]
        if (start and created < start) or (end and created > end):
            continue
        recs = db.query("SELECT id, status, email, student_id, rule_label, category, data_json FROM records WHERE batch_id=?",
                        (b["id"],))
        issued = [r for r in recs if r["status"] in ISSUED]
        mapping = cfg.get("mapping") or {}
        for r in issued:
            key = (r["student_id"] or "").strip().lower() or (r["email"] or "").strip().lower()
            if key:
                people[key].add(b["id"])
            cats[classify(r["rule_label"] or r["category"] or "")] += 1
            if mapping.get("DEPARTMENT"):
                dept = resolve_values(json.loads(r["data_json"] or "{}"), {"DEPARTMENT": mapping["DEPARTMENT"]}).get("DEPARTMENT", "").strip()
                if dept:
                    depts[dept] += 1
        fb = pipeline.feedback_summary(b["id"])
        if fb["count"]:
            rating_sum += fb["average"] * fb["count"]
            rating_n += fb["count"]
        events.append({
            "id": b["id"], "name": b["name"], "club": bclub, "club_name": clubs.CLUB_BY_ID[bclub]["name"] if bclub in clubs.CLUB_BY_ID else "",
            "date": _event_date(cfg, issued or recs, created), "created": created, "students": len(recs),
            "issued": len(issued), "emailed": sum(1 for r in issued if r["status"] == "Sent"),
            "claim_links": sum(1 for r in issued if r["status"] in ("Claim", "Claimed")),
            "invalid": sum(1 for r in recs if r["status"] == "Invalid"),
            "rating": fb["average"], "ratings": fb["count"]})
    return {
        "club": club or "all", "title": club_label(club), "start": start, "end": end, "events": events,
        "totals": {"events": len(events), "certificates": sum(e["issued"] for e in events), "participants": len(people),
                   "repeat": sum(1 for v in people.values() if len(v) >= 2),
                   "rating": round(rating_sum / rating_n, 2) if rating_n else None, "ratings": rating_n},
        "categories": [{"key": k, "label": CATEGORY_NAMES.get(k, k.title()), "count": n} for k, n in cats.most_common()],
        "departments": [{"name": k, "count": n} for k, n in depts.most_common(8)],
    }


def period_text(start: str, end: str) -> str:
    fmt = lambda s: date.fromisoformat(s).strftime("%d %b %Y")
    if start and end:
        return f"{fmt(start)} – {fmt(end)}"
    if start:
        return f"From {fmt(start)}"
    if end:
        return f"Until {fmt(end)}"
    return "All time"


def to_csv(rep: dict) -> str:
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["Date", "Event", "Club", "Students", "Certificates issued", "Emailed", "Claim links", "Need fixing",
                "Average rating", "Ratings"])
    for e in rep["events"]:
        w.writerow([e["date"], e["name"], e["club_name"], e["students"], e["issued"], e["emailed"], e["claim_links"],
                    e["invalid"], e["rating"] if e["rating"] is not None else "", e["ratings"]])
    return "﻿" + out.getvalue()


def to_pdf(rep: dict) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    ink, sub, line, soft = colors.HexColor("#111318"), colors.HexColor("#5f6672"), colors.HexColor("#e3e6ea"), colors.HexColor("#f6f7f9")
    h1 = ParagraphStyle("h1", fontName="Helvetica-Bold", fontSize=20, leading=24, textColor=ink)
    h2 = ParagraphStyle("h2", fontName="Helvetica-Bold", fontSize=12.5, leading=16, textColor=ink, spaceBefore=12, spaceAfter=6)
    body = ParagraphStyle("b", fontName="Helvetica", fontSize=9.5, leading=12.5, textColor=ink)
    small = ParagraphStyle("s", parent=body, fontSize=9, textColor=sub)
    esc = lambda t: str(t).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    story = []
    club = rep["club"]
    if club in clubs.CLUB_BY_ID:
        name = clubs.composite_logo(club)
        if name and (config.UPLOAD_DIR / name).exists():
            from PIL import Image as PILImage
            path = config.UPLOAD_DIR / name
            with PILImage.open(path) as im:
                ratio = im.width / im.height
            story += [Image(str(path), width=min(80 * mm, 16 * mm * ratio), height=min(80 * mm, 16 * mm * ratio) / ratio, hAlign="LEFT"),
                      Spacer(1, 4 * mm)]
    story += [Paragraph(esc(rep["title"]), h1), Spacer(1, 1.5 * mm),
              Paragraph(f"Report for {esc(period_text(rep['start'], rep['end']))} · made {date.today().strftime('%d %b %Y')} with SendEZ", small),
              Spacer(1, 6 * mm)]
    t = rep["totals"]
    tiles = [(t["events"], "Events"), (t["certificates"], "Certificates issued"), (t["participants"], "Students reached"),
             (t["repeat"], "Came to 2+ events"), (f"{t['rating']:.1f} / 5" if t["rating"] is not None else "–", "Average rating")]
    big = ParagraphStyle("big", fontName="Helvetica-Bold", fontSize=17, leading=20, textColor=ink)
    tile_table = Table([[Paragraph(str(v), big) for v, _ in tiles], [Paragraph(esc(l), small) for _, l in tiles]],
                       colWidths=[34.8 * mm] * 5, hAlign="LEFT")
    tile_table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), soft), ("BOX", (0, 0), (-1, -1), 0.5, line),
                                    ("INNERGRID", (0, 0), (-1, -1), 0, colors.white), ("LINEAFTER", (0, 0), (-2, -1), 3, colors.white),
                                    ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    story += [tile_table, Paragraph("Events", h2)]
    all_clubs = club == "all"
    head = ["Date", "Event"] + (["Club"] if all_clubs else []) + ["Students", "Certificates", "Rating"]
    rows = [[Paragraph(f"<b>{h}</b>", small) for h in head]]
    for e in rep["events"]:
        rows.append([Paragraph(esc(e["date"]), body), Paragraph(esc(e["name"]), body)]
                    + ([Paragraph(esc(e["club_name"] or "–"), body)] if all_clubs else [])
                    + [Paragraph(str(e["students"]), body), Paragraph(str(e["issued"]), body),
                       Paragraph(f"{e['rating']:.1f} ({e['ratings']})" if e["rating"] is not None else "–", body)])
    if len(rows) == 1:
        rows.append([Paragraph("No events in this period.", small)] + [""] * (len(head) - 1))
    widths = [26 * mm, 54 * mm] + ([30 * mm] if all_clubs else []) + [20 * mm, 24 * mm, 20 * mm]  # 174 mm = page width
    if not all_clubs:
        widths[1] += 30 * mm
    ev = Table(rows, colWidths=widths, repeatRows=1, hAlign="LEFT")
    ev.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, -1), 0.5, line), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                            ("BACKGROUND", (0, 0), (-1, 0), soft), ("TOPPADDING", (0, 0), (-1, -1), 4),
                            ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
    story.append(ev)
    for title, items, key in (("Certificates by category", rep["categories"], "label"), ("Top departments", rep["departments"], "name")):
        if items:
            story.append(Paragraph(title, h2))
            tbl = Table([[Paragraph(esc(i[key]), body), Paragraph(str(i["count"]), body)] for i in items],
                        colWidths=[110 * mm, 30 * mm], hAlign="LEFT")
            tbl.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, -1), 0.5, line), ("TOPPADDING", (0, 0), (-1, -1), 3),
                                     ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
            story.append(tbl)
    story += [Spacer(1, 8 * mm), Paragraph("Students are counted once per student ID (or email when there is no ID). "
                                           "Certificates issued = emailed + claim links. Ratings come from the optional stars in the certificate email.", small)]
    out = io.BytesIO()
    SimpleDocTemplate(out, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm, bottomMargin=16 * mm,
                      title=rep["title"], author="SendEZ").build(story)
    return out.getvalue()
