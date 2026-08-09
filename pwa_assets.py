"""PWA assets: the launch images, icons and web-app manifest.

Lives OUTSIDE app.py so it can run at Docker build time. That is not tidiness —
it is the fix for a real outage mode. These files used to be registered with
Streamlit's MediaFileManager, whose /media/<hash> URLs are per-process and
garbage-collected; iOS records the manifest URL when the icon is added to the
home screen and re-reads it on every launch, so the pilot's icon was pointing
at a 404 by the next deploy. No manifest means no background_color, which means
iOS paints the launch-image dissolve over a WHITE web view — the flash that
survived four rounds of paint-timing work (2026-07-29).

Publishing to /static/cai/ fixes the URL, but only if the files EXIST. Written
from a Streamlit session they appear on the first live visit, which is too late:
a freshly deployed container answers 404 to exactly the launch that needs them.
So the Docker build calls publish_all() and bakes them into the image.

Import-safe by design: no streamlit import, no app state, no network.
"""
import base64
import json
from functools import lru_cache
from pathlib import Path

from boot_shell import publish_static

_ROOT = Path(__file__).parent


def _icon_bytes(name: str) -> bytes | None:
    # branding/, not static/ — static/* is gitignored (runtime PDF mirror),
    # and an icon that never reaches the cloud breaks A2HS silently
    try:
        return (_ROOT / "branding" / "icons" / name).read_bytes()
    except Exception:
        return None


# Streamlit Community Cloud mounts the repo at /mount/src — absent locally.
_ON_CLOUD = Path("/mount/src").exists()

# iOS launch screens: (device px width, height, device-pixel-ratio) per
# iPhone class. iOS shows the matching image from the moment the icon is
# tapped until the page's first paint — on a weak connection that is most
# of the wait, and without these it is a black void (see 2026-07-13 video:
# ~15s of black before anything web-controlled can run).
_STARTUP_SIZES = [
    (640, 1136, 2), (750, 1334, 2), (828, 1792, 2),
    (1125, 2436, 3), (1170, 2532, 3), (1179, 2556, 3), (1206, 2622, 3),
    (1242, 2688, 3), (1284, 2778, 3), (1290, 2796, 3), (1320, 2868, 3),
]


# status-bar heights (pt) per launch-image class — the ONLY per-device input
# the PNG needs to land its chevron where the splash draws its own (the
# splash pads by env(safe-area-inset-top), which iOS reports as these).
# Keyed by the pixel triple because 828×1792@2 (XR, 48pt) and 1242×2688@3
# (XS Max, 44pt) share a pt-size with different bars.
_STARTUP_SAT = {
    (640, 1136, 2): 20, (750, 1334, 2): 20, (828, 1792, 2): 48,
    (1125, 2436, 3): 44, (1170, 2532, 3): 47, (1179, 2556, 3): 59,
    (1206, 2622, 3): 62, (1242, 2688, 3): 44, (1284, 2778, 3): 47,
    (1290, 2796, 3): 59, (1320, 2868, 3): 62,
}


