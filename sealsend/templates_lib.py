"""Certificate template library: bundled fonts, 14 original built-in designs and imported (Canva) templates.

Every design here is original artwork drawn in code, so it can ship with the app.
Imported templates are a PNG/JPG/PDF the user exported from their own Canva account
(or any design tool) with text fields the user positions in the visual editor.
"""
import copy
import math
import re
from io import BytesIO
from pathlib import Path

from reportlab.graphics import renderPDF
from reportlab.graphics.barcode import qr as rl_qr
from reportlab.graphics.shapes import Drawing
from reportlab.lib.colors import Color, HexColor
from reportlab.lib.pagesizes import A4, landscape, portrait
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from . import config

FONT_DIR = config.PACKAGE_DIR / "fonts"
FONTS = {
    "display": "PlayfairDisplay-Bold", "display-italic": "PlayfairDisplay-Italic", "formal": "Cinzel-Bold",
    "elegant": "CormorantGaramond-SemiBold", "elegant-italic": "CormorantGaramond-Italic", "script": "GreatVibes-Regular",
    "sans": "Inter-Regular", "sans-semibold": "Inter-SemiBold", "sans-black": "Inter-ExtraBold",
    "condensed": "Oswald-SemiBold", "geo": "Montserrat-Bold", "geo-medium": "Montserrat-Medium",
    "mono": "JetBrainsMono-Medium", "italic": "Lora-Italic",
    "unicode": "DejaVuSans", "unicode-bold": "DejaVuSans-Bold", "unicode-serif": "DejaVuSerif",
    "unicode-serif-bold": "DejaVuSerif-Bold",
}
# Older configs used these names.
FONT_ALIASES = {"serif": "elegant", "serif-bold": "display", "serif-italic": "italic", "sans-bold": "sans-semibold"}
FONT_LABELS = {"display": "Playfair Display", "script": "Great Vibes (script)", "formal": "Cinzel", "elegant": "Cormorant Garamond",
               "elegant-italic": "Cormorant Italic", "display-italic": "Playfair Italic", "italic": "Lora Italic",
               "sans": "Inter", "sans-semibold": "Inter SemiBold", "sans-black": "Inter Heavy", "condensed": "Oswald",
               "geo": "Montserrat Bold", "geo-medium": "Montserrat", "mono": "JetBrains Mono"}
_registered: set[str] = set()


def font(key: str) -> str:
    key = FONT_ALIASES.get(key, key)
    name = FONTS.get(key, FONTS["sans"])
    if name not in _registered:
        pdfmetrics.registerFont(TTFont(name, str(FONT_DIR / f"{name}.ttf")))
        _registered.add(name)
    return name


def font_for(text: str, key: str) -> str:
    """Use the design font unless it lacks a glyph in `text` (e.g. Cyrillic, Greek): then DejaVu."""
    name = font(key)
    cmap = pdfmetrics.getFont(name).face.charToGlyph
    if all(ord(ch) in cmap or ch.isspace() for ch in text):
        return name
    bold = any(k in key for k in ("bold", "display", "formal", "geo", "black", "condensed", "semibold", "script", "elegant"))
    serif = any(k in key for k in ("display", "formal", "elegant", "italic", "script"))
    return font(("unicode-serif" if serif else "unicode") + ("-bold" if bold else ""))


# ---------------------------------------------------------------- text rendering with {{X|default}} and [[optional]]
TOKEN_RE = re.compile(r"\{\{\s*([A-Za-z0-9_]+)\s*(?:\|([^}]*))?\}\}")
OPTIONAL_RE = re.compile(r"\[\[(.*?)\]\]", re.S)


def render_text(text: str, values: dict) -> str:
    def opt(m):
        inner = m.group(1)
        keys = TOKEN_RE.findall(inner)
        if all(values.get(k.upper(), "").strip() or d for k, d in keys):
            return inner
        return ""
    text = OPTIONAL_RE.sub(opt, text or "")
    out = TOKEN_RE.sub(lambda m: values.get(m.group(1).upper(), "").strip() or (m.group(2) or ""), text)
    return re.sub(r"[ \t]{2,}", " ", out).strip()


def required_tokens(text: str) -> list[str]:
    """Placeholders that must have a value: outside [[...]] and without a |default."""
    outside = OPTIONAL_RE.sub("", text or "")
    return [k.upper() for k, d in TOKEN_RE.findall(outside) if not d]


def all_tokens(text: str) -> list[str]:
    return [k.upper() for k, _ in TOKEN_RE.findall(text or "")]


# ---------------------------------------------------------------- drawing primitives
def _wrap(text: str, fname: str, size: float, max_w: float) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if pdfmetrics.stringWidth(trial, fname, size) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def draw_text(c, text, x, y, key, size, color, align="center", max_w=None, tracking=0.0, lines=1, leading=1.25):
    if not text:
        return
    fname = font_for(text, key)
    max_w = max_w or 1e9
    width = lambda s, sz: pdfmetrics.stringWidth(s, fname, sz) + tracking * max(len(s) - 1, 0)  # noqa: E731
    while True:
        rows = [text] if lines <= 1 else _wrap(text, fname, size, max_w)
        if (len(rows) <= max(lines, 1) and all(width(r, size) <= max_w for r in rows)) or size <= 6:
            break
        size -= 0.5
    c.setFillColor(color)
    total = (len(rows) - 1) * size * leading
    for i, row in enumerate(rows):
        yy = y + total / 2 - i * size * leading if lines > 1 else y
        tw = width(row, size)
        left = {"center": x - tw / 2, "right": x - tw}.get(align, x)
        t = c.beginText(left, yy)
        t.setFont(fname, size)
        t.setCharSpace(tracking)
        t.textOut(row)
        c.drawText(t)
    return size


def _star(c, cx, cy, r, color, points=5, inner=0.45, rot=0):
    p = c.beginPath()
    for i in range(points * 2):
        rr = r if i % 2 == 0 else r * inner
        a = math.pi / 2 + rot + i * math.pi / points
        (p.moveTo if i == 0 else p.lineTo)(cx + rr * math.cos(a), cy + rr * math.sin(a))
    p.close()
    c.setFillColor(color)
    c.drawPath(p, stroke=0, fill=1)


