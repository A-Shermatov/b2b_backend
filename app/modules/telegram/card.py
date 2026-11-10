"""The picture that goes with a request post: who asks, what for, for how much.

A white card like a job post: the buyer's logo top left, the Brend Market mark
top right, the request title large, the key facts underneath and a blue strip
with the site address. Drawn at double size and scaled down for smooth edges.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ASSETS = Path(__file__).parent / "assets"
WIDTH, HEIGHT = 1200, 750
SCALE = 2

INK = (11, 21, 48)
MUTE = (100, 112, 138)
LINE = (227, 232, 242)
BRAND = (26, 95, 216)
BRAND_DEEP = (10, 58, 160)
BRAND_50 = (238, 244, 255)
VOLT = (217, 245, 70)
VOLT_INK = (28, 36, 0)
WHITE = (255, 255, 255)

# Fonts in the order they are tried: the server image installs Inter and
# DejaVu; a developer's Windows machine has Segoe UI and Arial.
FONT_ROOTS = [Path("/usr/share/fonts"), Path("/usr/local/share/fonts"), Path("C:/Windows/Fonts")]
FONT_NAMES = {
    "bold": ["Inter-Bold.otf", "Inter-Bold.ttf", "DejaVuSans-Bold.ttf", "segoeuib.ttf", "arialbd.ttf"],
    "semibold": [
        "Inter-SemiBold.otf",
        "Inter-SemiBold.ttf",
        "DejaVuSans-Bold.ttf",
        "seguisb.ttf",
        "segoeuib.ttf",
        "arialbd.ttf",
    ],
    "regular": ["Inter-Regular.otf", "Inter-Regular.ttf", "DejaVuSans.ttf", "segoeui.ttf", "arial.ttf"],
}


class CardUnavailable(Exception):
    """No font with Cyrillic letters on this machine: the post goes out as text."""


@dataclass
class CardData:
    title: str
    category: str | None
    buyer_name: str
    buyer_logo: bytes | None = None
    facts: list[tuple[str, str]] = field(default_factory=list)
    number: str = ""
    site_host: str = ""


@lru_cache(maxsize=8)
def _font_path(weight: str) -> str:
    for name in FONT_NAMES[weight]:
        for root in FONT_ROOTS:
            if not root.exists():
                continue
            direct = root / name
            if direct.exists():
                return str(direct)
            found = next(root.rglob(name), None)
            if found:
                return str(found)
    raise CardUnavailable(f"No {weight} font found")


def _font(weight: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(_font_path(weight), size * SCALE)


def fonts_available() -> bool:
    try:
        for weight in FONT_NAMES:
            _font_path(weight)
    except CardUnavailable:
        return False
    return True


def _s(value: float) -> int:
    return round(value * SCALE)


def _fit(draw: ImageDraw.ImageDraw, text: str, font, max_width: float) -> str:
    """The text, cut with an ellipsis so it fits the width."""
    if draw.textlength(text, font=font) <= _s(max_width):
        return text
    while text and draw.textlength(text + "…", font=font) > _s(max_width):
        text = text[:-1]
    return text.rstrip() + "…"


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_width: float, lines: int) -> list[str]:
    words = text.split()
    result: list[str] = []
    current = ""
    for i, word in enumerate(words):
        candidate = f"{current} {word}".strip()
        if draw.textlength(candidate, font=font) <= _s(max_width):
            current = candidate
            continue
        if current:
            result.append(current)
        current = word
        if len(result) == lines - 1:
            rest = " ".join([current, *words[i + 1 :]])
            result.append(_fit(draw, rest, font, max_width))
            return result
    if current:
        result.append(_fit(draw, current, font, max_width))
    return result[:lines]


def _initials(name: str) -> str:
    letters = [part[0].upper() for part in name.replace("«", " ").replace("»", " ").split() if part[:1].isalnum()]
    return "".join(letters[:2]) or "?"


def _rounded_paste(base: Image.Image, picture: Image.Image, box: tuple[int, int, int], radius: int) -> None:
    """Paste a square picture with rounded corners at (x, y) of the given size."""
    x, y, size = box
    picture = picture.convert("RGBA")
    # Fill the square, cropping the middle, like object-fit: cover.
    w, h = picture.size
    side = min(w, h)
    picture = picture.crop(((w - side) // 2, (h - side) // 2, (w + side) // 2, (h + side) // 2))
    picture = picture.resize((size, size), Image.LANCZOS)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=255)
    base.paste(picture, (x, y), mask)


def render_request_card(data: CardData) -> bytes:
    """The card as PNG bytes."""
    canvas = Image.new("RGB", (_s(WIDTH), _s(HEIGHT)), WHITE)
    draw = ImageDraw.Draw(canvas)
    pad = 64

    # Who asks: the buyer's logo, or their initials on a soft blue tile.
    logo_size = 96
    tile = (_s(pad), _s(56), _s(logo_size))
    picture = None
    if data.buyer_logo:
        try:
            picture = Image.open(io.BytesIO(data.buyer_logo))
            picture.load()
        except Exception:  # noqa: BLE001 - a broken logo falls back to initials
            picture = None
    if picture is not None:
        _rounded_paste(canvas, picture, tile, _s(24))
        draw.rounded_rectangle(
            (tile[0], tile[1], tile[0] + tile[2], tile[1] + tile[2]),
            radius=_s(24),
            outline=LINE,
            width=_s(2),
        )
    else:
        draw.rounded_rectangle(
            (tile[0], tile[1], tile[0] + tile[2], tile[1] + tile[2]), radius=_s(24), fill=BRAND_50
        )
        draw.text(
            (tile[0] + tile[2] / 2, tile[1] + tile[2] / 2),
            _initials(data.buyer_name),
            font=_font("bold", 36),
            fill=BRAND,
            anchor="mm",
        )
    text_x = pad + logo_size + 22
    draw.text((_s(text_x), _s(70)), "Заказчик", font=_font("regular", 22), fill=MUTE)
    draw.text(
        (_s(text_x), _s(100)),
        _fit(draw, data.buyer_name, _font("semibold", 30), 470),
        font=_font("semibold", 30),
        fill=INK,
    )

    # Who publishes: the Brend Market mark and name, top right.
    word_font = _font("bold", 34)
    word = "Brend Market"
    word_w = draw.textlength(word, font=word_font) / SCALE
    mark_size = 64
    word_x = WIDTH - pad - word_w
    mark_x = word_x - 14 - mark_size
    mark = Image.open(ASSETS / "brand-mark.png").convert("RGBA").resize((_s(mark_size), _s(mark_size)), Image.LANCZOS)
    canvas.paste(mark, (_s(mark_x), _s(72)), mark)
    draw.text((_s(word_x), _s(72 + mark_size / 2)), word, font=word_font, fill=INK, anchor="lm")

    # The middle block — chip, title, facts — sits centred between the header
    # and the strip, whether the title takes one line or two.
    title_font = _font("bold", 58)
    title_lines = _wrap(draw, data.title, title_font, WIDTH - 2 * pad, 2)
    facts = data.facts[:4]
    block = (66 if data.category else 0) + 70 * len(title_lines) + (28 + 80 if facts else 0)
    top, bottom = 176, HEIGHT - 92 - 24
    y = top + max(0, (bottom - top - block) / 2)

    # What for: the category as a lime chip, then the title.
    if data.category:
        chip_font = _font("semibold", 24)
        chip_text = _fit(draw, data.category, chip_font, 700)
        chip_w = draw.textlength(chip_text, font=chip_font) / SCALE + 36
        draw.rounded_rectangle((_s(pad), _s(y), _s(pad + chip_w), _s(y + 44)), radius=_s(22), fill=VOLT)
        draw.text((_s(pad + 18), _s(y + 22)), chip_text, font=chip_font, fill=VOLT_INK, anchor="lm")
        y += 66
    for line in title_lines:
        draw.text((_s(pad), _s(y)), line, font=title_font, fill=INK)
        y += 70

    # How much and by when: up to four facts side by side, each column as
    # wide as its text needs, the spare room shared out evenly.
    if facts:
        y += 28
        value_font = _font("bold", 32)
        label_font = _font("regular", 22)
        gap = 48
        natural = [
            max(draw.textlength(v, font=value_font), draw.textlength(lbl, font=label_font)) / SCALE
            for lbl, v in facts
        ]
        room = WIDTH - 2 * pad - gap * (len(facts) - 1)
        if sum(natural) <= room:
            spare = (room - sum(natural)) / len(facts)
            widths = [w + spare for w in natural]
        else:
            widths = [w * room / sum(natural) for w in natural]
        x = pad
        for i, ((label, value), width) in enumerate(zip(facts, widths)):
            if i:
                line_x = x - gap / 2
                draw.line((_s(line_x), _s(y + 4), _s(line_x), _s(y + 78)), fill=LINE, width=_s(2))
            draw.text((_s(x), _s(y)), label, font=label_font, fill=MUTE)
            draw.text((_s(x), _s(y + 34)), _fit(draw, value, value_font, width), font=value_font, fill=INK)
            x += width + gap

    # Where to answer: a blue strip with the site and the request number.
    strip_top = HEIGHT - 92
    for row in range(_s(92)):
        t = row / _s(92)
        color = tuple(int(BRAND[c] + (BRAND_DEEP[c] - BRAND[c]) * t) for c in range(3))
        draw.line((0, _s(strip_top) + row, _s(WIDTH), _s(strip_top) + row), fill=color)
    draw.ellipse(
        (_s(pad), _s(strip_top + 38), _s(pad + 16), _s(strip_top + 54)), fill=VOLT
    )
    draw.text(
        (_s(pad + 30), _s(strip_top + 46)),
        f"Откликнитесь на {data.site_host}" if data.site_host else "Откликнитесь на Brend Market",
        font=_font("semibold", 28),
        fill=WHITE,
        anchor="lm",
    )
    if data.number:
        draw.text(
            (_s(WIDTH - pad), _s(strip_top + 46)),
            f"Заявка {data.number}",
            font=_font("regular", 24),
            fill=(214, 226, 255),
            anchor="rm",
        )

    out = canvas.resize((WIDTH, HEIGHT), Image.LANCZOS)
    buffer = io.BytesIO()
    out.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()