# Subtitle metrics, READ OUT OF A BROWSER, not derived. Measured 2026-07-28
# (evening, layout D — the user's pick of four rendered candidates) in the
# patched index.html at 375x812 with sat=0. Layout D is two tiers:
#
#   line 1  "מערכת פקודות"  13px, ls 2, rgba(23,26,18,.59), flanked by
#           26x1px rules (rgba .31) at a 12px gap
#   line 2  "בלמ״ס"          9.5px, ls 7, rgba(23,26,18,.37)
#
# Browser truth (pad = sat + 14vh):
#   .s1 flex row box: top pad+124, h 17 (asc 13 + desc 4, half-leading ZERO)
#       -> text baseline pad+137;  rules flex-centred -> y [pad+132, pad+133]
#       text item box w 100.953 (advance 76.947 + 12 x 2 tracking), box
#       centred; rules symmetric about the centre (measured 99.031/275.984
#       about a 187.5 centre)
#   .s2 box: top pad+147 (s1 bottom 141 + 6 flex gap), h 12, asc 9
#       -> baseline pad+156;  box w 60.141 (advance 25.137 + 5 x 7 tracking)
#
# The subtitle is the one splash element the launch image used to omit, which
# is what made the hand-off read as a second screen. Painting it here closes
# that, but only if it lands on the shell's copy to the pixel — hence measured
# constants and the matching CSS in boot_shell._HEAD_TEMPLATE. Change one side
# and you must change the other.
_SUB1_TEXT = 'מערכת פקודות'
_SUB1_PX = 13
_SUB1_WIDTH = 100.953    # layout width INCLUDING the trailing letter-spacing
_SUB1_BASELINE = 137     # below sat + 14vh
_SUB1_ALPHA = 150        # round(255 * .59)
_RULE_W, _RULE_GAP = 26, 12          # the flanking rules, from .s1::before CSS
_RULE_TOP, _RULE_ALPHA = 132, 79     # y below pad; round(255 * .31)
_SUB2_TEXT = 'בלמ"ס'
_SUB2_PX = 9.5
_SUB2_WIDTH = 60.141
_SUB2_BASELINE = 156
_SUB2_ALPHA = 94         # round(255 * .37)


def _draw_subtitle(img, fp: str, cx: float, pad_px: float, dpr: int) -> None:
    """Lay layout D out the way CSS lays out RTL runs with letter-spacing.

    CSS adds the tracking AFTER every character including the last, so in RTL
    the leftover spacing lands on the run's visual LEFT — inside the layout box
    but outside the ink. The BOX is what gets centred, so each line's INK ends
    up letter-spacing/2 to the RIGHT of the viewport centre (verified per
    character in the browser). The RULES, by contrast, are flex siblings and
    sit symmetric about the true centre. Reproducing all of that means walking
    right to left from each layout box's right edge — do not "fix" it by
    centring the ink.

    Tracking is recomputed from THIS font's advances rather than hardcoded:
    Pillow hints advances to whole pixels while the browser lays out
    fractionally; solving for the spacing that reproduces the measured layout
    width pins both ends of each run (v9 verification: worst glyph 0.22 CSS px,
    ends within 0.02).

    Composited through an L mask because ImageDraw.text() SILENTLY IGNORES the
    alpha channel of an RGBA fill — unlike polygon(), which is why the
    chevron's faded second stroke works while a first attempt at the subtitle
    came out at full opacity. Coverage x alpha through a mask is precisely what
    CSS does with color: rgba().
    """
    from PIL import Image, ImageDraw, ImageFont

    mask = Image.new("L", img.size, 0)
    mdraw = ImageDraw.Draw(mask)

    def run(text, px, width, baseline, alpha):
        font = ImageFont.truetype(fp, int(px * dpr + 0.5))
        adv = [font.getlength(ch) for ch in text]
        ls = (width * dpr - sum(adv)) / len(text)
        x = cx + width * dpr / 2           # right edge of the layout box
        for ch, a in zip(text, adv):
            x -= a
            mdraw.text((x, pad_px + baseline * dpr), ch,
                       font=font, fill=alpha, anchor="ls")
            x -= ls

    run(_SUB1_TEXT, _SUB1_PX, _SUB1_WIDTH, _SUB1_BASELINE, _SUB1_ALPHA)
    run(_SUB2_TEXT, _SUB2_PX, _SUB2_WIDTH, _SUB2_BASELINE, _SUB2_ALPHA)
    # the flanking rules: symmetric about cx, 1 CSS px tall, flex-centred on
    # line 1's optical middle
    half = _SUB1_WIDTH * dpr / 2
    y0, y1 = pad_px + _RULE_TOP * dpr, pad_px + (_RULE_TOP + 1) * dpr - 1
    for sgn in (1, -1):
        inner = cx + sgn * (half + _RULE_GAP * dpr)
        outer = inner + sgn * _RULE_W * dpr
        mdraw.rectangle([min(inner, outer), y0, max(inner, outer), y1],
                        fill=_RULE_ALPHA)
    img.paste((236, 237, 230), (0, 0), mask)