def _leaf(c, cx, cy, length, angle, color):
    c.saveState()
    c.translate(cx, cy)
    c.rotate(angle)
    p = c.beginPath()
    p.moveTo(0, 0)
    p.curveTo(length * 0.3, length * 0.28, length * 0.7, length * 0.28, length, 0)
    p.curveTo(length * 0.7, -length * 0.28, length * 0.3, -length * 0.28, 0, 0)
    c.setFillColor(color)
    c.drawPath(p, stroke=0, fill=1)
    c.restoreState()


def _medal(c, x, y, r, gold, dark, label="WINNER"):
    c.setFillColor(dark)
    for s in (-1, 1):
        p = c.beginPath()
        p.moveTo(x + s * r * 0.5, y - r * 0.2); p.lineTo(x + s * r * 0.85, y - r * 1.55)
        p.lineTo(x + s * r * 0.45, y - r * 1.35); p.lineTo(x + s * r * 0.2, y - r * 1.7); p.lineTo(x + s * r * 0.05, y - r * 0.3)
        c.drawPath(p, stroke=0, fill=1)
    c.setFillColor(gold)
    c.circle(x, y, r, stroke=0, fill=1)
    c.setStrokeColor(dark)
    c.setLineWidth(1.1)
    c.circle(x, y, r * 0.82, stroke=1, fill=0)
    draw_text(c, label, x, y - r * 0.1, "sans-black", r * 0.3, HexColor("#ffffff"), tracking=0.6)
    for i in (-1, 0, 1):
        _star(c, x + i * r * 0.3, y - r * 0.42, r * 0.09, HexColor("#ffffff"))


def _seal(c, x, y, r, color, text="OFFICIAL SEAL ✦ CERTIFIED ✦ "):
    c.setStrokeColor(color)
    c.setLineWidth(1.4)
    c.circle(x, y, r, stroke=1, fill=0)
    c.setLineWidth(0.6)
    c.circle(x, y, r * 0.72, stroke=1, fill=0)
    fname = font("formal")
    size = r * 0.17
    n = len(text)
    c.setFillColor(color)
    for i, ch in enumerate(text):
        a = math.pi / 2 - i * 2 * math.pi / n
        c.saveState()
        c.translate(x + r * 0.86 * math.cos(a), y + r * 0.86 * math.sin(a))
        c.rotate(math.degrees(a) - 90)
        c.setFont(font_for(ch, "formal") if ch == "✦" else fname, size)
        c.drawCentredString(0, -size * 0.35, ch)
        c.restoreState()
    _star(c, x, y, r * 0.38, color, points=8, inner=0.6)


def _corner_flourish(c, x, y, sx, sy, color, s=1.0):
    c.setStrokeColor(color)
    c.setLineWidth(1.2)
    for k, rad in enumerate((26, 40, 54)):
        p = c.beginPath()
        p.arc(x - rad * s * sx * (1 if sx > 0 else 1), y - rad * s * sy, x + rad * s * sx, y + rad * s * sy,
              startAng=(0 if sx > 0 else 180) if sy > 0 else (270 if sx > 0 else 180), extent=90 if (sx * sy) > 0 else -90)
        c.drawPath(p, stroke=1, fill=0)
    c.setFillColor(color)
    c.circle(x, y, 3.2 * s, stroke=0, fill=1)


# ---------------------------------------------------------------- built-in designs
def _bg(c, w, h, color):
    c.setFillColor(color)
    c.rect(0, 0, w, h, stroke=0, fill=1)


def d_university(c, w, h, p):
    _bg(c, w, h, p["paper"])
    c.setStrokeColor(p["accent2"]); c.setLineWidth(12); c.rect(16, 16, w - 32, h - 32)
    c.setStrokeColor(p["accent"]); c.setLineWidth(1.6); c.rect(32, 32, w - 64, h - 64)
    c.setLineWidth(0.6); c.rect(38, 38, w - 76, h - 76)
    for x, y in ((38, 38), (w - 38, 38), (38, h - 38), (w - 38, h - 38)):
        _star(c, x, y, 9, p["accent"], points=4, inner=0.35)
    c.setStrokeColor(p["accent"]); c.setLineWidth(0.8)
    c.line(w * 0.36, h * 0.655, w * 0.64, h * 0.655)
    _seal(c, w * 0.5, h * 0.2, 34, p["accent"])


def d_gold(c, w, h, p):
    _bg(c, w, h, p["paper"])
    c.setStrokeColor(p["accent2"]); c.setLineWidth(10); c.rect(18, 18, w - 36, h - 36)
    c.setStrokeColor(p["accent"]); c.setLineWidth(1.5); c.rect(34, 34, w - 68, h - 68)
    for x, y, sx, sy in ((48, 48, 1, 1), (w - 48, 48, -1, 1), (48, h - 48, 1, -1), (w - 48, h - 48, -1, -1)):
        c.setStrokeColor(p["accent"]); c.setLineWidth(1)
        c.line(x, y, x + 60 * sx, y); c.line(x, y, x, y + 60 * sy)
        _star(c, x, y, 5, p["accent"], points=4, inner=0.4)
    c.setStrokeColor(p["accent"]); c.setLineWidth(0.8); c.line(w * 0.38, h * 0.66, w * 0.62, h * 0.66)
    _medal(c, w * 0.5, h * 0.215, 30, p["accent"], p["accent2"])


def d_tech(c, w, h, p):
    _bg(c, w, h, p["paper"])
    c.setFillColor(p["accent2"]); c.rect(0, 0, w * 0.075, h, stroke=0, fill=1)
    c.setFillColor(p["accent"]); c.rect(w * 0.075, 0, 5, h, stroke=0, fill=1)
    c.setFillColor(Color(*p["accent"].rgb(), alpha=0.18))
    for i in range(9):
        for j in range(6):
            c.circle(w * 0.80 + i * 15, h * 0.70 + j * 15, 1.6, stroke=0, fill=1)
    c.setStrokeColor(Color(*p["accent"].rgb(), alpha=0.35)); c.setLineWidth(1.2)
    pts = [(w * 0.93, h * 0.08), (w * 0.93, h * 0.2), (w * 0.86, h * 0.27), (w * 0.72, h * 0.27)]
    for a, b in zip(pts, pts[1:]):
        c.line(*a, *b)
    c.setFillColor(p["accent"]); c.circle(*pts[-1], 3, stroke=0, fill=1)
    c.saveState(); c.translate(w * 0.045, h * 0.5); c.rotate(90)
    draw_text(c, "< VERIFIED BUILDER />", 0, -4, "mono", 11, HexColor("#ffffff"), tracking=2)
    c.restoreState()


