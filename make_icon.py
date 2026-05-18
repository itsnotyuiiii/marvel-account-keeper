"""Generate the application icon (icon.ico / icon.png).

Draws the app's duck mascot on a dark tile and writes a multi-resolution
.ico for Windows plus a 256px .png for macOS/Linux packaging. Re-run this
only if the artwork needs to change — the committed icon.ico is what the
build script consumes.

    python make_icon.py
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).parent
S = 1024  # supersampled working canvas; downsampled for crisp edges


def rounded(draw, box, radius, **kw):
    draw.rounded_rectangle(box, radius=radius, **kw)


def render() -> Image.Image:
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # ── dark tile ────────────────────────────────────────────────────────────
    rounded(d, (0, 0, S - 1, S - 1), radius=int(S * 0.22),
            fill=(21, 23, 29, 255))
    rounded(d, (8, 8, S - 9, S - 9), radius=int(S * 0.21),
            outline=(44, 47, 56, 255), width=6)
    # Marvel-red accent: a thin arc-like bar along the bottom inside edge.
    rounded(d, (int(S * 0.30), int(S * 0.90), int(S * 0.70), int(S * 0.90) + 26),
            radius=13, fill=(237, 29, 36, 255))

    # ── duck ────────────────────────────────────────────────────────────────
    # Geometry: head (radius r) + a bill protruding to cx + 1.5r. The whole
    # span (cx-r .. cx+1.5r) is kept inside the tile with even padding.
    cx, cy, r = int(S * 0.42), int(S * 0.45), int(S * 0.27)

    # bill (drawn first so the head overlaps its base)
    bill_box = (cx + int(r * 0.50), cy - int(r * 0.32),
                cx + int(r * 1.50), cy + int(r * 0.40))
    rounded(d, bill_box, radius=int(r * 0.34),
            fill=(255, 155, 47, 255),
            outline=(26, 21, 0, 255), width=7)
    # bill seam
    d.line((cx + int(r * 0.62), cy + int(r * 0.04),
            cx + int(r * 1.42), cy + int(r * 0.04)),
           fill=(26, 21, 0, 110), width=6)

    # head
    d.ellipse((cx - r, cy - r, cx + r, cy + r),
              fill=(63, 191, 74, 255),
              outline=(14, 44, 18, 255), width=11)

    # iridescent sheen — a single light arc across the top of the head
    d.arc((cx - int(r * 0.74), cy - int(r * 0.80),
           cx + int(r * 0.58), cy + int(r * 0.28)),
          start=202, end=338, fill=(111, 229, 124, 255), width=15)

    # eye
    ex, ey, er = cx + int(r * 0.15), cy - int(r * 0.23), int(r * 0.18)
    d.ellipse((ex - er, ey - er, ex + er, ey + er), fill=(14, 44, 18, 255))
    hl = int(er * 0.42)
    d.ellipse((ex - hl + 7, ey - hl - 5, ex + hl + 7, ey + hl - 5),
              fill=(255, 255, 255, 255))

    return img


def main() -> None:
    art = render()
    png = art.resize((256, 256), Image.LANCZOS)
    png.save(ROOT / "icon.png")
    sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    art.resize((256, 256), Image.LANCZOS).save(ROOT / "icon.ico", sizes=sizes)
    print(f"wrote {ROOT / 'icon.ico'}  and  {ROOT / 'icon.png'}")


if __name__ == "__main__":
    main()
