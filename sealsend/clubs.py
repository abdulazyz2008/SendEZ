"""Mahindra University and its clubs.

Picking a club for an event puts "Mahindra University · <Club>" and a combined university + club logo
on every certificate. Logos are uploaded in Settings (or shipped in static/clubs/<id>.png); until a club
has a logo, its button shows a neat placeholder with the club's initials.
"""
import hashlib
from io import BytesIO
from pathlib import Path

from . import config, settings

UNIVERSITY = "Mahindra University"

# id, name, what the club is, default certificate design for its events (changeable per event)
CLUBS = [
    ("kalakriti", "Kalakriti", "Cultural & Performing Arts Club", "arts-ornate"),
    ("roboverse", "Roboverse", "Robotics Club", "tech-hackathon"),
    ("synolo", "Synolo", "Music Club", "arts-ornate"),
    ("outreach", "Outreach Club", "Community Service & Social Welfare", "ngo-appreciation"),
    ("erudite", "The Erudite", "Literary & Debate Society", "university-classic"),
    ("coding-ninjas", "Coding Ninjas", "Programming & Computer Science Chapter", "tech-hackathon"),
    ("art-felt", "Art Felt", "Fine Arts & Design Club", "arts-ornate"),
    ("palaestro", "Palaestro", "Fitness & Sports Club", "sports-champion"),
    ("celeste", "Celeste", "Astronomy & Astrophysics Club", "university-classic"),
    ("aeiforia", "AeiForia", "Sustainability & Environmental Awareness Club", "ngo-appreciation"),
]
CLUB_BY_ID = {c[0]: {"id": c[0], "name": c[1], "about": c[2], "template": c[3]} for c in CLUBS}
BUNDLED = config.PACKAGE_DIR / "static" / "clubs"


def _stored(key: str) -> str:
    return (settings.load().get("university_logos") or {}).get(key, "")


def logo_path(key: str) -> Path | None:
    """key is 'university' or a club id. Uploaded logo first, then one shipped with the app."""
    name = _stored(key)
    if name and (config.UPLOAD_DIR / Path(name).name).exists():
        return config.UPLOAD_DIR / Path(name).name
    for ext in (".png", ".jpg"):
        p = BUNDLED / f"{key}{ext}"
        if p.exists():
            return p
    return None


def set_logo(key: str, data: bytes) -> str:
    from PIL import Image
    if key != "university" and key not in CLUB_BY_ID:
        raise ValueError("Unknown club")
    try:
        im = Image.open(BytesIO(data))
        im.load()
    except Exception as e:
        raise ValueError("That image could not be read") from e
    im = im.convert("RGBA")
    im.thumbnail((1200, 1200))
    name = f"logo_{key}_{hashlib.sha1(data).hexdigest()[:10]}.png"
    im.save(config.UPLOAD_DIR / name, "PNG", optimize=True)
    s = settings.load()
    s.setdefault("university_logos", {})[key] = name
    settings.save(s)
    return name


def placeholder_png(key: str, size: int = 256) -> bytes:
    """Initials on a soft rounded square: used until a club's real logo is uploaded."""
    from PIL import Image, ImageDraw, ImageFont
    title = UNIVERSITY if key == "university" else CLUB_BY_ID[key]["name"]
    words = [w for w in title.replace("The ", "").split() if w[0].isalnum()]
    initials = "".join(w[0] for w in words[:2]).upper() or "?"
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=size // 4, fill=(28, 28, 30, 255))
    try:
        font = ImageFont.truetype(str(config.PACKAGE_DIR / "fonts" / "Inter-ExtraBold.ttf"), int(size * 0.38))
    except OSError:  # font missing or unreadable: still draw the initials
        font = ImageFont.load_default(size=int(size * 0.38))
    box = d.textbbox((0, 0), initials, font=font)
    d.text(((size - (box[2] - box[0])) / 2 - box[0], (size - (box[3] - box[1])) / 2 - box[1]), initials,
           font=font, fill=(255, 255, 255, 255))
    out = BytesIO()
    im.save(out, "PNG")
    return out.getvalue()


def composite_logo(club_id: str) -> str:
    """University logo | club logo, side by side on one transparent image, for the certificate's logo slot.
    Returns a file name in the uploads folder ('' if neither logo exists yet)."""
    from PIL import Image
    paths = [p for p in (logo_path("university"), logo_path(club_id)) if p]
    if not paths:
        return ""
    key = hashlib.sha1("|".join(f"{p}:{p.stat().st_mtime_ns}" for p in paths).encode()).hexdigest()[:12]
    name = f"logo_combo_{club_id}_{key}.png"
    dest = config.UPLOAD_DIR / name
    if dest.exists():
        return name
    h = 300
    imgs = []
    for p in paths:
        im = Image.open(p).convert("RGBA")
        bbox = im.getbbox()  # trim transparent margins so both logos look the same size
        if bbox:
            im = im.crop(bbox)
        imgs.append(im.resize((max(1, round(im.width * h / im.height)), h)))
    gap = 70 if len(imgs) > 1 else 0
    canvas = Image.new("RGBA", (sum(i.width for i in imgs) + gap, h), (0, 0, 0, 0))
    x = 0
    for i, im in enumerate(imgs):
        canvas.paste(im, (x, 0), im)
        x += im.width
        if i == 0 and gap:
            for yy in range(int(h * 0.12), int(h * 0.88)):  # thin divider between the two logos
                for xx in range(x + gap // 2 - 1, x + gap // 2 + 2):
                    canvas.putpixel((xx, yy), (170, 170, 170, 255))
            x += gap
    canvas.save(dest, "PNG", optimize=True)
    return name


def branding_for(club_id: str) -> dict:
    """Certificate branding for an event run by this club (signatories come from the saved defaults)."""
    club = CLUB_BY_ID[club_id]
    org = settings.org()
    return {**org, "org_name": f"{UNIVERSITY} · {club['name']}", "logo": composite_logo(club_id)}


def catalog() -> dict:
    return {"university": {"name": UNIVERSITY, "has_logo": bool(logo_path("university"))},
            "clubs": [{**c, "has_logo": bool(logo_path(c["id"]))} for c in CLUB_BY_ID.values()]}