def d_corporate(c, w, h, p):
    _bg(c, w, h, p["paper"])
    c.setFillColor(p["accent2"]); c.rect(0, h - 14, w, 14, stroke=0, fill=1)
    c.setFillColor(p["accent"]); c.rect(0, h - 14, w * 0.3, 14, stroke=0, fill=1)
    c.setFillColor(p["accent"]); c.rect(w * 0.09, h * 0.705, 46, 4, stroke=0, fill=1)
    c.setStrokeColor(HexColor("#e5e7eb")); c.setLineWidth(0.8); c.line(w * 0.09, h * 0.27, w * 0.91, h * 0.27)
    c.setFillColor(Color(*p["accent"].rgb(), alpha=0.07))
    c.circle(w * 0.98, h * 0.05, 170, stroke=0, fill=1)
    c.setFillColor(Color(*p["accent2"].rgb(), alpha=0.06))
    c.circle(w * 0.9, h * -0.05, 120, stroke=0, fill=1)


def d_school(c, w, h, p):
    _bg(c, w, h, p["paper"])
    c.setStrokeColor(p["accent"]); c.setLineWidth(8); c.roundRect(20, 20, w - 40, h - 40, 26)
    c.setStrokeColor(p["accent2"]); c.setLineWidth(2); c.setDash(6, 6); c.roundRect(34, 34, w - 68, h - 68, 18); c.setDash()
    cols = [HexColor("#f59e0b"), HexColor("#ec4899"), HexColor("#10b981"), HexColor("#3b82f6"), HexColor("#8b5cf6")]
    spots = [(0.075, 0.88, 13), (0.075, 0.70, 8), (0.925, 0.88, 15), (0.925, 0.70, 9), (0.075, 0.42, 7), (0.925, 0.42, 7),
             (0.30, 0.935, 6), (0.70, 0.935, 6), (0.5, 0.06, 7), (0.40, 0.06, 5), (0.60, 0.06, 5)]
    for i, (x, y, r) in enumerate(spots):
        _star(c, w * x, h * y, r, cols[i % len(cols)], rot=i * 0.4)


def d_sports(c, w, h, p):
    _bg(c, w, h, p["paper"])
    for k, col in enumerate((p["accent"], p["accent2"], p["accent"])):
        c.setFillColor(col)
        off = k * 26
        path = c.beginPath(); path.moveTo(0, h - off); path.lineTo(150 - off, h); path.lineTo(170 - off + 0, h)
        path.lineTo(0, h - off - 20); path.close(); c.drawPath(path, stroke=0, fill=1)
        path = c.beginPath(); path.moveTo(w, off); path.lineTo(w - 150 + off, 0); path.lineTo(w - 170 + off, 0)
        path.lineTo(w, off + 20); path.close(); c.drawPath(path, stroke=0, fill=1)
    c.setStrokeColor(p["accent2"]); c.setLineWidth(3); c.rect(26, 26, w - 52, h - 52)
    c.setFillColor(p["accent"]); c.rect(w * 0.42, h * 0.645, w * 0.16, 4, stroke=0, fill=1)
    _medal(c, w * 0.5, h * 0.215, 28, HexColor("#d4a017"), p["accent2"], "CHAMPION")


def d_medical(c, w, h, p):
    _bg(c, w, h, p["paper"])
    c.setFillColor(p["accent2"]); c.rect(0, h * 0.84, w, h * 0.16, stroke=0, fill=1)
    c.setFillColor(p["accent"]); c.rect(0, h * 0.84 - 4, w, 4, stroke=0, fill=1)
    c.setFillColor(Color(*p["accent"].rgb(), alpha=0.07))
    for i in range(3):
        for j in range(3):
            x, y, s = (w * 0.045 + i * 22) if i < 3 else 0, h * 0.47 + j * 22, 5
            c.rect(w - x - s / 3, y - s, s * 2 / 3, s * 2, stroke=0, fill=1); c.rect(w - x - s, y - s / 3, s * 2, s * 2 / 3, stroke=0, fill=1)
            c.rect(x - s / 3, y - s, s * 2 / 3, s * 2, stroke=0, fill=1); c.rect(x - s, y - s / 3, s * 2, s * 2 / 3, stroke=0, fill=1)
    draw_text(c, "CONTINUING PROFESSIONAL EDUCATION", w * 0.5, h * 0.905, "sans-semibold", 11, HexColor("#ffffff"), tracking=3)


def d_ngo(c, w, h, p):
    _bg(c, w, h, p["paper"])
    c.setStrokeColor(p["accent2"]); c.setLineWidth(1.4); c.rect(28, 28, w - 56, h - 56)
    greens = [p["accent"], p["accent2"], HexColor("#84a98c")]
    for (x, y, base) in ((40, h - 40, -40), (w - 40, h - 40, 140)):
        for i in range(7):
            _leaf(c, x, y, 64 - i * 4, base + i * 14, greens[i % 3])
    c.setFillColor(p["accent"]); c.circle(w * 0.5, h * 0.665, 3, stroke=0, fill=1)
    c.setStrokeColor(p["accent"]); c.setLineWidth(0.8); c.line(w * 0.4, h * 0.665, w * 0.49, h * 0.665); c.line(w * 0.51, h * 0.665, w * 0.6, h * 0.665)


def d_workshop(c, w, h, p):
    _bg(c, w, h, p["paper"])
    c.setFillColor(p["accent2"]); c.rect(0, h - 64, w, 64, stroke=0, fill=1)
    c.setStrokeColor(p["accent2"]); c.setLineWidth(1); c.rect(24, 24, w - 48, h - 112)
    c.setFillColor(p["accent"]); c.rect(w * 0.08, 24, 70, 5, stroke=0, fill=1)


