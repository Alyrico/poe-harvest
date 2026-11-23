"""
PoE Harvest - screenshot reader (phase 1)

Reads, from one Path of Exile screenshot (any resolution: it's scaled to 1440 pixels tall first,
the size everything here was measured at):
  * the Sacred Flora / Sacred Specimen tooltip nearest the mouse
  * every magenta Crystallised Lifeforce label
  * the Wildwood wisp counter (yellow / blue / purple), if shown

Everything here works on a single image; nothing talks to the game.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_DIR = os.path.join(HERE, "assets", "templates")
REFERENCE_HEIGHT = 1440          # every pixel size in this file is for a 1440-pixel-tall game window


def normalize(img: np.ndarray):
    """Scale a screenshot to 1440 pixels tall (keeping its shape). Returns (image, scale), where
    scale = reference pixels per screen pixel. 1080p is enlarged by 1.33, 4K shrunk by 0.67."""
    h = img.shape[0]
    if h == REFERENCE_HEIGHT or h < 200:
        return img, 1.0
    s = REFERENCE_HEIGHT / h
    interp = cv2.INTER_CUBIC if s > 1 else cv2.INTER_AREA
    return cv2.resize(img, (int(round(img.shape[1] * s)), REFERENCE_HEIGHT), interpolation=interp), s

# ---------------------------------------------------------------------------
# Colours (RGB) of text inside tooltips
# ---------------------------------------------------------------------------
TEXT_COLOURS = {
    "mod":      (135, 135, 254),   # mod lines, "Guarded by Sacred Fauna", magic names
    "reward":   (219, 152, 42),    # "Tier 3 Sacred Fauna drop an additional ..."
    "rare":     (254, 254, 118),   # rare plant name + base
    "normal":   (200, 200, 200),   # normal plant name + base
    "specimen": (130, 230, 230),   # "Wild Specimen"
    "grey":     (124, 124, 127),   # "Interact to begin Sacred Flora encounter"
}


def colour_alpha(bgr: np.ndarray, rgb: tuple[int, int, int], tol: float = 0.18) -> np.ndarray:
    """How strongly each pixel looks like anti-aliased text of colour `rgb` on a dark background.
    Text pixels are roughly a*colour + (1-a)*dark, so we project each pixel onto the colour
    direction and reject pixels whose hue doesn't fit. Returns a float image in 0..1."""
    c = np.array(rgb[::-1], dtype=np.float32)             # to BGR
    p = bgr.astype(np.float32)
    cn = float(c @ c)
    a = (p @ c) / cn                                       # projection length
    resid = np.linalg.norm(p - a[..., None] * c, axis=2) / (np.sqrt(cn) + 1e-6)
    a = np.clip(a, 0, 1)
    a[resid > tol + 0.25 * (1 - a)] = 0                    # allow more slack on faint edge pixels
    return a


# ---------------------------------------------------------------------------
# Tooltip detection
# ---------------------------------------------------------------------------
@dataclass
class Tooltip:
    kind: str            # "flora" or "specimen"
    x0: int
    y0: int
    x1: int
    y1: int              # bottom of the header
    score: float
    traced: str = ""     # "left"/"right" when that corner was hidden and the edge was traced
    frame: str = ""      # which header frame matched (see FRAMES)


# header frames: template file stem -> the kind of tooltip it belongs to.
#   flora:        a rare plant (gold frame, two lines: name + base)
#   flora_single: a normal (white) or magic plant (one line, plain frame) - white maps
#   specimen:     a Sacred Specimen (boss)
FRAMES = {"flora": "flora", "flora_single": "flora", "specimen": "specimen"}


def _load_templates():
    t = {}
    for frame in FRAMES:
        left = cv2.imread(os.path.join(TEMPLATE_DIR, f"{frame}_corner_left.png"))
        if left is None:
            raise FileNotFoundError(f"missing template for {frame}")
        t[frame] = (left, cv2.flip(left, 1))
    return t


_TEMPLATES = None

