# -*- coding: utf-8 -*-
"""Build branding/icons/* — the home-screen icons, from source, reproducibly.

Run:  python _build_icons.py

Why a script and not a hand-drawn PNG: the icon is the FIRST frame of the dark
chain. The launch images (pwa_assets._startup_png) and the splash are generated
from geometry, and the icon has to agree with them; a binary someone exported
once from a design tool drifts the moment either side is touched. Everything
below is the same double chevron the splash draws, so the two can be re-derived
together.

THE MARK is the splash's chevron pair, with two deliberate deviations, both
measured at 60pt (the size a phone actually shows) rather than at 512:

  * STROKE. The splash chevron is a 32px box with a 6px border rotated 45deg,
    i.e. band thickness / half-width = (6*sqrt2)/(32*0.7071) = 0.375. At icon
    size that reads thin and the two chevrons start to merge into grey, so the
    icon uses 0.42. On the launch image the chevron is ~43px tall on a full
    screen; here it is ~40px tall in a 60px tile, competing with saturated
    neighbours. Different job, different weight.

  * INK. The splash draws olive over olive-at-alpha-115, which works on a full
    dark screen but dies in a 60px tile — the faded arm drops below the point
    where the eye separates it from the field. The icon leads with CREAM and
    follows with solid OLIVE. That is not a new colour idea: it is the
    wordmark's own two-tone ("Command" cream + "AI" olive) applied to the mark,
    and it is what lets the icon survive on a DARK wallpaper, where the tile
    itself goes invisible and only the ink is left to carry it.

THE FIELD is lifted (#222819 -> #10130B) rather than flat #14170E. Flat matches
the app backdrop exactly, but on a dark wallpaper the tile then has no visible
edge and the icon reads as two chevrons floating on the user's own background.
The lift restores an edge without adding brightness, so the dark chain — icon,
launch image, splash, app — still hands off without a flash.

SIZE is bounded by the maskable safe zone, not by taste. manifest declares the
icons "any maskable" (pwa_assets.build_manifest), so Android may crop to a
circle of diameter 0.8*size; anything past radius 0.4*size can be cut. With the
proportions below the outermost ink — the lower chevron's arm tips — lands at
radius 0.386, the same margin the previous C_ icon had. Raising R past ~0.332
pushes the tips outside the circle. verify() asserts this every build.
"""
from pathlib import Path

from PIL import Image, ImageDraw

_ROOT = Path(__file__).parent
_OUT = _ROOT / "branding" / "icons"

# ── palette ────────────────────────────────────────────────────────────────
CREAM = (236, 237, 230)   # #ECEDE6 — the wordmark's "Command"
OLIVE = (163, 174, 110)   # #A3AE6E — the brand, and the wordmark's "AI"
BG_TOP = (34, 40, 25)     # the lifted field, top
BG_BOT = (16, 19, 11)     # ... and bottom; brackets the app's #14170E

# ── geometry, as fractions of the icon edge ────────────────────────────────
R_F = 0.325       # chevron half-width (apex to arm tip). Capped by the
                  # maskable safe zone — see the module docstring.
GAP_F = 0.72      # apex-to-apex spacing, in units of R
T_F = 0.42        # band thickness, in units of R (splash uses 0.375)
OPTICAL = 0.965   # the pair sits a hair above true centre, so it reads centred

SS = 4            # supersample. draw.polygon paints full-brightness pixels to
                  # the vector edge; reducing 4x with BOX gives exact
                  # area-average coverage, which is what an AA rasteriser
                  # computes. Same reasoning as pwa_assets._startup_png.

SIZES = (180, 192, 512, 1024)   # 180/192/512 are published by publish_all();
                                # 1024 is the App Store Connect upload.


def _chevron(d, cx, apex_y, R, color):
    """One chevron, mitered exactly like the splash's rotated-square border.

    WebKit draws the splash chevron as border-top + border-left of a square
    rotated 45deg. Where a border strip meets a zero-width neighbour it ends in
    a MITER: the outer corner is cut diagonally back to the inner edge. Square
    tips are the tell that a mark was redrawn rather than derived, so the cut
    is reproduced here.
    """
    T = T_F * R
    tc = T / 2                      # the miter's inward/downward reach
    d.polygon([
        (cx - R, apex_y + R),
        (cx, apex_y),
        (cx + R, apex_y + R),
        (cx + R - tc, apex_y + R + tc),
        (cx, apex_y + T),
        (cx - R + tc, apex_y + R + tc),
    ], fill=color)


def _field(size):
    """Vertical lift. Built one pixel wide and stretched — no per-pixel loop."""
    col = Image.new("RGB", (1, size))
    px = col.load()
    for y in range(size):
        t = y / max(1, size - 1)
        px[0, y] = tuple(round(a + (b - a) * t) for a, b in zip(BG_TOP, BG_BOT))
    return col.resize((size, size), Image.BILINEAR)


def icon(size: int) -> Image.Image:
    """The icon at `size`, RGB and full-bleed.

    RGB, not RGBA: iOS rejects an app icon with an alpha channel, and a
    transparent home-screen icon composites onto the wallpaper instead of the
    field. Full-bleed square, no baked corners: iOS and Android each apply
    their own mask, and a pre-rounded icon shows the seam inside theirs.
    """
    n = size * SS
    layer = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)

    R = R_F * n
    gap = GAP_F * R
    height = gap + R + T_F * R          # bbox of the pair, apex to arm tip
    y0 = (n - height) / 2 * OPTICAL

    _chevron(d, n / 2, y0, R, CREAM + (255,))
    _chevron(d, n / 2, y0 + gap, R, OLIVE + (255,))

    out = _field(size)
    small = layer.resize((size, size), Image.BOX)
    out.paste(small, (0, 0), small)
    return out


def verify(size: int = 512) -> float:
    """Assert the ink stays inside the maskable safe zone. Returns its radius.

    A maskable icon may be cropped to a circle of diameter 0.8*size, so ink
    past radius 0.4*size is not guaranteed to survive. This measures the
    rendered PNG rather than the vectors, so a future change to R_F, GAP_F,
    T_F or OPTICAL cannot quietly push the arm tips out of frame.
    """
    im = icon(size)
    px = im.load()
    c = (size - 1) / 2
    worst = 0.0
    for y in range(size):
        for x in range(size):
            r, g, b = px[x, y]
            # ink = anything materially brighter than the field's top value
            if r + g + b > (BG_TOP[0] + BG_TOP[1] + BG_TOP[2]) + 60:
                dx, dy = x - c, y - c
                worst = max(worst, (dx * dx + dy * dy) ** 0.5)
    frac = worst / size
    assert frac <= 0.400, (
        f"ink reaches radius {frac:.4f} of the icon — past the 0.400 maskable "
        f"safe zone. Lower R_F (or GAP_F/T_F) until this passes."
    )
    return frac


def main():
    _OUT.mkdir(parents=True, exist_ok=True)
    frac = verify()
    for size in SIZES:
        path = _OUT / f"icon-{size}.png"
        icon(size).save(path, "PNG", optimize=True)
        print(f"  wrote {path.relative_to(_ROOT)}  ({path.stat().st_size:,} bytes)")
    print(f"  maskable safe zone: ink at radius {frac:.4f} of icon (limit 0.400) OK")


if __name__ == "__main__":
    main()