def d_arts(c, w, h, p):
    _bg(c, w, h, p["paper"])
    c.setStrokeColor(p["accent2"]); c.setLineWidth(5); c.rect(22, 22, w - 44, h - 44)
    c.setStrokeColor(p["accent"]); c.setLineWidth(0.8); c.rect(32, 32, w - 64, h - 64)
    for x, y, sx, sy in ((32, 32, 1, 1), (w - 32, 32, -1, 1), (32, h - 32, 1, -1), (w - 32, h - 32, -1, -1)):
        _corner_flourish(c, x, y, sx, sy, p["accent"])
    for dx in (-1, 1):
        _leaf(c, w * 0.5 + dx * 12, h * 0.66, 34, 180 if dx < 0 else 0, p["accent"])
    c.setFillColor(p["accent2"]); c.circle(w * 0.5, h * 0.66, 3.5, stroke=0, fill=1)


def d_government(c, w, h, p):
    _bg(c, w, h, p["paper"])
    c.setLineWidth(0.45)
    for k in range(4):
        c.setStrokeColor(Color(*p["accent"].rgb(), alpha=0.55 - k * 0.1))
        for edge in range(4):
            path = c.beginPath()
            n = 160
            for i in range(n + 1):
                t = i / n
                off = 22 + 5 * math.sin(t * math.pi * 36 + k * 1.2) + k * 2.2
                pt = {0: (t * w, off), 1: (t * w, h - off), 2: (off, t * h), 3: (w - off, t * h)}[edge]
                (path.moveTo if i == 0 else path.lineTo)(*pt)
            c.drawPath(path, stroke=1, fill=0)
    c.setStrokeColor(p["accent2"]); c.setLineWidth(1.2); c.rect(44, 44, w - 88, h - 88)
    c.setFillColor(Color(*p["accent"].rgb(), alpha=0.05)); c.circle(w * 0.5, h * 0.48, 150, stroke=0, fill=1)


def d_internship(c, w, h, p):
    _bg(c, w, h, p["paper"])
    c.setFillColor(p["accent2"]); c.rect(0, 0, w * 0.28, h, stroke=0, fill=1)
    c.setFillColor(p["accent"]); c.rect(w * 0.28, 0, 6, h, stroke=0, fill=1)
    c.setFillColor(Color(1, 1, 1, alpha=0.06))
    for i in range(6):
        c.circle(w * 0.14, h * 0.5, 40 + i * 28, stroke=0, fill=1)
    draw_text(c, "CERTIFICATE", w * 0.14, h * 0.56, "sans-black", 21, HexColor("#ffffff"), tracking=3)
    draw_text(c, "OF COMPLETION", w * 0.14, h * 0.505, "sans-semibold", 10.5, Color(1, 1, 1, alpha=0.75), tracking=3.4)


def d_participation(c, w, h, p):
    _bg(c, w, h, p["paper"])
    c.setStrokeColor(p["accent2"]); c.setLineWidth(1.2); c.rect(26, 26, w - 52, h - 52)
    c.setFillColor(p["accent"])
    for x, y in ((26, 26), (w - 26, 26), (26, h - 26), (w - 26, h - 26)):
        c.rect(x - 7, y - 7, 14, 14, stroke=0, fill=1)
    c.setStrokeColor(p["accent"]); c.setLineWidth(0.8); c.line(w * 0.42, h * 0.655, w * 0.58, h * 0.655)


def d_mono(c, w, h, p):
    _bg(c, w, h, p["paper"])
    c.setStrokeColor(p["ink"]); c.setLineWidth(3); c.rect(24, 24, w - 48, h - 48)
    c.setLineWidth(0.6); c.rect(32, 32, w - 64, h - 64)


# Field factory: a classic centered layout tuned per design.
def centered(title_font="formal", title_size=34, name_font="script", name_size=50, body_font="sans", accent_font="sans-semibold",
             y=None, title_track=4.0, upper_title=True, presented="This certificate is proudly presented to",
             presented_font="italic", org_y=0.885, logo=True, signatures=True, sig_y=0.17, id_color="muted",
             name_color="name", underline=True, x=0.5, align="center", max_w=0.72, id_x=0.075, title_max=0.8):
    y = y or {}
    f = []
    if logo:
        f.append({"type": "logo", "x": x, "y": y.get("logo", 0.9), "h": 0.075, "align": align})
    f += [
        {"key": "org", "text": "[[{{ORG_NAME}}]]", "x": x, "y": y.get("org", org_y), "size": 12, "font": accent_font, "color": "muted",
         "align": align, "tracking": 2.0, "upper": True, "max_width": max_w},
        {"key": "title", "text": "{{TITLE|Certificate of Achievement}}", "x": x, "y": y.get("title", 0.765), "size": title_size,
         "font": title_font, "color": "title", "align": align, "tracking": title_track, "upper": upper_title, "max_width": title_max},
        {"key": "presented", "text": presented, "x": x, "y": y.get("presented", 0.605), "size": 13, "font": presented_font,
         "color": "muted", "align": align},
        {"key": "name", "text": "{{NAME}}", "x": x, "y": y.get("name", 0.49), "size": name_size, "font": name_font, "color": name_color,
         "align": align, "max_width": max_w, "underline": underline},
        {"key": "description", "text": "{{DESCRIPTION}}", "x": x, "y": y.get("description", 0.38), "size": 14, "font": body_font,
         "color": "text", "align": align, "max_width": max_w + 0.04, "lines": 2},
    ]
    if signatures:
        f += [{"type": "signature", "n": 1, "x": 0.25 if align == "center" else x + 0.06, "y": sig_y},
              {"type": "signature", "n": 2, "x": 0.75, "y": sig_y}]
    f.append({"key": "cert_id", "text": "Certificate ID {{CERT_ID}}", "x": id_x, "y": 0.07, "size": 7.5, "font": "mono",
              "color": id_color, "align": "left"})
    f.append({"type": "qr", "x": 0.885, "y": 0.135, "size": 0.1})
    return f


def _pal(paper, ink, title, name, text, muted, accent, accent2):
    return {"paper": paper, "ink": ink, "title": title, "name": name, "text": text, "muted": muted, "accent": accent, "accent2": accent2}