# How to recognise the header frame's border, and which rows (relative to the corner template's
# top) hold its bottom border. The bottom border is used for tracing a hidden corner: the top
# border can disappear under the buff icons at the top of the screen, the bottom one rarely does.
_BORDER = {
    "flora":    dict(row=3, bottom=(69, 75), test=lambda r, g, b: (r > 140) & (g > 100) & (b < 110) & (r > b + 60)),
    # plain frame: a light line, grey for normal plants (blue-ish for magic, so only brightness counts)
    "flora_single": dict(row=2, bottom=(40, 44), test=lambda r, g, b: (r + g + b > 330) & (np.abs(r - g) < 60)),
    "specimen": dict(row=3, bottom=(55, 60), test=lambda r, g, b: (b > 90) & (b > r + 30) & (g > 50)),
}


def _peaks(res, thr, min_dist=20):
    ys, xs = np.where(res > thr)
    pts = sorted(zip(res[ys, xs].tolist(), xs.tolist(), ys.tolist()), reverse=True)
    keep = []
    for s, x, y in pts:
        if all(abs(x - kx) > min_dist or abs(y - ky) > min_dist for _, kx, ky in keep):
            keep.append((s, x, y))
    return keep


def _trace_border(img, frame, y_top, x_start, step, max_gap=40):
    """Follow the header's bottom border from x_start in direction `step` (+1/-1), bridging small
    gaps. Returns the last x that was still border. y_top = top of the corner template."""
    test = _BORDER[frame]["test"]
    lo, hi = _BORDER[frame]["bottom"]
    h, w = img.shape[:2]
    rows = img[max(0, y_top + lo):min(h, y_top + hi)].astype(int)
    if rows.size == 0:
        return x_start
    b, g, r = rows[..., 0], rows[..., 1], rows[..., 2]
    on = test(r, g, b).any(axis=0)
    x, last, gap = x_start, x_start, 0
    while 0 <= x < w:
        if on[x]:
            last, gap = x, 0
        else:
            gap += 1
            if gap > max_gap:
                break
        x += step
    return last


def _match(img, small, tmpl, tmpl_small, thr):
    """Template peaks, found at half resolution then refined at full resolution
    (about 15x faster than searching the whole 2560x1440 screen at full size)."""
    th, tw = tmpl.shape[:2]
    H, W = img.shape[:2]
    out = []
    for s_, x, y in _peaks(cv2.matchTemplate(small, tmpl_small, cv2.TM_CCOEFF_NORMED), thr - 0.15, 10):
        x0, y0 = max(0, 2 * x - 8), max(0, 2 * y - 8)
        x1, y1 = min(W, 2 * x + tw + 8), min(H, 2 * y + th + 8)
        if x1 - x0 < tw or y1 - y0 < th:
            continue
        res = cv2.matchTemplate(img[y0:y1, x0:x1], tmpl, cv2.TM_CCOEFF_NORMED)
        _, best, _, (bx, by) = cv2.minMaxLoc(res)
        if best > thr:
            out.append((float(best), x0 + bx, y0 + by))
    # the coarse pass can report the same corner twice
    keep = []
    for p in sorted(out, reverse=True):
        if all(abs(p[1] - k[1]) > 20 or abs(p[2] - k[2]) > 20 for k in keep):
            keep.append(p)
    return keep