@lru_cache(maxsize=None)
def _startup_png(w: int, h: int, dpr: int) -> bytes:
    """Olive launch screen with the double-chevron mark at the SPLASH's
    chevron position, so the OS launch image morphs into the splash without
    a jump (the user filmed the old centered chevron leaping to the splash's
    top-aligned one). The splash chevron's first apex sits at
    env(safe-area-inset-top) + 14vh − 6.6px: the .cai-splash padding is
    sat+14vh, and a 26px box + 6px border rotated 45° overhangs its layout
    top by (32·√2−32)/2 ≈ 6.6px. Verified against the launch video: apex at
    ~176pt on a 393×852 device = 59 + 119.3 − 6.6."""
    import io
    from PIL import Image, ImageDraw

    # Dark chain (2026-08-03): the launch image shares the APP's backdrop —
    # the sage field read as a bright second screen that then "flashed" into
    # the dark app. Ink flips accordingly: olive chevron, cream wordmark.
    img = Image.new("RGB", (w, h), "#14170E")
    draw = ImageDraw.Draw(img, "RGBA")
    dd = 32 * 0.7071 * dpr          # apex-to-arm-tip reach of the 32px box
    tv = 6 * 1.4142 * dpr           # vertical band thickness of a 6px stroke
    # The arm tips are MITERED, and blunt tips are what betrayed the hand-off.
    # Once the 2026-08-09 hand-off finally went flash-free, the PNG->HTML swap
    # was STILL visible on the chevrons alone (the user pointed straight at
    # them). Pixel rows off the 21:34 video, all four launches: bright ink
    # ended at apex+29 before the swap vs apex+21 after, dim tail at ~80 luma
    # down to +52 before vs ~46 down to +39 after — the arms visibly retract.
    # The reason is how each renderer ends the stroke. This polygon used to
    # close the tips with a full-thickness vertical cut; WebKit draws the
    # splash's chevron as border-top+border-left of a rotated square, and a
    # border strip meeting a zero-width neighbour ends in a MITER: the outer
    # corner is cut diagonally back to the inner edge. Rotated 45°, that cut
    # runs from the tip (dd, dd) inward+down by 6/√2 on each axis — the tip
    # tapers, it does not end square. Same spec geometry in Blink/WebKit, so
    # matching the miter makes the two chevrons agree to antialiasing noise,
    # and the swap has nothing left to show.
    tc = 6 * 0.7071 * dpr           # the miter cut's inward/downward reach
    cx = w / 2
    sat = _STARTUP_SAT.get((w, h, dpr), 47)
    apex = (sat + 0.14 * (h / dpr) - 6.6) * dpr
    for i, color in enumerate([(163, 174, 110, 255), (163, 174, 110, 115)]):
        ay = apex + i * 23 * dpr
        draw.polygon(
            [(cx - dd, ay + dd), (cx, ay), (cx + dd, ay + dd),
             (cx + dd - tc, ay + dd + tc), (cx, ay + tv),
             (cx - dd + tc, ay + dd + tc)],
            fill=color,
        )
    # ── wordmark ──────────────────────────────────────────────────────────
    # The launch image used to be chevron-only, on the theory that the splash
    # would supply the wordmark a moment later. On a phone that moment is
    # SIX AND A HALF SECONDS (2026-07-27 video: launch image t=3.5-10.0, splash
    # at t=11.0) — the user stares at a near-empty olive field for most of the
    # boot. Painting the wordmark here makes the very first frame the finished
    # splash instead of a fragment of it.
    #
    # Geometry is exact, not eyeballed. Measured in the browser against the
    # live .cai-splash (364x904, sat=0): the chevron box is 43px tall from
    # padding-top = sat + 14vh, the flex gap is 18px, so the title's line box
    # starts at sat + 14vh + 61. Pillow reports ascent 34 / descent 11 for
    # Suez One at 34px — a 45px sum that equals the browser's line box to the
    # pixel, so half-leading is zero and the baseline sits exactly one ascent
    # below the box top. Advance width agrees too (PIL 205px vs browser 205.1),
    # which is what lets a centred draw land on the CSS-centred text.
    try:
        from PIL import ImageFont
        fp = _ROOT / "branding" / "fonts" / "SuezOne-Regular.ttf"
        font = ImageFont.truetype(str(fp), int(round(34 * dpr)))
        baseline = (sat + 0.14 * (h / dpr) + 61 + 34) * dpr
        # two-tone like the splash/entry ("Command" cream, "AI" olive): both
        # ends pinned to the full-string advance so the total width matches
        # the single-run CSS text; seam kerning error lands invisibly between
        total = font.getlength("CommandAI")
        x0 = cx - total / 2
        draw.text((x0, baseline), "Command", font=font,
                  fill=(236, 237, 230, 255), anchor="ls")
        draw.text((x0 + total - font.getlength("AI"), baseline), "AI",
                  font=font, fill=(163, 174, 110, 255), anchor="ls")
        _draw_subtitle(img, str(fp), cx,
                       (sat + 0.14 * (h / dpr)) * dpr, dpr)
    except Exception:
        # a missing/unreadable font must never break the launch image — the
        # chevron alone is exactly the old behaviour
        pass
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()