BUILTINS = [
    {"id": "university-classic", "name": "University Classic", "niche": "Academic",
     "description": "Navy and gold with an official seal. Convocations, academic honors, university events.",
     "palette": _pal("#fdfbf5", "#1c2a3f", "#1f3a5f", "#1f3a5f", "#333333", "#6b6b6b", "#b8892d", "#1f3a5f"),
     "decor": d_university, "fields": centered(sig_y=0.17), "title": "Certificate of Achievement", "keywords": ["university", "college", "academic", "convocation", "honor", "seminar", "conference"]},
    {"id": "gold-excellence", "name": "Gold Excellence", "niche": "Competitions & Awards",
     "description": "Gold frame and a winner's medal. First, second and third place, best-of awards.",
     "palette": _pal("#fffbf0", "#3d2f08", "#7a5a0c", "#3d2f08", "#3d3420", "#7a6b4f", "#c9a227", "#8a6a12"),
     "decor": d_gold, "fields": centered(title_font="display", title_track=2.5), "title": "Certificate of Excellence", "keywords": ["award", "winner", "competition", "contest", "olympiad", "quiz"]},
    {"id": "tech-hackathon", "name": "Tech Hackathon", "niche": "Technology",
     "description": "Code-inspired, modern. Hackathons, coding contests, tech fests, developer events.",
     "palette": _pal("#ffffff", "#0b1020", "#0b1020", "#0b1020", "#334155", "#64748b", "#4f46e5", "#0b1020"),
     "decor": d_tech, "title": "Certificate of Achievement", "keywords": ["hackathon", "hack", "code", "coding", "tech", "developer", "ai", "programming", "robotics", "cyber"],
     "fields": [
         {"type": "logo", "x": 0.135, "y": 0.86, "h": 0.07, "align": "left"},
         {"key": "org", "text": "[[{{ORG_NAME}}]]", "x": 0.135, "y": 0.78, "size": 11, "font": "mono", "color": "accent", "align": "left", "upper": True, "tracking": 1.5},
         {"key": "title", "text": "{{TITLE|Certificate of Achievement}}", "x": 0.135, "y": 0.68, "size": 34, "font": "geo", "color": "title", "align": "left", "max_width": 0.62},
         {"key": "presented", "text": "// awarded to", "x": 0.135, "y": 0.575, "size": 12, "font": "mono", "color": "muted", "align": "left"},
         {"key": "name", "text": "{{NAME}}", "x": 0.135, "y": 0.47, "size": 44, "font": "geo", "color": "accent", "align": "left", "max_width": 0.72},
         {"key": "description", "text": "{{DESCRIPTION}}", "x": 0.135, "y": 0.365, "size": 14, "font": "sans", "color": "text", "align": "left", "max_width": 0.66, "lines": 2},
         {"type": "signature", "n": 1, "x": 0.25, "y": 0.15}, {"type": "signature", "n": 2, "x": 0.55, "y": 0.15},
         {"key": "cert_id", "text": "id: {{CERT_ID}}", "x": 0.135, "y": 0.06, "size": 7.5, "font": "mono", "color": "muted", "align": "left"},
         {"type": "qr", "x": 0.885, "y": 0.14, "size": 0.11}]},
    {"id": "corporate-modern", "name": "Corporate Modern", "niche": "Corporate & Training",
     "description": "Clean and professional. Employee training, compliance courses, workshops at work.",
     "palette": _pal("#ffffff", "#0f172a", "#0f172a", "#0f172a", "#334155", "#64748b", "#0f766e", "#0f172a"),
     "decor": d_corporate, "title": "Certificate of Completion", "keywords": ["training", "corporate", "course", "compliance", "employee", "program", "bootcamp"],
     "fields": [
         {"type": "logo", "x": 0.09, "y": 0.86, "h": 0.07, "align": "left"},
         {"key": "org", "text": "[[{{ORG_NAME}}]]", "x": 0.91, "y": 0.875, "size": 11, "font": "sans-semibold", "color": "muted", "align": "right", "upper": True, "tracking": 1.8},
         {"key": "title", "text": "{{TITLE|Certificate of Completion}}", "x": 0.09, "y": 0.745, "size": 30, "font": "sans-black", "color": "title", "align": "left", "max_width": 0.8},
         {"key": "presented", "text": "This is to certify that", "x": 0.09, "y": 0.6, "size": 12.5, "font": "sans", "color": "muted", "align": "left"},
         {"key": "name", "text": "{{NAME}}", "x": 0.09, "y": 0.5, "size": 42, "font": "display", "color": "name", "align": "left", "max_width": 0.8},
         {"key": "description", "text": "{{DESCRIPTION}}", "x": 0.09, "y": 0.395, "size": 14, "font": "sans", "color": "text", "align": "left", "max_width": 0.78, "lines": 2},
         {"type": "signature", "n": 1, "x": 0.2, "y": 0.13}, {"type": "signature", "n": 2, "x": 0.47, "y": 0.13},
         {"key": "cert_id", "text": "Credential ID {{CERT_ID}}", "x": 0.09, "y": 0.06, "size": 7.5, "font": "mono", "color": "muted", "align": "left"},
         {"type": "qr", "x": 0.885, "y": 0.135, "size": 0.1}]},
    {"id": "school-stars", "name": "School Stars", "niche": "Schools",
     "description": "Bright and friendly. School competitions, sports day, class achievements.",
     "palette": _pal("#fffdf7", "#1e3a8a", "#1e3a8a", "#be185d", "#1f2937", "#6b7280", "#3b82f6", "#f59e0b"),
     "decor": d_school, "title": "Certificate of Achievement", "keywords": ["school", "class", "student of", "kids", "junior", "annual day"],
     "fields": centered(title_font="geo", title_size=30, title_track=1.5, name_font="display", name_size=46, presented_font="sans", presented="is proudly awarded to", underline=False, title_max=0.7)},
    {"id": "sports-champion", "name": "Sports Champion", "niche": "Sports",
     "description": "Bold and energetic. Sports meets, tournaments, marathons, fitness challenges.",
     "palette": _pal("#ffffff", "#111111", "#111111", "#111111", "#222222", "#666666", "#dc2626", "#111111"),
     "decor": d_sports, "title": "Certificate of Victory", "keywords": ["sport", "sports", "tournament", "match", "marathon", "athletic", "cricket", "football", "chess", "run", "meet"],
     "fields": centered(title_font="condensed", title_size=44, title_track=3, name_font="condensed", name_size=46, presented_font="sans-semibold", presented="PRESENTED TO", underline=False, y={"title": 0.75, "presented": 0.585})},
    {"id": "medical-cme", "name": "Medical & CME", "niche": "Healthcare",
     "description": "Calm teal, clinical clarity. CME credits, nursing workshops, health camps.",
     "palette": _pal("#ffffff", "#0f2e33", "#0f3d44", "#0f3d44", "#334e52", "#6b8285", "#14b8a6", "#0f3d44"),
     "decor": d_medical, "title": "Certificate of Attendance", "keywords": ["medical", "cme", "health", "nursing", "clinical", "hospital", "pharma", "doctor", "first aid"],
     "fields": centered(title_font="display", title_size=32, title_track=1, upper_title=False, name_font="display", name_size=40, presented_font="sans", presented="This certifies that", logo=False, org_y=0.705,
                        y={"title": 0.64, "presented": 0.555, "name": 0.455, "description": 0.35}, sig_y=0.15)},
    {"id": "ngo-appreciation", "name": "Volunteer Appreciation", "niche": "NGO & Community",
     "description": "Warm greens and leaves. Volunteers, community service, donation drives.",
     "palette": _pal("#fbf8f1", "#2f3e2f", "#2f4f3a", "#2f4f3a", "#3f4a3f", "#7a7f6e", "#52796f", "#354f52"),
     "decor": d_ngo, "title": "Certificate of Appreciation", "keywords": ["volunteer", "ngo", "community", "service", "charity", "donation", "nss", "social", "drive", "clean"],
     "fields": centered(title_font="elegant", title_size=36, title_track=3, name_font="script", name_size=52, presented_font="elegant-italic", presented="With heartfelt gratitude to")},
    {"id": "workshop-minimal", "name": "Workshop Minimal", "niche": "Workshops & Seminars",
     "description": "Minimal header band. Seminars, guest lectures, webinars, short workshops.",
     "palette": _pal("#ffffff", "#111111", "#ffffff", "#111111", "#333333", "#6b6b6b", "#111111", "#111111"),
     "decor": d_workshop, "title": "Certificate of Participation", "keywords": ["workshop", "webinar", "lecture", "talk", "session", "masterclass", "orientation"],
     "fields": [
         {"key": "title", "text": "{{TITLE|Certificate of Participation}}", "x": 0.08, "y": 0.928, "size": 20, "font": "sans-black", "color": "title", "align": "left", "upper": True, "tracking": 2, "max_width": 0.6},
         {"key": "org", "text": "[[{{ORG_NAME}}]]", "x": 0.92, "y": 0.928, "size": 10.5, "font": "sans-semibold", "color": "#bbbbbb", "align": "right", "upper": True, "tracking": 1.5, "max_width": 0.35},
         {"type": "logo", "x": 0.92, "y": 0.75, "h": 0.07, "align": "right"},
         {"key": "presented", "text": "Awarded to", "x": 0.08, "y": 0.70, "size": 13, "font": "sans", "color": "muted", "align": "left"},
         {"key": "name", "text": "{{NAME}}", "x": 0.08, "y": 0.575, "size": 50, "font": "sans-black", "color": "name", "align": "left", "max_width": 0.84},
         {"key": "description", "text": "{{DESCRIPTION}}", "x": 0.08, "y": 0.45, "size": 15, "font": "sans", "color": "text", "align": "left", "max_width": 0.8, "lines": 2},
         {"type": "signature", "n": 1, "x": 0.2, "y": 0.2}, {"type": "signature", "n": 2, "x": 0.46, "y": 0.2},
         {"key": "cert_id", "text": "{{CERT_ID}}", "x": 0.2, "y": 0.06, "size": 7.5, "font": "mono", "color": "muted", "align": "left"},
         {"type": "qr", "x": 0.885, "y": 0.18, "size": 0.1}]},
    {"id": "arts-ornate", "name": "Arts & Culture", "niche": "Arts & Culture",
     "description": "Burgundy and ornaments. Music, dance, drama, literary and cultural fests.",
     "palette": _pal("#fbf6ee", "#4a1622", "#6d1a2c", "#4a1622", "#4a3a3a", "#8a7272", "#b08d57", "#6d1a2c"),
     "decor": d_arts, "title": "Certificate of Merit", "keywords": ["art", "arts", "music", "dance", "drama", "theatre", "literary", "cultural", "painting", "poetry", "fest"],
     "fields": centered(title_font="elegant", title_size=38, title_track=4, name_font="script", name_size=54, presented_font="elegant-italic", presented="is honoured to present this to", id_x=0.13)},
    {"id": "government-official", "name": "Official Formal", "niche": "Government & Institutions",
     "description": "Security-style guilloche border. Official recognitions, institutional certificates.",
     "palette": _pal("#fcfcf7", "#14213d", "#14213d", "#14213d", "#2b2b2b", "#666666", "#2a6f4e", "#14213d"),
     "decor": d_government, "title": "Certificate of Recognition", "keywords": ["government", "official", "ministry", "municipal", "department", "institute", "recognition"],
     "fields": centered(title_font="formal", title_size=32, title_track=3, name_font="display", name_size=42, presented_font="italic", presented="This is to certify that", sig_y=0.17)},
    {"id": "internship-completion", "name": "Internship", "niche": "Internships & Experience",
     "description": "Split layout with a bold side panel. Internships, fellowships, project completion.",
     "palette": _pal("#ffffff", "#0b2545", "#0b2545", "#0b2545", "#334155", "#64748b", "#13a89e", "#0b2545"),
     "decor": d_internship, "title": "Internship Completion", "keywords": ["intern", "internship", "fellowship", "apprentice", "project", "experience"],
     "fields": [
         {"type": "logo", "x": 0.14, "y": 0.84, "h": 0.08, "align": "center", "invert": True},
         {"key": "org", "text": "[[{{ORG_NAME}}]]", "x": 0.37, "y": 0.84, "size": 11, "font": "sans-semibold", "color": "accent", "align": "left", "upper": True, "tracking": 1.8, "max_width": 0.56},
         {"key": "title", "text": "{{TITLE|Internship Completion}}", "x": 0.37, "y": 0.73, "size": 26, "font": "sans-black", "color": "title", "align": "left", "max_width": 0.56},
         {"key": "presented", "text": "This certifies that", "x": 0.37, "y": 0.625, "size": 12.5, "font": "sans", "color": "muted", "align": "left"},
         {"key": "name", "text": "{{NAME}}", "x": 0.37, "y": 0.525, "size": 40, "font": "display", "color": "name", "align": "left", "max_width": 0.56},
         {"key": "description", "text": "{{DESCRIPTION}}", "x": 0.37, "y": 0.41, "size": 13.5, "font": "sans", "color": "text", "align": "left", "max_width": 0.56, "lines": 3},
         {"type": "signature", "n": 1, "x": 0.47, "y": 0.15}, {"type": "signature", "n": 2, "x": 0.72, "y": 0.15},
         {"key": "cert_id", "text": "{{CERT_ID}}", "x": 0.14, "y": 0.07, "size": 7.5, "font": "mono", "color": "#9fb3c8", "align": "center"},
         {"type": "qr", "x": 0.14, "y": 0.2, "size": 0.1, "on_dark": True}]},
    {"id": "participation-clean", "name": "Participation", "niche": "General",
     "description": "Simple and versatile. Participation certificates for any event.",
     "palette": _pal("#ffffff", "#1f2937", "#1f2937", "#111827", "#374151", "#6b7280", "#6b7280", "#374151"),
     "decor": d_participation, "title": "Certificate of Participation", "keywords": ["participation", "participant", "attendance", "event"],
     "fields": centered(title_font="display", title_size=34, title_track=1.5, upper_title=False, name_font="display", name_size=44, presented_font="sans", presented="This certificate is presented to")},
    {"id": "mono-minimal", "name": "Mono Minimal", "niche": "General",
     "description": "Pure black and white. Elegant, prints perfectly in grayscale.",
     "palette": _pal("#ffffff", "#000000", "#000000", "#000000", "#1a1a1a", "#666666", "#000000", "#000000"),
     "decor": d_mono, "title": "Certificate", "keywords": ["minimal", "simple", "black"],
     "fields": centered(title_font="sans-black", title_size=30, title_track=8, name_font="display", name_size=46, presented_font="sans", underline=False)},
]
BUILTIN_BY_ID = {t["id"]: t for t in BUILTINS}
LEGACY = {"classic": "university-classic", "gold": "gold-excellence", "participation": "participation-clean", "mono": "mono-minimal"}
NICHES = []
for _t in BUILTINS:
    if _t["niche"] not in NICHES:
        NICHES.append(_t["niche"])

