"""Engrave the visitor's name on the award plaque and compose the 9:16 frame the hold segment zooms into.

Everything about the plaque is config (assets/plaque.json): the image, the nameplate box in that
image's pixels, the font and the engraving colour. A different plaque is a JSON edit, not code.
"""
import json
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

from . import config

SCALE = 2  # the card is rendered at 2x so the push-in stays sharp
CARD_W, CARD_H = 720 * SCALE, 1280 * SCALE


@lru_cache(maxsize=1)
def spec() -> dict:
    return json.loads((config.ASSETS_DIR / "plaque.json").read_text())


def _font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(config.ASSETS_DIR / spec()["font"]), size)


def _fit_lines(name: str, box_w: int, box_h: int) -> tuple[list[str], int]:
    """One line if it can be engraved at a readable size, else two lines split at the middle space."""
    one = min(int(box_h * 0.56), 400)
    while one > 8 and _font(one).getlength(name) > box_w:
        one -= 1
    words = name.split(" ")
    if one >= box_h * 0.40 or len(words) == 1:
        return [name], one
    best = min(range(1, len(words)), key=lambda i: abs(len(" ".join(words[:i])) - len(" ".join(words[i:]))))
    lines = [" ".join(words[:best]), " ".join(words[best:])]
    two = int(box_h * 0.38)
    while two > 8 and max(_font(two).getlength(l) for l in lines) > box_w:
        two -= 1
    return (lines, two) if two > one else ([name], one)


def engrave(name: str) -> Image.Image:
    """The plaque photo with the name engraved on the nameplate (at the photo's own resolution)."""
    s = spec()
    plaque = Image.open(config.ASSETS_DIR / s["image"]).convert("RGB")
    x0, y0, x1, y1 = s["nameplate"]
    text = name.upper() if s.get("uppercase", True) else name
    pad_x = int((x1 - x0) * 0.05)
    lines, size = _fit_lines(text, (x1 - x0) - 2 * pad_x, y1 - y0)
    font = _font(size)

    # Text mask, centred in the nameplate. Ink fills the letters; a light lip under each stroke and a
    # dark lip above make it read as cut into the metal rather than printed on top.
    mask = Image.new("L", plaque.size, 0)
    d = ImageDraw.Draw(mask)
    line_h = size * 1.08
    top = (y0 + y1) / 2 - line_h * len(lines) / 2
    for i, line in enumerate(lines):
        bbox = font.getbbox(line, anchor="ls")
        x = (x0 + x1) / 2 - (bbox[2] - bbox[0]) / 2 - bbox[0]
        baseline = top + line_h * i + line_h * 0.82
        d.text((x, baseline), line, font=font, fill=255, anchor="ls")

    lip = max(1, size // 22)
    highlight = ImageChops.offset(mask, 0, lip).filter(ImageFilter.GaussianBlur(0.6))
    shadow = ImageChops.offset(mask, 0, -lip).filter(ImageFilter.GaussianBlur(0.6))
    out = plaque.copy()
    out.paste((255, 236, 170), (0, 0), highlight.point(lambda v: v * 0.55))
    out.paste((20, 12, 2), (0, 0), shadow.point(lambda v: v * 0.45))
    out.paste(tuple(s.get("text_color", (43, 28, 8))), (0, 0), mask.point(lambda v: v * 0.93))
    return out


def render_card(name: str, out: Path) -> tuple[float, float]:
    """Compose the engraved plaque onto a 9:16 card (blurred plaque behind, edges feathered).

    Returns the nameplate centre in card pixels, the point the push-in zooms toward.
    """
    plaque = engrave(name)
    pw, ph = plaque.size

    cover = max(CARD_W / pw, CARD_H / ph)
    bg = plaque.resize((round(pw * cover), round(ph * cover)), Image.LANCZOS)
    left, top = (bg.width - CARD_W) // 2, (bg.height - CARD_H) // 2
    bg = bg.crop((left, top, left + CARD_W, top + CARD_H)).filter(ImageFilter.GaussianBlur(40))
    bg = Image.eval(bg, lambda v: int(v * 0.45))

    fit = CARD_W / pw
    fg = plaque.resize((CARD_W, round(ph * fit)), Image.LANCZOS)
    feather = Image.new("L", fg.size, 255)
    fd = ImageDraw.Draw(feather)
    edge = int(fg.height * 0.08)
    for i in range(edge):
        a = int(255 * i / edge)
        fd.line([(0, i), (fg.width, i)], fill=a)
        fd.line([(0, fg.height - 1 - i), (fg.width, fg.height - 1 - i)], fill=a)
    fy = (CARD_H - fg.height) // 2
    bg.paste(fg, (0, fy), feather)
    bg.save(out)

    x0, y0, x1, y1 = spec()["nameplate"]
    return (x0 + x1) / 2 * fit, fy + (y0 + y1) / 2 * fit
