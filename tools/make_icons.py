"""Draw the app's icons (a sprouting leaf) into src/poe_harvest/assets/. Run once; the .ico files are committed."""
import math
import os

from PIL import Image, ImageDraw

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src", "poe_harvest", "assets")
STATES = {"ready": ((46, 160, 67), (120, 220, 110)), "paused": ((110, 110, 110), (180, 180, 180)),
          "problem": ((214, 120, 32), (250, 190, 90))}


def leaf(size, dark, light):
    S = size * 4                                         # draw big, shrink for smooth edges
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.ellipse([S * .03, S * .03, S * .97, S * .97], fill=(24, 26, 24, 255))
    # leaf: two arcs meeting at the tips, tilted
    pts = []
    for t in range(0, 181):
        a = math.radians(t)
        x = math.cos(a) * .36
        y = math.sin(a) * .17
        pts.append((x, y))
    pts += [(x, -y) for x, y in reversed(pts)]
    ang = math.radians(-40)
    poly = [(S * (.52 + x * math.cos(ang) - y * math.sin(ang)), S * (.46 + x * math.sin(ang) + y * math.cos(ang)))
            for x, y in pts]
    d.polygon(poly, fill=light)
    # midrib + stem
    x0, y0 = S * (.52 - .36 * math.cos(ang)), S * (.46 - .36 * math.sin(ang))
    x1, y1 = S * (.52 + .36 * math.cos(ang)), S * (.46 + .36 * math.sin(ang))
    d.line([(x0, y0), (x1, y1)], fill=dark, width=max(2, S // 22))
    d.line([(x0, y0), (S * .30, S * .80)], fill=dark, width=max(2, S // 16))
    return im.resize((size, size), Image.LANCZOS)


def main():
    os.makedirs(OUT, exist_ok=True)
    for state, (dark, light) in STATES.items():
        big = leaf(256, dark, light)
        big.save(os.path.join(OUT, f"tray_{state}.ico"), sizes=[(16, 16), (20, 20), (24, 24), (32, 32), (48, 48)])
    leaf(256, *STATES["ready"]).save(os.path.join(OUT, "app.ico"),
                                     sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    leaf(256, *STATES["ready"]).save(os.path.join(OUT, "app.png"))
    print("icons written to", OUT)


if __name__ == "__main__":
    main()
