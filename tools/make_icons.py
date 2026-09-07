#!/usr/bin/env python3
"""Compute the icons out of the logo image.

    python3 tools/make_icons.py Logo_Favicon_blue.png

⚠ The source is `Logo_Favicon_blue.png`, NOT `Logo_Favicon.png`. The circle was
  drawn in amber; the site's safelight was later changed to a blue
  (`#6aa9e0`, at the very bottom of site.css). `Logo_Favicon.png` is the
  original as it came back; the blue version is what the site runs.

Out of one square image (a contact sheet on the site's ground) come:

    static/icons/favicon-16.png      the marked frame only
    static/icons/favicon-32.png      the whole mark
    static/icons/favicon-48.png      the whole mark
    static/icons/favicon.ico         16 + 32 + 48 in one file
    static/icons/apple-touch-icon.png    180, with air around it
    static/icons/icon-192.png        PWA
    static/icons/icon-512.png        PWA
    static/icons/icon-maskable-512.png   PWA, with the 20 % safe zone
    static/icons/mark.png            128, ground cut out (for the masthead)

⚠ At 16 px the whole contact sheet turns to mush -- four frames, ten
  perforations and a circle do not fit into 16 pixels. So the smallest size
  gets a DIFFERENT motif: only the marked frame with the circle. An .ico may
  carry a different image per size, and that is what is used here.
"""
import struct
import sys
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageChops

GROUND = (26, 24, 21)          # #1a1815 -- the site's ground
# ⚠ Both boxes belong to the IMAGE, not to the program. With a new logo they
#   have to be measured again (the drawing's bounding box, and the circle).
DRAWING = (195, 216, 1073, 1012)  # the drawing in the 1254 px original
CIRCLE = (619, 572, 1079, 1032)   # the marked frame with the circle


def load(src: Path) -> Image.Image:
    """Open the image and bring the ground to EXACTLY `GROUND`.

    The generator lays a slight vignette over the ground. At 16 px that turns
    into a grey haze around the drawing -- so everything that is nearly ground
    is set to the pure value."""
    im = Image.open(src).convert("RGB")
    bg = im.getpixel((4, 4))
    d = ImageChops.difference(im, Image.new("RGB", im.size, bg)).convert("L")
    return Image.composite(im, Image.new("RGB", im.size, GROUND),
                           d.point(lambda p: 255 if p > 14 else 0))


def square(im: Image.Image, ratio: float) -> Image.Image:
    """A square around the drawing that fills `ratio` of the width."""
    x0, y0, x1, y1 = DRAWING
    cx, cy, side = (x0 + x1) / 2, (y0 + y1) / 2, (x1 - x0) / ratio
    out = Image.new("RGB", (round(side), round(side)), GROUND)
    out.paste(im.crop((round(cx - side/2), round(cy - side/2),
                       round(cx + side/2), round(cy + side/2))), (0, 0))
    return out


def transparent(im: Image.Image) -> Image.Image:
    """Cut the ground away, so that only the strokes are left.

    The edges are antialiased, that is, mixed from ink and ground. The alpha
    comes from the distance to the ground, and the colour is computed back
    (`unpremultiply`) -- otherwise the mark would have a brownish halo on any
    other background."""
    out = Image.new("RGBA", im.size)
    sp, dp = im.load(), out.load()
    for y in range(im.size[1]):
        for x in range(im.size[0]):
            px = sp[x, y]
            a = max(abs(c - g) for c, g in zip(px, GROUND)) / 210
            if a < 0.06:
                dp[x, y] = (0, 0, 0, 0)
                continue
            a = min(1.0, a)
            dp[x, y] = (*(min(255, max(0, round((c - g*(1-a)) / a)))
                          for c, g in zip(px, GROUND)), round(a * 255))
    return out


def ico(path: Path, pairs) -> None:
    """An .ico with PNG parts -- its own drawing per size."""
    blobs = []
    for n, img in pairs:
        b = BytesIO()
        img.resize((n, n), Image.LANCZOS).save(b, "PNG", optimize=True)
        blobs.append(b.getvalue())
    off = 6 + 16 * len(blobs)
    head = struct.pack("<HHH", 0, 1, len(blobs))
    for (n, _), blob in zip(pairs, blobs):
        head += struct.pack("<BBBBHHII", n, n, 0, 0, 1, 32, len(blob), off)
        off += len(blob)
    path.write_bytes(head + b"".join(blobs))


def main(src: Path, out: Path) -> None:
    im = load(src)
    tight, safe = square(im, 0.90), square(im, 0.72)
    small = im.crop(CIRCLE)
    out.mkdir(parents=True, exist_ok=True)

    # ⚠ `maskable` gets the SMALLER mark (0.62): Android cuts a round piece out
    #   of the icon, and anything inside the outer 20 % can be gone. With
    #   `safe` (0.72) the contact sheet would have touched the edge.
    maskable = square(im, 0.62)
    for n, img, name in ((16, small, "favicon-16.png"),
                         (32, tight, "favicon-32.png"),
                         (48, tight, "favicon-48.png"),
                         (180, safe, "apple-touch-icon.png"),
                         (192, safe, "icon-192.png"),
                         (512, safe, "icon-512.png"),
                         (512, maskable, "icon-maskable-512.png")):
        img.resize((n, n), Image.LANCZOS).save(out / name, optimize=True)
    ico(out / "favicon.ico", [(16, small), (32, tight), (48, tight)])
    transparent(tight).resize((128, 128), Image.LANCZOS).save(
        out / "mark.png", optimize=True)

    # The app icon. ⚠ It does not live under `static/` but in the iOS project
    #   -- and it is computed HERE, so that a new logo does not have to be
    #   followed in two places. iOS rounds it itself, hence the air (0.70).
    ios = Path("ios/Roamlight/Assets.xcassets/AppIcon.appiconset/icon-1024.png")
    if ios.parent.is_dir():
        square(im, 0.70).resize((1024, 1024), Image.LANCZOS).save(ios, optimize=True)
        print(f"{ios}  {ios.stat().st_size:>6} B")

    for p in sorted(out.iterdir()):
        print(f"{p}  {p.stat().st_size:>6} B")


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "Logo_Favicon_blue.png"),
         Path(sys.argv[2] if len(sys.argv) > 2 else "static/icons"))
