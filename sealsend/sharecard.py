"""Help students show off a verified certificate: an "Add to LinkedIn profile" link and a share card
(the 1200x630 picture that WhatsApp, LinkedIn, X and iMessage show when the verify link is shared)."""
from datetime import date, datetime
from io import BytesIO
from pathlib import Path
from urllib.parse import urlencode

from . import config


def certification_name(title: str, event: str) -> str:
    title, event = (title or "").strip(), (event or "").strip()
    if title and event:
        return f"{title} – {event}"
    return title or event or "Certificate"


def linkedin_url(title: str, event: str, issuer: str, verify_url: str, cert_id: str, issued: date | None = None) -> str:
    """LinkedIn's own "add a certification to your profile" link, with every field filled in."""
    issued = issued or date.today()
    return "https://www.linkedin.com/profile/add?" + urlencode({
        "startTask": "CERTIFICATION_NAME", "name": certification_name(title, event), "organizationName": issuer or "",
        "issueYear": issued.year, "issueMonth": issued.month, "certUrl": verify_url, "certId": cert_id})


def issued_on(rec: dict) -> date:
    for key in ("sent_at", "claimed_at", "updated_at"):
        value = rec.get(key) or ""
        if value:
            try:
                return datetime.strptime(value[:10], "%Y-%m-%d").date()
            except ValueError:
                pass
    return date.today()


def _font(name: str, size: int):
    from PIL import ImageFont
    try:
        return ImageFont.truetype(str(config.PACKAGE_DIR / "fonts" / name), size)
    except OSError:
        return ImageFont.load_default(size=size)


def _fit(draw, text: str, name: str, size: int, max_width: int, min_size: int = 26):
    """The largest font (down to min_size) that fits on one line; the text is shortened if it still doesn't."""
    while size > min_size and draw.textlength(text, font=_font(name, size)) > max_width:
        size -= 2
    font = _font(name, size)
    while text and draw.textlength(text, font=font) > max_width:
        text = text[:-2].rstrip() + "…"
    return text, font


def render_card(payload: dict, logo: Path | None = None) -> bytes:
    from PIL import Image, ImageDraw
    w, h, pad = 1200, 630, 72
    im = Image.new("RGB", (w, h), (246, 247, 249))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([28, 28, w - 28, h - 28], radius=28, fill=(255, 255, 255), outline=(226, 229, 234), width=2)
    d.rectangle([28, 120, 36, h - 120], fill=(0, 122, 255))  # thin accent bar
    lg = None
    if logo and Path(logo).exists():
        try:
            lg = Image.open(logo).convert("RGBA")
            lg.thumbnail((420, 96))
        except Exception:
            lg = None
    name, nf = _fit(d, payload.get("name") or "", "Inter-ExtraBold.ttf", 72, w - 2 * pad)
    line = certification_name(payload.get("title", ""), payload.get("event", ""))
    line, lf = _fit(d, line, "Inter-SemiBold.ttf", 36, w - 2 * pad, 22)
    block = (lg.height + 34 if lg else 0) + 78 + nf.size + 22 + lf.size
    top = max(pad, (h - 60 - block) // 2)  # centred above the footer line
    if lg:
        im.paste(lg, (pad, top), lg)
        top += lg.height + 34
    # "Verified" pill
    pill = "VERIFIED CERTIFICATE"
    pf = _font("Inter-SemiBold.ttf", 24)
    pw = d.textlength(pill, font=pf) + 76
    d.rounded_rectangle([pad, top, pad + pw, top + 46], radius=23, fill=(232, 247, 237))
    d.line([(pad + 20, top + 24), (pad + 28, top + 32), (pad + 44, top + 15)], fill=(36, 138, 61), width=5, joint="curve")
    d.text((pad + 56, top + 9), pill, font=pf, fill=(36, 138, 61))
    top += 78
    d.text((pad, top), name, font=nf, fill=(17, 19, 24))
    top += nf.size + 22
    d.text((pad, top), line, font=lf, fill=(60, 64, 72))
    foot = " · ".join(x for x in (payload.get("issuer", ""), f"ID {payload.get('code', '')}") if x.strip())
    foot, ff = _fit(d, foot, "Inter-Regular.ttf", 26, w - 2 * pad, 18)
    d.text((pad, h - pad - 30), foot, font=ff, fill=(110, 116, 128))
    out = BytesIO()
    im.save(out, "PNG", optimize=True)
    return out.getvalue()