DEFAULT_IMPORTED_FIELDS = [
    {"key": "name", "text": "{{NAME}}", "x": 0.5, "y": 0.5, "size": 40, "font": "display", "color": "#1a1a1a", "align": "center", "max_width": 0.7, "enabled": True},
    {"key": "description", "text": "{{DESCRIPTION}}", "x": 0.5, "y": 0.4, "size": 14, "font": "sans", "color": "#333333", "align": "center", "max_width": 0.72, "lines": 2, "enabled": True},
    {"key": "title", "text": "{{TITLE|Certificate of Achievement}}", "x": 0.5, "y": 0.76, "size": 30, "font": "formal", "color": "#1a1a1a", "align": "center", "max_width": 0.8, "enabled": False},
    {"key": "date", "text": "{{DATE|}}", "x": 0.5, "y": 0.32, "size": 12, "font": "sans", "color": "#555555", "align": "center", "enabled": False},
    {"key": "cert_id", "text": "Certificate ID {{CERT_ID}}", "x": 0.06, "y": 0.05, "size": 7.5, "font": "mono", "color": "#666666", "align": "left", "enabled": True},
    {"type": "qr", "key": "qr", "x": 0.9, "y": 0.12, "size": 0.1, "enabled": True},
]


def default_imported_fields() -> list[dict]:
    return copy.deepcopy(DEFAULT_IMPORTED_FIELDS)


