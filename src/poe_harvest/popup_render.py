"""Draws the popup as an image (Pillow), so it looks the same everywhere and can be previewed."""
from __future__ import annotations

import os

from PIL import Image, ImageDraw, ImageFont

FONT_CANDIDATES = {
    "regular": [r"C:\Windows\Fonts\segoeui.ttf", r"C:\Windows\Fonts\arial.ttf",
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"],
    "bold": [r"C:\Windows\Fonts\segoeuib.ttf", r"C:\Windows\Fonts\arialbd.ttf",
             "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"],
}

BG = (14, 13, 18)
BORDER = (150, 118, 60)
DIVIDER = (70, 60, 40)
COLOURS = {
    "title": (240, 215, 130),
    "mod": (150, 150, 255),
    "reward": (230, 160, 60),
    "bad": (255, 95, 95),
    "wisps": (205, 215, 150),
    "sub": (175, 175, 180),
    "status": (255, 230, 150),
    "footer": (125, 125, 135),
    "good": (110, 220, 120),
    "neutral": (230, 200, 90),
}
TYPE_COLOURS = {"Wild": (195, 120, 255), "Vivid": (255, 210, 80), "Primal": (90, 190, 255),
                "Sacred": (240, 240, 240)}

_fonts = {}


def font(kind: str, size: int):
    key = (kind, size)
    if key not in _fonts:
        for path in FONT_CANDIDATES[kind]:
            if os.path.exists(path):
                _fonts[key] = ImageFont.truetype(path, size)
                break
        else:
            _fonts[key] = ImageFont.load_default()
    return _fonts[key]


def _w(draw, text, f):
    l, _, r, _ = draw.textbbox((0, 0), text, font=f)
    return r - l


ICON_COLOURS = {"good": (110, 220, 120), "bad": (255, 95, 95), "neutral": (230, 200, 90)}


def _icon(d, kind, x, y, size, width):
    """✔ / ✘ / ~ drawn as lines, so they don't depend on the font having those characters."""
    c = ICON_COLOURS.get(kind, COLOURS["sub"])
    if kind == "good":
        d.line([(x + size * .12, y + size * .55), (x + size * .40, y + size * .82), (x + size * .90, y + size * .18)],
               fill=c, width=width, joint="curve")
    elif kind == "bad":
        a, b = size * .2, size * .8
        d.line([(x + a, y + a), (x + b, y + b)], fill=c, width=width)
        d.line([(x + a, y + b), (x + b, y + a)], fill=c, width=width)
    else:
        import math
        pts = [(x + size * (.1 + .8 * i / 16), y + size * (.5 - .16 * math.sin(i / 16 * 2 * math.pi))) for i in range(17)]
        d.line(pts, fill=c, width=width, joint="curve")


def _norm(line):
    if isinstance(line, dict):
        return line
    text, colour = line
    return {"text": text, "colour": colour}


def render(spec: dict, scale: float = 1.0) -> Image.Image:
    """spec: {"title", "title_right"?, "type"?, "lines": [(text, colour) or {"text", "colour", "icon"?,
    "right"?, "big"?}], "status"?, "status_colour"?, "footer"?}"""
    s = lambda v: int(round(v * scale))  # noqa: E731
    f_title, f_line, f_small, f_big = font("bold", s(22)), font("regular", s(18)), font("regular", s(15)), font("bold", s(24))
    pad, gap, line_h, small_h, big_h = s(14), s(8), s(26), s(21), s(32)
    icon_w = s(26)
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))

    title = spec.get("title", "")
    right = spec.get("title_right", "")
    lines = [_norm(l) for l in spec.get("lines", [])]
    bottom = [t for t in (spec.get("status", ""), spec.get("footer", "")) if t]
    has_icons = any(l.get("icon") for l in lines)

    width = _w(probe, title, f_title) + (s(30) + _w(probe, right, f_line) if right else 0)
    for l in lines:
        f = f_big if l.get("big") else f_small if l.get("small") else f_line
        w = _w(probe, l["text"], f) + (icon_w if (has_icons and not l.get("big") and not l.get("small")) else 0)
        if l.get("right"):
            w += s(24) + _w(probe, l["right"], f_line)
        width = max(width, w)
    for text in bottom:
        width = max(width, _w(probe, text, f_small))
    width = min(max(width + 2 * pad, s(340)), s(1100))
    height = pad + s(30) + gap + sum(big_h if l.get("big") else small_h if l.get("small") else line_h for l in lines) \
        + (gap + len(bottom) * small_h if bottom else 0) + pad

    img = Image.new("RGB", (width, height), BG)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, width - 1, height - 1], outline=BORDER, width=max(1, s(2)))
    y = pad
    d.text((pad, y), title, font=f_title, fill=COLOURS["title"])
    if right:
        tc = TYPE_COLOURS.get(spec.get("type"), COLOURS["sub"])
        d.text((width - pad - _w(d, right, f_line), y + s(3)), right, font=f_line, fill=tc)
    y += s(30)
    d.line([pad, y + gap // 2, width - pad, y + gap // 2], fill=DIVIDER, width=1)
    y += gap
    for l in lines:
        colour = l.get("rgb") or COLOURS.get(l.get("colour"), COLOURS["sub"])
        if l.get("big"):
            d.text((pad, y), l["text"], font=f_big, fill=colour)
            y += big_h
            continue
        if l.get("small"):
            d.text((pad, y + s(2)), l["text"], font=f_small, fill=colour)
            y += small_h
            continue
        x = pad
        if has_icons:
            if l.get("icon"):
                _icon(d, l["icon"], x, y + s(4), s(17), max(2, s(2.5)))
            x += icon_w
        d.text((x, y), l["text"], font=f_line, fill=colour)
        if l.get("right"):
            rc = ICON_COLOURS.get(l.get("icon"), COLOURS["sub"])
            d.text((width - pad - _w(d, l["right"], f_line), y), l["right"], font=f_line, fill=rc)
        y += line_h
    if bottom:
        d.line([pad, y + gap // 2, width - pad, y + gap // 2], fill=DIVIDER, width=1)
        y += gap
        for i, text in enumerate(bottom):
            colour = spec.get("status_colour", "status") if (i == 0 and spec.get("status")) else "footer"
            d.text((pad, y), text, font=f_small, fill=COLOURS.get(colour, COLOURS["status"]))
            y += small_h
    return img


def place(img_size, anchor, screen, margin=16):
    """Where to put the popup: beside the anchor box (tooltip or labels), never over it.
    anchor = (x0, y0, x1, y1); screen = (left, top, width, height) of the game window."""
    w, h = img_size
    sl, st, sw, sh = screen
    if anchor is None:
        x, y = sw // 2 - w // 2, sh // 3
    else:
        x0, y0, x1, y1 = anchor
        if x1 + margin + w <= sw:
            x = x1 + margin
        elif x0 - margin - w >= 0:
            x = x0 - margin - w
        else:                                   # no room at either side: go below
            x = max(0, min(sw - w, x0))
            y0 = y1 + margin
        y = y0
    x = max(0, min(sw - w, x))
    y = max(0, min(sh - h, y))
    return sl + x, st + y
