"""The picture that goes with a request post, laid out like a job post.

A clean white card: the buyer's logo and name top left (a grey building or
person when there is no logo), the Brend Market mark top right, the request
title large and bold, then a few lines with an icon each — budget, amount,
deadlines, city. Drawn at double size and scaled down for smooth edges.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ASSETS = Path(__file__).parent / "assets"
WIDTH = 1080
# The card is as tall as its content: never shorter than this, at most five lines.
MIN_HEIGHT = 560
MAX_LINES = 5
TITLE_TOP = 214
TITLE_LINE = 80
ROW = 58
SCALE = 2
PAD = 64

INK = (10, 10, 12)
TEXT = (24, 26, 32)
LINE = (226, 230, 238)
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

# Line icons, drawn from the same set the site uses.
ICONS = {"budget", "quantity", "proposals", "deadline", "place", "payment"}


class CardUnavailable(Exception):
    """No font with Cyrillic letters on this machine: the post goes out as text."""


@dataclass
class CardData:
    title: str
    buyer_name: str
    buyer_logo: bytes | None = None
    buyer_is_company: bool = True
    # (icon, text) in order of importance; the card keeps as many as fit.
    lines: list[tuple[str, str]] = field(default_factory=list)


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


@lru_cache(maxsize=16)
def _asset(name: str, size: int) -> Image.Image:
    image = Image.open(ASSETS / f"{name}.png").convert("RGBA")
    return image.resize((_s(size), _s(size)), Image.LANCZOS)


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


def _rounded_paste(base: Image.Image, picture: Image.Image, x: int, y: int, size: int, radius: int) -> None:
    """Paste a picture as a rounded square, cropped from its middle like object-fit: cover."""
    picture = picture.convert("RGBA")
    w, h = picture.size
    side = min(w, h)
    picture = picture.crop(((w - side) // 2, (h - side) // 2, (w + side) // 2, (h + side) // 2))
    picture = picture.resize((size, size), Image.LANCZOS)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=255)
    base.paste(picture, (x, y), mask)


def _open_logo(data: bytes | None) -> Image.Image | None:
    if not data:
        return None
    try:
        picture = Image.open(io.BytesIO(data))
        picture.load()
        return picture
    except Exception:  # noqa: BLE001 - a broken logo falls back to the grey icon
        return None


def render_request_card(data: CardData) -> bytes:
    """The card as PNG bytes."""
    # Measure first: the height follows the title and the number of lines.
    measure = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    title_font = _font("bold", 66)
    title_lines = _wrap(measure, data.title, title_font, WIDTH - 2 * PAD, 2)
    rows = data.lines[:MAX_LINES]
    rows_top = TITLE_TOP + TITLE_LINE * len(title_lines) + 46
    height = max(MIN_HEIGHT, int(rows_top + ROW * len(rows) + 48))

    canvas = Image.new("RGB", (_s(WIDTH), _s(height)), WHITE)
    draw = ImageDraw.Draw(canvas)

    # Who asks: their logo and name, top left.
    logo = 92
    top = 56
    picture = _open_logo(data.buyer_logo)
    if picture is not None:
        _rounded_paste(canvas, picture, _s(PAD), _s(top), _s(logo), _s(20))
        draw.rounded_rectangle(
            (_s(PAD), _s(top), _s(PAD + logo), _s(top + logo)), radius=_s(20), outline=LINE, width=_s(2)
        )
    else:
        icon = _asset("icon-company" if data.buyer_is_company else "icon-person", logo)
        canvas.paste(icon, (_s(PAD), _s(top)), icon)
    name_x = PAD + logo + 20
    name_font = _font("bold", 32)
    name_lines = _wrap(draw, data.buyer_name, name_font, 380, 2)
    name_top = top + logo / 2 - 20 * len(name_lines)
    for i, line in enumerate(name_lines):
        draw.text((_s(name_x), _s(name_top + 40 * i)), line, font=name_font, fill=INK)

    # Who publishes: the Brend Market mark and name, top right.
    word_font = _font("bold", 42)
    word = "Brend Market"
    word_w = draw.textlength(word, font=word_font) / SCALE
    mark_size = 68
    word_x = WIDTH - PAD - word_w
    mark_x = word_x - 14 - mark_size
    mark = _asset("brand-mark", mark_size)
    mark_top = top + (logo - mark_size) / 2
    canvas.paste(mark, (_s(mark_x), _s(mark_top)), mark)
    draw.text((_s(word_x), _s(top + logo / 2)), word, font=word_font, fill=INK, anchor="lm")

    # What for: the title, large, up to two lines.
    y = TITLE_TOP
    for line in title_lines:
        draw.text((_s(PAD), _s(y)), line, font=title_font, fill=INK)
        y += TITLE_LINE

    # The facts, one per line with its icon.
    y = rows_top
    text_font = _font("regular", 34)
    for icon_name, text in rows:
        if icon_name in ICONS:
            icon = _asset(f"icon-{icon_name}", 38)
            canvas.paste(icon, (_s(PAD), _s(y + 2)), icon)
        draw.text(
            (_s(PAD + 60), _s(y + 21)),
            _fit(draw, text, text_font, WIDTH - 2 * PAD - 60),
            font=text_font,
            fill=TEXT,
            anchor="lm",
        )
        y += ROW

    out = canvas.resize((WIDTH, height), Image.LANCZOS)
    buffer = io.BytesIO()
    out.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()