def get_template(template_id: str) -> dict | None:
    """Built-in template, or an imported one from the database."""
    template_id = LEGACY.get(template_id, template_id)
    if template_id in BUILTIN_BY_ID:
        return BUILTIN_BY_ID[template_id]
    from . import db
    row = db.one("SELECT * FROM templates WHERE id=?", (template_id,))
    if not row:
        return None
    return {"id": row["id"], "name": row["name"], "niche": "My templates", "description": row["description"],
            "custom": True, "background": row["file"], "kind": row["kind"], "fields": json_loads(row["fields_json"]),
            "title": row["default_title"] or "Certificate of Achievement", "palette": None, "decor": None,
            "page_w": row["page_w"], "page_h": row["page_h"]}


def json_loads(s):
    import json
    return json.loads(s or "[]")


def template_text_fields(tpl: dict) -> list[str]:
    return [f.get("text", "") for f in tpl.get("fields", []) if f.get("enabled", True) and f.get("type", "text") == "text"]


# ---------------------------------------------------------------- rendering
def _color(value, pal):
    if isinstance(value, str) and value.startswith("#"):
        return HexColor(value)
    if pal and value in pal:
        return HexColor(pal[value])
    return HexColor("#1a1a1a")


def _image_reader(path: str | None):
    if not path:
        return None
    p = config.UPLOAD_DIR / Path(path).name
    return ImageReader(str(p)) if p.exists() else None


def _draw_signature(c, f, w, h, values, pal, assets):
    n = f.get("n", 1)
    name = values.get(f"SIGNER{n}_NAME", "").strip()
    title = values.get(f"SIGNER{n}_TITLE", "").strip()
    if not name and not title:
        return
    x, y = f["x"] * w, f["y"] * h
    sig = _image_reader(assets.get(f"signer{n}_sig"))
    if sig:
        iw, ih = sig.getSize()
        sh = 34
        sw = min(150, sh * iw / ih)
        c.drawImage(sig, x - sw / 2, y + 16, width=sw, height=sw * ih / iw, mask="auto")
    c.setStrokeColor(_color("text", pal) if pal else HexColor("#333333"))
    c.setLineWidth(0.7)
    c.line(x - 80, y + 14, x + 80, y + 14)
    if name:
        draw_text(c, name, x, y, "sans-semibold", 11, _color("text", pal) if pal else HexColor("#222222"), max_w=170)
        if title:
            draw_text(c, title, x, y - 13, "sans", 9, _color("muted", pal) if pal else HexColor("#666666"), max_w=170)
    else:
        draw_text(c, title, x, y, "sans", 10, _color("text", pal) if pal else HexColor("#333333"), max_w=170)