def find_tooltips(img: np.ndarray) -> list[Tooltip]:
    global _TEMPLATES
    if _TEMPLATES is None:
        _TEMPLATES = _load_templates()
    small = cv2.resize(img, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
    found: list[Tooltip] = []
    for frame, (tl, tr) in _TEMPLATES.items():
        kind = FRAMES[frame]
        h, w = tl.shape[:2]
        tls = cv2.resize(tl, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
        trs = cv2.resize(tr, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
        lefts = _match(img, small, tl, tls, 0.72)
        rights = _match(img, small, tr, trs, 0.65)
        used_r = set()
        for sl, xl, yl in lefts:
            cands = [(i, sr, xr, yr) for i, (sr, xr, yr) in enumerate(rights)
                     if abs(yr - yl) <= 4 and xr > xl + 100 and i not in used_r]
            if cands:
                i, sr, xr, yr = min(cands, key=lambda c: c[2])
                used_r.add(i)
                found.append(Tooltip(kind, xl, yl, xr + w, yl + h, round(min(sl, sr), 3), frame=frame))
            elif sl > 0.8:   # right corner hidden: trace the border to find the right edge
                xr = _trace_border(img, frame, yl, xl + w, +1)
                if xr - xl > 250:
                    found.append(Tooltip(kind, xl, yl, xr + 1, yl + h, round(sl, 3), traced="right", frame=frame))
        for i, (sr, xr, yr) in enumerate(rights):
            if i in used_r or sr < 0.8:
                continue
            xl = _trace_border(img, frame, yr, xr, -1)
            if xr + w - xl > 250:
                found.append(Tooltip(kind, xl, yr, xr + w, yr + h, round(sr, 3), traced="left", frame=frame))
    # two frames matching the same tooltip: keep the better match
    keep: list[Tooltip] = []
    for t in sorted(found, key=lambda t: -t.score):
        if all(min(t.x1, k.x1) - max(t.x0, k.x0) < 50 or min(t.y1, k.y1) - max(t.y0, k.y0) < 10 for k in keep):
            keep.append(t)
    return keep


def nearest_tooltip(tooltips: list[Tooltip], cursor: tuple[int, int] | None):
    if not tooltips:
        return None
    if cursor is None:
        return max(tooltips, key=lambda t: t.score)
    cx, cy = cursor

    def dist(t):
        dx = max(t.x0 - cx, 0, cx - t.x1)
        dy = max(t.y0 - cy, 0, cy - (t.y1 + 200))   # body extends ~200px below the header
        return dx * dx + dy * dy
    return min(tooltips, key=dist)


# ---------------------------------------------------------------------------
# OCR engines
# ---------------------------------------------------------------------------
class OcrEngine:
    name = "base"
    scale = 2.0          # how much to enlarge a line before reading it

    def read_line(self, gray: np.ndarray, digits_only: bool = False) -> str:
        raise NotImplementedError

    def read_lines(self, grays: list, digits_only: bool = False) -> list[str]:
        return [self.read_line(g, digits_only) for g in grays]


class TesseractEngine(OcrEngine):
    name = "tesseract"

    def __init__(self, exe: str | None = None):
        import pytesseract
        if exe:
            pytesseract.pytesseract.tesseract_cmd = exe
        self.pt = pytesseract

    def read_line(self, gray, digits_only=False):
        cfg = "--psm 7 -l eng"
        if digits_only:
            cfg += " -c tessedit_char_whitelist=0123456789,"
        return self.pt.image_to_string(gray, config=cfg).strip()

    def read_lines(self, grays, digits_only=False):
        """All lines in one Tesseract call (starting Tesseract is most of the cost):
        stack them with white gaps and read as a block. Falls back to one call per
        line if the block doesn't come back with the same number of lines."""
        if len(grays) <= 1:
            return [self.read_line(g, digits_only) for g in grays]
        gap = 28
        width = max(g.shape[1] for g in grays)
        parts = []
        for g in grays:
            parts.append(cv2.copyMakeBorder(g, 0, gap, 0, width - g.shape[1], cv2.BORDER_CONSTANT, value=255))
        sheet = np.vstack(parts)
        cfg = "--psm 6 -l eng"
        if digits_only:
            cfg += " -c tessedit_char_whitelist=0123456789,"
        text = self.pt.image_to_string(sheet, config=cfg)
        lines = [t.strip() for t in text.splitlines() if t.strip()]
        if len(lines) == len(grays):
            return lines
        return [self.read_line(g, digits_only) for g in grays]


class RapidEngine(OcrEngine):
    name = "rapidocr"
    scale = 1.0          # its recogniser resizes to 48px itself; enlarging first hurts it

    def __init__(self):
        from rapidocr_onnxruntime import RapidOCR
        self.ocr = RapidOCR()

    def read_line(self, gray, digits_only=False):
        img = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
        res, _ = self.ocr(img, use_det=False, use_cls=False, use_rec=True)
        if not res:
            return ""
        return str(res[0][1]).strip()


def _bundled_tesseract():
    """The copy of Tesseract installed with the app (next to the .exe), if any."""
    import sys
    roots = [os.path.dirname(sys.executable), getattr(sys, "_MEIPASS", ""), os.path.dirname(os.path.dirname(HERE))]
    for root in roots:
        exe = os.path.join(root, "tesseract", "tesseract.exe")
        if root and os.path.isfile(exe):
            return exe
    return None


TESSERACT_PLACES = [
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe"),
]


def find_tesseract() -> str | None:
    import shutil
    exe = _bundled_tesseract() or shutil.which("tesseract")
    if exe:
        return exe
    return next((p for p in TESSERACT_PLACES if os.path.isfile(p)), None)


def make_engine(name: str = "auto") -> OcrEngine:
    """Tesseract reads these tooltips best (271/271 on the first 48 screenshots, vs 236/271
    for RapidOCR), so it's the default; RapidOCR is only a fallback."""
    if name in ("tesseract", "auto"):
        exe = find_tesseract()
        if exe:
            return TesseractEngine(exe)
        if name == "tesseract":
            raise FileNotFoundError("The text reader (Tesseract) is missing - reinstall PoE Harvest Tracker")
    return RapidEngine()


def _line_image(alpha: np.ndarray, scale: float = 2.0, pad: int = 6) -> np.ndarray:
    """Black text on white, upscaled - what OCR engines like best. The background level
    (e.g. the header's brown gradient) is stretched away so it comes out pure white."""
    lo = float(np.median(alpha))
    hi = max(float(np.percentile(alpha, 99)), lo + 0.2)
    a = np.clip((alpha - lo) / (hi - lo), 0, 1)
    g = (255 - a * 255).astype(np.uint8)
    g = cv2.copyMakeBorder(g, pad, pad, pad * 2, pad * 2, cv2.BORDER_CONSTANT, value=255)
    if scale != 1:
        g = cv2.resize(g, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    return g


# ---------------------------------------------------------------------------
# Reading a tooltip
# ---------------------------------------------------------------------------
@dataclass
class TextLine:
    y0: int
    y1: int
    x0: int
    x1: int
    colour: str
    text: str = ""


@dataclass
class TooltipReading:
    kind: str
    name: str = ""
    base: str = ""
    rarity: str = ""
    lines: list[TextLine] = field(default_factory=list)
    box: tuple = ()
    bottom: int = 0          # y of the last text line (for placing the popup)


def _runs(profile, min_count, min_len=1):
    out, start = [], None
    for i, c in enumerate(profile):
        if c >= min_count and start is None:
            start = i
        elif c < min_count and start is not None:
            if i - start >= min_len:
                out.append((start, i))
            start = None
    if start is not None and len(profile) - start >= min_len:
        out.append((start, len(profile)))
    return out


def _centred_extent(mask_cols: np.ndarray, centre: int, max_gap: int = 22, symmetric: bool = True):
    """Columns of one centred text line: grow outwards from the centre, bridging word gaps,
    then trim whichever side is longer so the line stays centred (cuts off background text
    showing through the tooltip at one edge)."""
    on = np.where(mask_cols)[0]
    if len(on) == 0:
        return None
    # start from the text column closest to the centre
    start = on[np.argmin(np.abs(on - centre))]
    left = right = start
    gap = 0
    x = start
    while x > 0:
        x -= 1
        if mask_cols[x]:
            left, gap = x, 0
        else:
            gap += 1
            if gap > max_gap:
                break
    gap = 0
    x = start
    while x < len(mask_cols) - 1:
        x += 1
        if mask_cols[x]:
            right, gap = x, 0
        else:
            gap += 1
            if gap > max_gap:
                break
    half_l, half_r = centre - left, right - centre
    if symmetric and abs(half_l - half_r) > 24:   # lopsided: background text glued to one side
        half = min(half_l, half_r) + 6
        left, right = max(left, centre - half), min(right, centre + half)
    return int(left), int(right)


def read_tooltip(img: np.ndarray, tt: Tooltip, engine: OcrEngine) -> TooltipReading:
    H, W = img.shape[:2]
    x0, x1 = max(0, tt.x0), min(W, tt.x1)
    out = TooltipReading(kind=tt.kind, box=(tt.x0, tt.y0, tt.x1, tt.y1))
    sc = engine.scale

    # ---- header: name (+ base type for flora) ----
    hx0 = x0 + 40
    hdr = img[tt.y0:tt.y1, hx0:max(hx0 + 1, x1 - 40)]
    name_colours = ["rare", "mod", "normal"] if tt.kind == "flora" else ["specimen"]
    alphas = {c: colour_alpha(hdr, TEXT_COLOURS[c]) for c in name_colours}
    best = max(alphas, key=lambda c: float((alphas[c] > 0.5).sum()))
    out.rarity = {"rare": "rare", "mod": "magic", "normal": "normal", "specimen": "specimen"}[best]
    a = alphas[best]
    rows = _runs((a > 0.45).sum(1), 6, min_len=8)
    header_imgs, text_centre = [], None
    for r0, r1 in rows:
        sub = a[max(0, r0 - 3):r1 + 3]
        # centred on the box (a hidden corner is traced along the bottom border, so the box is
        # reliable): trims buff icons or other UI overlapping one end of the header
        ext = _centred_extent((sub > 0.45).any(0), sub.shape[1] // 2, max_gap=30)
        if ext is None:
            continue
        l, r = ext
        if text_centre is None:
            text_centre = hx0 + (l + r) // 2
        header_imgs.append(_line_image(sub[:, max(0, l - 4):r + 5], scale=sc))

    # A hidden corner means the traced edge may be wrong: the text is centred, so mirror
    # the edge we trust around the centre of the name.
    if text_centre is not None and tt.traced == "left":
        x0 = max(0, 2 * text_centre - x1)
    elif text_centre is not None and tt.traced == "right":
        x1 = min(W, 2 * text_centre - x0)
    centre_abs = text_centre if text_centre is not None else (x0 + x1) // 2
    out.box = (x0, tt.y0, x1, tt.y1)

    # ---- body ----
    body_colours = ["mod", "reward"] if tt.kind == "flora" else ["grey"]
    y_start = tt.y1
    y_end = min(H, tt.y1 + 520)
    inset = 20 if tt.kind == "specimen" else 4       # keep the frame's side borders out
    x0, x1 = x0 + inset, x1 - inset
    body = img[y_start:y_end, x0:x1]
    al = {c: colour_alpha(body, TEXT_COLOURS[c]) for c in body_colours}
    combined = np.max(np.stack([al[c] for c in body_colours]), axis=0)
    centre = centre_abs - x0
    prof = (combined > 0.45).sum(1)
    # grey Specimen text also matches the desaturated scene behind the tooltip, so only
    # count rows that are clearly busier than that background
    min_count = 5 if tt.kind == "flora" else max(5, int(0.12 * prof[:200].max(initial=0)))
    runs = _runs(prof, min_count, min_len=7)
    last_end = None
    body_imgs = []
    for r0, r1 in runs:
        # lines are ~10-14px apart; after the first line there's a separator (~24px)
        max_gap = 32 if len(body_imgs) == 1 else 22
        if last_end is not None and r0 - last_end > max_gap:
            break                                    # past the end of the tooltip body
        if r1 - r0 > 26:                             # taller than a text line: reroll buttons etc.
            break
        sub_rows = slice(max(0, r0 - 3), r1 + 3)
        ext = _centred_extent((combined[sub_rows] > 0.45).any(0), centre)
        if ext is None:
            continue
        l, r = ext
        if r - l < 60:                               # reroll buttons: the body has ended
            break
        colour = max(body_colours, key=lambda c: float(al[c][sub_rows, l:r + 1].sum()))
        line_alpha = al[colour][sub_rows, max(0, l - 4):r + 5]
        body_imgs.append(_line_image(line_alpha, scale=sc))
        last_end = r1
        out.lines.append(TextLine(y_start + r0, y_start + r1, x0 + l, x0 + r, colour, ""))
    out.bottom = (y_start + last_end) if last_end is not None else tt.y1

    # ---- read every line of the tooltip in one go ----
    texts = engine.read_lines(header_imgs + body_imgs)
    head, body_txt = texts[:len(header_imgs)], texts[len(header_imgs):]
    if head:
        out.name = head[0]
        if len(head) > 1:
            out.base = head[1]
    for line, text in zip(out.lines, body_txt):
        line.text = text or "?"                      # keep the line so a failed read is visible
    return out


# ---------------------------------------------------------------------------
# Lifeforce labels (magenta background, white text)
# ---------------------------------------------------------------------------
@dataclass
class LifeforceLabel:
    x0: int
    y0: int
    x1: int
    y1: int
    text: str
    amount: int | None
    colour: str | None


LIFEFORCE_RE = re.compile(r"(\d[\d,.]*)\s*[xX×]*\s*(WILD|VIVID|PRIMAL|SACRED)", re.I)


LABEL_COLOUR = (255, 0, 255)      # the loot filter's lifeforce label background (a setting)


def find_lifeforce(img: np.ndarray, engine: OcrEngine, colour=None) -> list[LifeforceLabel]:
    R, G, B = colour or LABEL_COLOUR
    b, g, r = [img[..., i].astype(int) for i in range(3)]
    # the label background, give or take (for magenta: red and blue above 200, green below 70)
    magenta = (np.abs(r - R) <= 55) & (np.abs(b - B) <= 55) & (np.abs(g - G) <= 70)
    mask = magenta.astype(np.uint8) * 255
    # close only horizontally (fills the gaps between letters) so labels stacked
    # directly on top of each other stay separate
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((1, 15), np.uint8))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(mask)
    boxes, imgs = [], []
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if w < 90 or h < 26 or h > 80 or area < 0.6 * w * h:    # ~49px tall at filter font size 45
            continue
        crop = img[y:y + h, x:x + w].astype(int)
        # white text: bright in every channel, so far from the background colour in its weakest
        # channel (green for magenta); the dark border isn't text
        bgr = [B, G, R]
        weakest = int(np.argmin(bgr))
        txt_alpha = np.clip((crop[..., weakest] - bgr[weakest] - 40) / 200.0, 0, 1)
        strong = [i for i in range(3) if bgr[i] > 128] or [int(np.argmax(bgr))]
        txt_alpha[(crop[..., strong] < 150).any(axis=2)] = 0   # only inside the label
        boxes.append((x, y, w, h))
        imgs.append(_line_image(txt_alpha, scale=engine.scale * 0.75, pad=4))
    out = []
    for (x, y, w, h), text in zip(boxes, engine.read_lines(imgs)):
        m = LIFEFORCE_RE.search(text)
        amount = colour = None
        if m:
            digits = re.sub(r"[^\d]", "", m.group(1))
            amount = int(digits) if digits else None
            colour = m.group(2).capitalize()
        out.append(LifeforceLabel(int(x), int(y), int(x + w), int(y + h), text, amount, colour))
    return out


# ---------------------------------------------------------------------------
# Wisp counter (bottom right, above the skill bar; 2560x1440)
# Up to three rows at fixed heights, filled from the bottom: an icon, then the number.
# ---------------------------------------------------------------------------
WISP_SLOTS = [1244, 1189, 1133]          # row centres, bottom row first
# x measured from the right edge (the counter sits by the skill bar, which hangs off the right side)
WISP_ICON_FROM_RIGHT = (735, 685)        # = x 1825-1875 on a 2560-wide screen
WISP_DIGIT_FROM_RIGHT = (684, 565)       # = x 1876-1995
WISP_ICON_MIN = 200                      # real icons have ~300 matching pixels, other UI ~70
WISP_DIGITS_MIN = 120                    # real numbers have 200+ digit pixels, background ~60
NUMBER_RE = re.compile(r"^\d{1,3}(,\d{3})*$")


def _wisp_icon(img, y):
    W = img.shape[1]
    reg = img[y - 18:y + 18, W - WISP_ICON_FROM_RIGHT[0]:W - WISP_ICON_FROM_RIGHT[1]].astype(int)
    b, g, r = reg[..., 0], reg[..., 1], reg[..., 2]
    counts = {"yellow": int(((r > 180) & (g > 120) & (b < 130) & (r - b > 90)).sum()),
              "blue": int(((b > 170) & (g > 120) & (r < 150) & (b > r + 60)).sum()),
              "purple": int(((b > 140) & (r > 110) & (g < 120) & (b > g + 50) & (r > g + 30)).sum())}
    colour = max(counts, key=counts.get)
    return colour if counts[colour] >= WISP_ICON_MIN else None


def _digit_alpha(band):
    """The counter's digits are pale yellow-green: green and red clearly above blue. Grey stone,
    sparks and the panel ornament next to the top row don't have that balance."""
    b, g, r = [band[..., i].astype(np.float32) for i in range(3)]
    return (np.clip((g - b - 15) / 45.0, 0, 1) * np.clip((r - b - 10) / 40.0, 0, 1)
            * np.clip((g - 70) / 60.0, 0, 1))


def _keep_glyphs(a):
    """Drop sparks and specks: keep blobs that cover the digits' band (~17px tall), plus small
    blobs on the baseline (the comma). Kept digits keep their soft edges."""
    m = (a > 0.35).astype(np.uint8)
    n, lab, st, _ = cv2.connectedComponentsWithStats(m)
    tall = [i for i in range(1, n) if 13 <= st[i, cv2.CC_STAT_HEIGHT] <= 24 and st[i, cv2.CC_STAT_WIDTH] <= 90]
    if not tall:
        return a * 0
    top = int(np.median([st[i, 1] for i in tall]))
    base = int(np.median([st[i, 1] + st[i, 3] for i in tall]))
    band = max(1, base - top)
    keep = np.zeros(n, bool)
    for i in range(1, n):
        x, y, w, h, area = st[i]
        overlap = min(y + h, base) - max(y, top)
        if i in tall and overlap >= 0.8 * band:
            keep[i] = True
        elif 3 <= h <= 9 and w <= 7 and area >= 4 and abs(y + h - base) <= 5:   # comma
            keep[i] = True
    near = cv2.dilate(keep[lab].astype(np.uint8), np.ones((5, 5), np.uint8)).astype(bool)
    return a * near


def read_wisps(img: np.ndarray, engine: OcrEngine) -> dict:
    """{"yellow": 2378, "blue": 4571, ...}; a colour whose number couldn't be read is None."""
    H, W = img.shape[:2]
    if H != REFERENCE_HEIGHT or W < 1600:
        return {}
    dx0, dx1 = W - WISP_DIGIT_FROM_RIGHT[0], W - WISP_DIGIT_FROM_RIGHT[1]
    found, imgs = [], []
    for y in WISP_SLOTS:
        colour = _wisp_icon(img, y)
        if colour is None:
            break                                    # rows fill from the bottom
        a = _digit_alpha(img[y - 20:y + 20, dx0:dx1])
        if int((a > 0.35).sum()) < WISP_DIGITS_MIN:
            break
        a = _keep_glyphs(a)
        cols = np.where((a > 0.35).any(0))[0]
        if len(cols) == 0:
            break
        start = end = cols[0]
        for x in cols[1:]:                           # digits and comma, then stop at a big gap
            if x - end > 10:
                break
            end = x
        found.append(colour)
        imgs.append(_line_image(a[:, max(0, start - 3):end + 4], scale=engine.scale * 1.25))
    out = {}
    for colour, text in zip(found, engine.read_lines(imgs, digits_only=True)):
        text = text.replace(" ", "").replace(".", ",")
        out[colour] = int(text.replace(",", "")) if NUMBER_RE.match(text) else None
    return out


# ---------------------------------------------------------------------------
# One call for a whole screenshot
# ---------------------------------------------------------------------------
def read_screenshot(path_or_img, engine: OcrEngine, cursor=None, want=("tooltip", "lifeforce", "wisps"),
                    label_colour=None):
    """Everything in the result is in the 1440-tall frame: res["image"] is the scaled screenshot and
    res["scale"] its scale (divide a position by it to get back to screen pixels)."""
    img = cv2.imread(path_or_img) if isinstance(path_or_img, str) else path_or_img
    img, scale = normalize(img)
    if cursor is not None and scale != 1:
        cursor = (cursor[0] * scale, cursor[1] * scale)
    res = {"image": img, "scale": scale}
    if "tooltip" in want:
        tts = find_tooltips(img)
        tt = nearest_tooltip(tts, cursor)
        res["tooltip"] = read_tooltip(img, tt, engine) if tt else None
        res["tooltips_found"] = len(tts)
    if "lifeforce" in want:
        res["lifeforce"] = find_lifeforce(img, engine, label_colour)
    if "wisps" in want:
        res["wisps"] = read_wisps(img, engine)
    return res
