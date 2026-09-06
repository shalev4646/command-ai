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


def _startup_png(w: int, h: int, dpr: int) -> bytes:
    """A PLAIN olive field — no chevron, no wordmark (2026-09-06, user's call,
    option A). The logo lives in the boot shell only and fades in there.

    Why the logo left the launch image, after two months of pixel-matching:
    the device videos of 2026-09-06 (22:15, 23:06 — the latter a fresh
    install) showed iOS placing the launch image itself 2px lower in 5 of 7
    launches and exactly on the pixel in the other 2, while the shell's
    splash never moved. Two copies of one logo, one of which jitters by the
    OS, cannot be made to coincide; the seam was visible at every launch
    ("two parts"). With nothing but the backdrop here, the dissolve into the
    shell is olive→olive by construction, and the one-frame brightening iOS
    adds when it resizes the web view at dismissal (793→852pt, read off the
    shell's diagnostic line) lands on a plain surface, where 3% is nothing.
    The shell then fades the identity block in (see cai-veiled in
    boot_shell) once the web view has been handed the full glass.

    ⛔ Cache-locked: iOS never refreshes the INSTALLED app's launch PNG. An
    existing install keeps whatever image it was added with (the pilot's
    2026-09-06 23:00 install carries the last logo image); only a remove +
    re-add picks this one up. Every fresh install does."""
    import io
    from PIL import Image

    img = Image.new("RGB", (w, h), "#14170E")
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