def _draw_qr(c, f, w, h, url, pal):
    size = f.get("size", 0.1) * h
    widget = rl_qr.QrCodeWidget(url)  # always dark modules on a white quiet zone: inverted codes fail on many phones
    bx0, by0, bx1, by1 = widget.getBounds()
    d = Drawing(size, size, transform=[size / (bx1 - bx0), 0, 0, size / (by1 - by0), 0, 0])
    d.add(widget)
    x, y = f["x"] * w - size / 2, f["y"] * h - size / 2
    c.setFillColor(HexColor("#ffffff"))
    c.rect(x - 3, y - 3, size + 6, size + 6, stroke=0, fill=1)
    renderPDF.draw(d, c, x, y)
    draw_text(c, "Scan to verify", f["x"] * w, y - 10, "sans", 6.5,
              HexColor("#ffffff") if f.get("on_dark") else (_color("muted", pal) if pal else HexColor("#666666")))


def draw_fields(c, tpl, w, h, values, assets, qr_on=True):
    pal = tpl.get("palette")
    for f in tpl.get("fields", []):
        if not f.get("enabled", True):
            continue
        kind = f.get("type", "text")
        if kind == "logo":
            logo = _image_reader(assets.get("logo"))
            if logo:
                iw, ih = logo.getSize()
                lh = f.get("h", 0.075) * h
                lw = min(lh * iw / ih, w * 0.3)
                lh = lw * ih / iw
                x = f["x"] * w
                left = {"center": x - lw / 2, "right": x - lw}.get(f.get("align", "center"), x)
                c.drawImage(logo, left, f["y"] * h - lh / 2, width=lw, height=lh, mask="auto")
            continue
        if kind == "signature":
            _draw_signature(c, f, w, h, values, pal, assets)
            continue
        if kind == "qr":
            if qr_on and values.get("VERIFY_URL"):
                _draw_qr(c, f, w, h, values["VERIFY_URL"], pal)
            continue
        text = render_text(f.get("text", ""), values)
        if f.get("upper"):
            text = text.upper()
        if not text:
            continue
        x, y = float(f["x"]) * w, float(f["y"]) * h
        color = _color(f.get("color", "text"), pal)
        size = draw_text(c, text, x, y, f.get("font", "sans"), float(f.get("size", 14)), color, f.get("align", "center"),
                         float(f.get("max_width", 0.85)) * w, float(f.get("tracking", 0)), int(f.get("lines", 1)))
        if f.get("underline") and size:
            fname = font_for(text, f.get("font", "sans"))
            tw = pdfmetrics.stringWidth(text, fname, size)
            left = {"center": x - tw / 2, "right": x - tw}.get(f.get("align", "center"), x)
            c.setStrokeColor(_color("accent", pal))
            c.setLineWidth(0.9)
            c.line(left - 24, y - size * 0.32, left + tw + 24, y - size * 0.32)


def page_size(tpl: dict):
    if tpl.get("custom") and tpl.get("page_w"):
        return float(tpl["page_w"]), float(tpl["page_h"])
    return portrait(A4) if tpl.get("orientation") == "portrait" else landscape(A4)


def render(tpl: dict, values: dict, assets: dict, meta: dict, qr_on: bool = True) -> bytes:
    """One certificate as PDF bytes. meta: title, author, subject, keywords."""
    w, h = page_size(tpl)
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=(w, h), invariant=1)
    c.setTitle(meta.get("title", "Certificate"))
    c.setAuthor(meta.get("author", ""))
    c.setCreator("SendEZ")
    c.setSubject(meta.get("subject", ""))
    c.setKeywords(meta.get("keywords", ""))
    pdf_bg = None
    if tpl.get("custom"):
        bg = config.UPLOAD_DIR / Path(tpl["background"]).name
        if tpl.get("kind") == "pdf":
            pdf_bg = bg
        elif bg.exists():
            c.drawImage(ImageReader(str(bg)), 0, 0, width=w, height=h)
    else:
        pal = {k: HexColor(v) for k, v in tpl["palette"].items()}
        tpl["decor"](c, w, h, pal)
    draw_fields(c, tpl, w, h, values, assets, qr_on)
    c.showPage()
    c.save()
    data = buf.getvalue()
    if pdf_bg is not None:
        data = _merge_onto_pdf(pdf_bg, data, meta)
    return data


def _merge_onto_pdf(bg_path: Path, overlay: bytes, meta: dict) -> bytes:
    """Imported PDF template: keep its vector artwork and stamp the text layer on top."""
    from pypdf import PdfReader, PdfWriter, Transformation

    base = PdfReader(str(bg_path)).pages[0]
    over = PdfReader(BytesIO(overlay)).pages[0]
    bw, bh = float(base.mediabox.width), float(base.mediabox.height)
    ow, oh = float(over.mediabox.width), float(over.mediabox.height)
    writer = PdfWriter()
    page = writer.add_page(base)
    page.merge_transformed_page(over, Transformation().scale(bw / ow, bh / oh).translate(
        float(base.mediabox.left), float(base.mediabox.bottom)))
    writer.add_metadata({"/Title": meta.get("title", ""), "/Author": meta.get("author", ""), "/Creator": "SendEZ",
                         "/Subject": meta.get("subject", ""), "/Keywords": meta.get("keywords", "")})
    writer._ID = None
    out = BytesIO()
    writer.write(out)
    return out.getvalue()


SAMPLE_VALUES = {"NAME": "Aisha Rahman", "EVENT": "AI Hackathon 2026", "ACHIEVEMENT": "First Place",
                 "DATE": "04 November 2026", "TITLE": "", "CERT_ID": "SS-SAMPLE0000",
                 "DESCRIPTION": "for securing First Place at AI Hackathon 2026, held on 04 November 2026",
                 "ORG_NAME": "Your Organization", "SIGNER1_NAME": "Dr. Priya Sharma", "SIGNER1_TITLE": "Event Coordinator",
                 "SIGNER2_NAME": "Prof. Daniel Okafor", "SIGNER2_TITLE": "Head of Department",
                 "VERIFY_URL": "https://example.org/verify/SS-SAMPLE0000"}