def build_manifest(start_url: str, icons: dict) -> bytes:
    """The manifest bytes. start_url is passed in because it depends on request
    state (the caidbg flag) that this module deliberately knows nothing about."""
    return json.dumps({
        "name": "CommandAI — עוזר הפקודות של צה\"ל",
        "short_name": "CommandAI",
        "lang": "he",
        "dir": "rtl",
        "start_url": start_url,
        "scope": "/",
        "display": "standalone",
        # Portrait-locked on purpose. Nothing in the app reads
        # env(safe-area-inset-left/right) — every inset in the CSS is top or
        # bottom — so in landscape the Dynamic Island eats the leading edge of
        # the header and the drawer. The launch images are portrait-only too
        # (_STARTUP_SIZES), so a landscape cold start has no art to show.
        # Chat in one hand is a portrait posture anyway; if landscape is ever
        # wanted, the lateral insets have to land FIRST.
        "orientation": "portrait",
        "background_color": "#14170E",   # the dark chain: webview pre-paint matches the app
        "theme_color": "#14170E",
        "icons": [
            {"src": icons[192], "sizes": "192x192",
             "type": "image/png", "purpose": "any maskable"},
            {"src": icons[512], "sizes": "512x512",
             "type": "image/png", "purpose": "any maskable"},
        ],
    }, ensure_ascii=False).encode("utf-8")


def publish_all(start_url: str = "/") -> dict | None:
    """Write every PWA asset to its stable /static/cai/ URL and return the map.

    Idempotent and cheap on repeat calls (publish_static only writes on change),
    so the Streamlit session can call it every rerun while the Docker build
    calls it once to bake the files into the image.
    """
    try:
        urls = {}
        for size in (180, 192, 512):
            data = _icon_bytes(f"icon-{size}.png")
            if not data:
                return None
            urls[size] = publish_static(f"icon-{size}.png", data)
            if not urls[size]:
                return None
        urls["manifest"] = publish_static(
            "manifest.json", build_manifest(start_url, urls))
        if not urls["manifest"]:
            return None
        urls["startup"] = [
            (w, h, r, publish_static(f"launch-{w}x{h}@{r}.png",
                                     _startup_png(w, h, r)))
            for (w, h, r) in _STARTUP_SIZES
        ]
        if any(not u for (_, _, _, u) in urls["startup"]):
            return None
        return urls
    except Exception:
        return None


if __name__ == "__main__":
    import sys
    ok = publish_all() is not None
    print("pwa assets published:", ok)
    sys.exit(0 if ok else 1)
