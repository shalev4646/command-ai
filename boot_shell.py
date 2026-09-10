"""Brand Streamlit's static index.html with an instant olive boot splash.

Single source of truth for the boot-shell patch, imported by two callers:

  * app.py — at runtime (first session self-heals the file if it is somehow
    unpatched, e.g. after a dependency reinstall).
  * the Docker build — `python -c "import boot_shell; boot_shell.patch_index_html()"`
    bakes the branded HTML into the image so the FIRST request already serves
    the olive splash, never the stock Streamlit skeleton.

The static shell is the first thing the browser paints — before the websocket,
the theme config, the gray skeleton or any delta. Out of the box that whole
phase is Streamlit's white page + spinner + skeleton (the "junk" users see on
slow loads). Patching the served file makes t=0 already look like the boot
splash, which the app's own `.cai-splash` then takes over seamlessly (same
olive, same wordmark) — one clean branded screen end to end.

NOTE ON HOSTING: on Streamlit Community Cloud this patch is a no-op in practice
— the platform serves its own index.html snapshot, so the file patched here is
never the one delivered. It only bites on a host where we own the served file
(local dev, or a self-hosted container), which is exactly why the container
build runs it. See the git history for the long Community-Cloud boot saga.
"""
import base64
import hashlib
import inspect
import os
import re
from pathlib import Path

import streamlit as st

# Human-readable half of the stamp. The machine half is a hash of the injected
# markup itself (computed in patch_index_html), so ANY edit below re-patches
# automatically and this only has to move when you want the version legible in
# a bug report.
#
# The hash is not belt-and-braces. Bumping by hand was the rule until
# 2026-07-28, when editing the subtitle's colour without touching this constant
# left the dev venv serving the previous markup — patch_index_html returned True
# and changed nothing, and the browser measurement that followed was quietly
# taken against stale CSS. A patch carrying any other stamp is STRIPPED and
# re-injected rather than nursed along with targeted swaps: a long-lived dev venv
# keeps its patched index.html forever, and silently testing last week's boot
# shell is worse than the cost of a rewrite.
_VERSION = "v46"


# viewport-fit=cover is NOT here, and that is the whole lesson of v12.
#
# Shipping it made the splash and the launch image agree perfectly — measured
# on device, chevron ink row 171 -> 172 across the hand-off, no jump at all.
# It also RESIZED THE APP. With cover the web view owns the full screen, and
# the app's layout — which had been tuned against the inset viewport for
# months — ended ~48px short of the bottom: content shifted up, a dead black
# band under the disclaimer, and it stayed that way for the whole session
# (2026-07-29 14:33 video, side by side against the 13:53 one). The pilot's
# words were "the whole screen went up and stays stuck", and he was right.
#
# So the viewport goes back to what the app expects, and the splash is aligned
# the other way round — by the cai-pad script below, which changes only the
# splash (retired 2026-09-01 with cover's return, back 2026-09-04 for the
# first-frame env()=0 ghost — see _PAD_JS_TEMPLATE).
# maximum-scale=1 stays: it caps zoom, never geometry, and shipping it here
# spares one more runtime write to this meta during boot.
#
# viewport-fit=cover is BACK (2026-09-01, user decision: the open drawer must
# run to the physical top of the glass, edge line included — a strip the web
# view does not own can only ever be ONE flat color, verified live when iOS
# flattened a canvas gradient). Static in the meta, never applied at runtime:
# the 2026-07-29 splash jump (171→164→171) was the RUNTIME application
# resizing the web view mid-boot, not cover itself. The July static-cover
# regression (content ~48px short of the bottom, dead band under the
# disclaimer) predates the measured-viewport engine — the empirical
# stBottom corrective and the glass clamp now pin the column to the real
# glass in either viewport mode; device checkpoint 1 verifies exactly that.
_STATIC_VIEWPORT_TOKENS = (", maximum-scale=1", ", viewport-fit=cover")

# THE SPLASH TAKES ITS ANCHOR FROM THE LAUNCH PNG'S OWN TABLE, NOT FROM env().
#
# History: the non-cover alignment shim lived here until 2026-09-01, was
# retired when viewport-fit=cover returned (under cover the CSS fallback
# `env(safe-area-inset-top) + 14vh` IS the launch image's formula), and is
# back on 2026-09-04 for a different reason, measured frame-by-frame on the
# 21:58 device video (60fps): WebKit's FIRST TWO FRAMES lay the splash out with
# env(safe-area-inset-top) = 0 — the UI process has not pushed the insets to
# the web process yet — so while iOS dissolves the launch image into the web
# view, the dissolving-in splash sits ~56pt ABOVE the PNG (device: sat 59pt),
# a dim second logo over the real one for 33ms; on the third frame the insets
# land, the splash drops onto the PNG and the rest of the dissolve is
# invisible because the two layers are now pixel-identical. That two-frame
# ghost is the user's "two screens".
#
# env() cannot be made early. The PNG's own math can: pwa_assets._startup_png
# places the block at `sat + 0.14 * (h / dpr)` with sat from _STARTUP_SAT,
# keyed by the device's physical screen — and screen.width/height/dpr are
# known at parse time. So this first-KB script computes EXACTLY the PNG's
# padding from EXACTLY the PNG's table and writes it as --cai-pad before the
# splash markup is parsed: the very first painted frame already sits on the
# PNG, whatever env() says. Unknown screens (Android, desktop, a future
# iPhone) set nothing and fall back to the env() formula — today's behaviour.
# The table is rendered from _STARTUP_SAT at patch time, never copied, so the
# two can never drift (locked by tests/test_boot_pad.py).
#
# iOS reports screen.width/height in portrait regardless of orientation;
# the min/max swap below is belt-and-braces for hosts that do not.
#
# ⛔ THE ANCHOR IS CACHE-LOCKED — the 2026-09-03 lesson, learned on device.
# A centered variant (50vh − 80px, all three layers moved together) shipped on
# 2026-09-02 and was REVERTED within the hour: iOS caches the launch PNG on
# the installed home-screen app and there is NO remote way to refresh it, so
# every existing install boots OLD PNG (top-anchored) → NEW splash (centered)
# — a visible two-screen jump with mismatched chevrons, filmed by the user at
# 00:15 ("שני מסכים... חיצים לא תואמים"). Moving this anchor is only safe
# together with a step that re-mints the installed PNG (re-add to home
# screen, or a native-wrapper migration) — as an explicit, user-approved
# migration, never a plain deploy. This script does NOT move the anchor: it
# reproduces sat + 14vh, the formula the installed PNGs were minted with.
_PAD_JS_TEMPLATE = (
    '<script id="cai-pad">(function(){try{var T=__TABLE__;'
    'var d=window.devicePixelRatio||1,w=screen.width,h=screen.height;'
    'if(w>h){var t=w;w=h;h=t}'
    'var k=Math.round(w*d)+"x"+Math.round(h*d)+"x"+Math.round(d);'
    'if(!(k in T))return;'
    'var f=function(){var st=document.documentElement.style;'
    'st.setProperty("--cai-pad",Math.round(T[k]+0.14*h)+"px");'
    'st.setProperty("--cai-idx",(Math.floor(w/2)-__IDCX__)+"px");'
    'st.setProperty("--cai-glass",h+"px");st.setProperty("--cai-vh14",(0.14*h).toFixed(2)+"px")};'
    'f();window.__caiPad=f;'
    '}catch(e){}})()</script>'
)


def _pad_js() -> str:
    """The cai-pad script with _STARTUP_SAT rendered in (lazy: pwa_assets
    imports this module, so the table is fetched at patch time, not import)."""
    sat = __import__("pwa_assets")._STARTUP_SAT
    table = "{" + ",".join(
        f'"{w}x{h}x{d}":{v}' for (w, h, d), v in sorted(sat.items())
    ) + "}"
    return (_PAD_JS_TEMPLATE.replace("__TABLE__", table)
            .replace("__IDCX__", str(__import__("pwa_assets")._ID_CX)))


_VIEWPORT_RE = re.compile(
    r'(<meta[^>]*\bname="viewport"[^>]*\bcontent=")(?P<val>[^"]*)(")'
)


def _cover_viewport(src: str) -> str:
    """Add the static viewport tokens to Streamlit's viewport meta, in place."""
    def add(m: re.Match) -> str:
        val = m.group("val")
        for tok in _STATIC_VIEWPORT_TOKENS:
            if tok.split("=")[0].lstrip(", ") not in val:
                val += tok
        return m.group(1) + val + m.group(3)

    return _VIEWPORT_RE.sub(add, src, count=1)


# Tokens THIS version never writes but older ones did. Without these a v12
# file could not be stripped back to pristine, so patch_index_html would see a
# leftover viewport-fit, trip its own guard and refuse to upgrade — a
# permanently stuck dev venv. Retired tokens go here — and viewport-fit=cover
# left this list on 2026-09-01 when it un-retired into the static set above.
_LEGACY_VIEWPORT_TOKENS = ()


def _uncover_viewport(src: str) -> str:
    """Inverse of _cover_viewport, so the patch round-trips byte-exactly."""
    def rm(m: re.Match) -> str:
        val = m.group("val")
        for tok in _STATIC_VIEWPORT_TOKENS + _LEGACY_VIEWPORT_TOKENS:
            val = val.replace(tok, "")
        return m.group(1) + val + m.group(3)

    return _VIEWPORT_RE.sub(rm, src, count=1)


def _font_data_uri() -> str:
    """Suez One as base64, or '' if the file is missing.

    THE reason this exists: the shell used to pull the wordmark font with
    <link rel="stylesheet" href="https://fonts.googleapis.com/..."> in <head>.
    A cross-origin stylesheet there is RENDER-BLOCKING — the browser paints
    nothing at all until it resolves, which on a cold phone launch means DNS +
    TLS + fetch to a third party before the first pixel. Measured on the live
    app 2026-07-27: that request completed at 10.8s, and the device video from
    the same day shows the boot splash appearing at exactly t=10.0 — until then
    the user stares at the frozen OS launch image with nothing moving on it.
    Inlining the bytes deletes the whole chain: the shell paints as soon as
    index.html lands, and the spinner below can actually spin during the wait.

    SUBSET woff2, not the full TTF — and the size is the whole point. The full
    face inlined as base64 was 92KB inside <head>, which made index.html 111KB,
    served UNCOMPRESSED (Tornado sends no content-encoding). The 2026-07-28
    evening device video shows what that costs: on a cellular link the page
    could not paint until ~8.5s after the tap, iOS gave up waiting, started
    dissolving the launch image into a still-unpainted (white) web view, and
    the first olive frame landed ONE FRAME after the dissolve peak. That is
    the residual white flash — an OS-level composite (the status bar washes
    out with it), unfixable by any CSS, only by painting sooner.

    branding/fonts/SuezOne-boot.woff2 is the same face subset to the ~30
    glyphs the splash can use (wordmark latin + full Hebrew + punctuation):
    6.6KB, base64 8.8KB, glyph rasters and advances verified pixel-identical
    to the full TTF — so every measured geometry constant survives. The PNG
    side (app._startup_png) keeps drawing with the full TTF via Pillow, which
    never travels over the network.
    """
    try:
        p = Path(__file__).parent / "branding" / "fonts" / "SuezOne-boot.woff2"
        return base64.b64encode(p.read_bytes()).decode("ascii")
    except Exception:
        return ""


# The color-scheme meta above is the LAST piece of the white-flash story. The
# 27KB diet and the progressive splash each shrank the flash (device videos:
# white peak 205 → 190 → 181 → 170 across four rounds) but could not zero it:
# the residue is 2-3 frames of Safari's paint-pipeline warm-up AFTER parsing,
# during which the compositor shows the DOCUMENT CANVAS — which defaults to
# WHITE no matter what CSS says, because CSS has not painted yet. color-scheme
# dark is the one lever that recolours that pre-paint canvas: the same 2-3
# frames become dark, and a dark blink inside an olive→olive dissolve reads as
# a shadow, not a flash. It also matches the app (dark theme, dark iOS
# keyboard). Applies at PARSE time, before any style resolution — which is the
# whole point.

# Micro-style, injected right after <meta charset> — the FIRST paintable rule
# in the document. The 2026-07-29 device video (three launches) showed the
# white flash surviving the 27KB diet for a subtle reason: shrinking the file
# made iOS dismiss the launch image EARLIER (dissolve at tap+2.4s, and ~0.5s
# on warm relaunches, tracking responseEnd), while first paint still trailed
# the last byte by a few frames. The dissolve therefore keeps landing on an
# unpainted (white) web view no matter how small the file gets — the race
# cannot be won by shrinking alone. It CAN be won by painting before the
# download finishes: this rule guarantees that whatever WebKit paints first,
# even mid-stream with only the head parsed, is already olive; and the splash
# markup now sits at the TOP of <body> (see patch_index_html) so the full
# splash is paintable at ~15KB into the stream instead of at the very end.
# :root{color-scheme:dark} rides in the SAME first-KB style: the meta version
# of the signal (id=cai-scheme, further down in <head>) declares the scheme at
# parse time, but the CSS property is what actually flips the computed value —
# verified in-browser: with the meta alone, getComputedStyle(:root).colorScheme
# stayed "normal"; with the property it reads "dark", and the pre-paint canvas
# follows the used scheme.
# Dark chain (2026-08-03, user decision): the launch image, the splash and
# the app now share ONE dark backdrop — the light-olive splash era ended
# because the sage→dark handoff read as a flash/"two screens" on device.
_MICRO = ('<style id="cai-micro">:root{color-scheme:dark}'
          'html,body{background:#14170E;margin:0}</style>')

# __FACE__ is substituted at patch time. A plain placeholder, not an f-string:
# this block is nearly all CSS braces and escaping them all would bury it.
#
# THE APPLE WEB-APP METAS ARE STATIC TOO (2026-09-06, v31). Until now
# apple-mobile-web-app-status-bar-style (black-translucent), -capable and
# -title reached the page only through app.py's runtime injector, 1-2s after
# load. Every cold launch therefore began with the DEFAULT status-bar
# treatment — a web view one status bar short (793 of 852pt on the pilot's
# phone) — and iOS re-laid the whole view the moment the meta landed: that
# re-layout is the viewport step the engine had to chase (793→852), the
# spinner's +78-row jump (15:56 video), the dark band under the splash for
# 0.35-1.5s (exactly as long as the server took to deliver the injector),
# and the intermittent lifted frame at the launch-image dissolve. The
# theme-color story below is the same bug class, fixed the same way: a meta
# that is present from the first byte never transitions.
#
# The <meta name="theme-color"> is HERE, statically, and not only in the
# runtime PWA injector — that placement is a bug fix, not tidiness. When the
# meta first appears at runtime (the Streamlit component lands ~3.5s after
# first paint), iOS standalone re-evaluates the status-bar treatment and
# RESIZES the web view by a few px. The splash's wait block is bottom-anchored,
# so it jumped: 2026-07-28 evening video, t=13.91s — a 57px one-frame spasm,
# settling 7px higher, logo rows untouched (the top anchor never moved, which
# is what pins the cause to a viewport-height change, not a scroll or rerun).
# Present from the first byte, the value never transitions and the viewport
# never steps. The runtime injector still exists for reruns, but writes only
# when the value actually differs.
_HEAD_TEMPLATE = """
    <meta id="cai-theme" name="theme-color" content="#14170E">
    <meta id="cai-scheme" name="color-scheme" content="dark">
    <meta id="cai-capable" name="apple-mobile-web-app-capable" content="yes">
    <meta id="cai-mcapable" name="mobile-web-app-capable" content="yes">
    <meta id="cai-sbstyle" name="apple-mobile-web-app-status-bar-style" content="black-translucent">
    <meta id="cai-apptitle" name="apple-mobile-web-app-title" content="CommandAI">
    <style id="cai-boot" data-cai-ver="__VER__">
      __FACE__
      html, body { background: #14170E; }
      /* height pinned to the GLASS, not the viewport (2026-09-06, device video
         15:56): iOS re-reports the layout viewport twice during a cold boot
         (793 → 852 → …) and the bottom-anchored wait block jumped with it
         (+78 then −19 rows). --cai-glass = screen.height from the cai-pad
         script; viewports off the PNG table fall back to 100vh as before. */
      #cai-boot-splash { position: fixed; inset: 0; min-height: var(--cai-glass, 100vh);
        box-sizing: border-box; /* the height is the whole box, padding-top included */
        z-index: 2147483000; background: #14170E;
        display: flex; flex-direction: column; align-items: center; justify-content: flex-start;
        /* --cai-pad = the launch PNG's own sat+14vh, set by #cai-pad at parse time
           (see _PAD_JS_TEMPLATE); env() is the fallback for screens off the table */
        padding-top: var(--cai-pad, calc(env(safe-area-inset-top, 0px) + 14vh));
        gap: 18px; transition: opacity .4s ease; pointer-events: none; }
      /* THE IDENTITY IS A RASTER — THE SAME RASTER THE LAUNCH PNG CARRIES (v43,
         2026-09-07). Two months of making CSS + fonts reproduce Pillow to the
         pixel (mitered chevron tips, 4x supersampling, the RTL letter-spacing
         quirk, content-box pins, a 43px chevron block, a 2px iOS nudge) still
         left one seam: on the 06.09 23:06 device video the dissolve from the
         launch image to the page shows the subtitle DOUBLED for ~12 frames —
         WebKit lays the letter-spaced Hebrew lines out a few px from where
         Chromium (which the PNG was calibrated against) does, and iOS
         cross-fades the two. Only one raster has no seam. So
         pwa_assets._identity_raster draws chevron + wordmark + subtitle once
         per device pixel ratio; the launch PNG pastes it at integer device
         pixels — pad = round(sat + 14vh) and left = floor(width/2) − 115,
         both WHOLE CSS px, because Blink snaps a box to integer CSS px before
         scaling while WebKit snaps to device px, and an integer CSS px is the
         same pixel in both (the Edge proof caught 81.333px painted at 81);
         and this rule paints the identical bytes at the identical device
         pixels: --cai-pad / --cai-idx come from the cai-pad script with the
         same integers, the box is 230x182pt, background-size pins one raster
         px to one device px. No font, no layout, nothing for the dissolve to
         show. Screens off the PNG table (Android, desktop) fall back to
         env()+14vh and centring — there is no launch image to match there. */
      #cai-boot-splash .id { position: absolute; width: 230px; height: 182px;
        top: calc(var(--cai-pad, calc(env(safe-area-inset-top, 0px) + 14vh)) - 12px);
        left: var(--cai-idx, calc(50% - 115px));
        background: url(__ID2X__) 0 0 / 230px 182px no-repeat; }
      @media (-webkit-min-device-pixel-ratio: 2.5), (min-resolution: 2.5dppx) {
        #cai-boot-splash .id { background-image: url(__ID3X__); } }
      /* NO lift choreography. A staggered per-element entrance was tried
         (2026-07-27, shell v4) and it FOUGHT Streamlit: reruns replace the
         DOM mid-cascade, so the curtain lifted onto a dark screen of
         opacity-0 elements and the composer popped in ~1.5s late (video #3,
         "פותח ומעלים את השאלה"). The Claude-app smoothness comes from the
         opposite move — the curtain waits until the screen is COMPLETE and
         geometrically settled (see ready()/stability in the script below),
         then lifts once over a finished static page. */
      /* Bottom-anchored waiting ring, matching .cai-splash-wait in app.py so the
         hand-off does not move it. Fades in at 1.2s (was 2.5s — on device every
         real load is slower than that, so the earlier fade only means the splash
         stops looking frozen sooner; a fast local load still never shows it).
         This is the ONLY moving thing on screen during the wait — the OS launch
         image before it cannot animate at all. */
      @keyframes caiBootSpin { to { transform: rotate(360deg); } }
      @keyframes caiBootFade { from { opacity: 0; } to { opacity: 1; } }
      /* The wait stack is bottom-anchored and GROWS UPWARD: the ring is its
         last child, so its distance from the bottom (14vh) is identical
         whether or not the long-wait copy above it is showing. The ring
         must not move — a splash element that shifts position mid-wait is
         exactly the "it keeps switching screens" the pilot reported. */
      /* the wait block hangs from the GLASS bottom (--cai-glass, cai-pad script),
         not from the splash box: iOS re-reports the viewport during a cold boot
         and a margin-anchored block rode every change (+78 rows, 15:56 video) */
      #cai-boot-splash .wait { position: absolute; left: 0; right: 0;
        top: calc(var(--cai-glass, 100vh) - var(--cai-vh14, 14vh)); transform: translateY(-100%);
        display: flex; flex-direction: column; align-items: center; gap: 13px; }
      #cai-boot-splash .w { width: 22px; height: 22px; margin: 0; box-sizing: content-box !important;
        border: 2px solid rgba(236,237,230,.20); border-top-color: rgba(236,237,230,.55);
        border-radius: 50%;
        animation: caiBootSpin .9s linear infinite, caiBootFade .5s ease both;
        animation-delay: 0s, 1.2s; }
      /* SAY SOMETHING when the boot drags. 2026-07-28 device video: 51s of
         splash with a silent spinner (index.html alone took 13.7s to land)
         — indistinguishable from a hang, and the pilot had no way to tell
         whether to keep waiting or force-quit. Hidden until armed, so a
         normal load never sees it and the geometry is untouched. */
      #cai-boot-splash .m { display: none; max-width: 78vw; text-align: center;
        font: 600 12px ui-monospace, Menlo, monospace; line-height: 1.7;
        color: rgba(236,237,230,.65); opacity: 0; transition: opacity .5s ease; }
      #cai-boot-splash .m.on { opacity: 1; }
      #cai-boot-splash .r { display: none; pointer-events: auto;
        font: 700 12px ui-monospace, Menlo, monospace; color: #C4CE92;
        background: rgba(163,174,110,.14); border: 1px solid rgba(163,174,110,.4);
        border-radius: 999px; padding: 8px 20px;
        animation: caiBootFade .4s ease both; }
      /* Connection bar — see the watchdog in the script below for WHY. Lives
         outside #root, so no Streamlit rerender can take it away. */
      #cai-net-bar { position: fixed; z-index: 2147483100;
        top: calc(env(safe-area-inset-top, 0px) + 8px); left: 10px; right: 10px;
        display: none; align-items: center; justify-content: space-between; gap: 10px;
        padding: 10px 14px; border-radius: 14px;
        background: #2B1E12; border: 1px solid rgba(236,237,230,.16);
        box-shadow: 0 10px 30px rgba(0,0,0,.45);
        font: 600 13px system-ui, -apple-system, "Segoe UI", sans-serif; color: #F0E7D8; }
      #cai-net-bar.on { display: flex; }
      #cai-net-bar button { flex: none; border: 0; border-radius: 999px;
        font: 700 13px system-ui, -apple-system, "Segoe UI", sans-serif;
        color: #171A12; background: #E8D9A8; padding: 7px 16px; }
      [data-testid="stSkeleton"], [data-testid="stAppSkeleton"],
      [data-testid="stStatusWidget"], [data-testid="stDecoration"] { display: none !important; }
      /* NO dim-before-lift. A two-beat lift (curtain darkens in place, then
         slides) was tried in v10 as a fix for "a black screen that gets
         stuck" — and the user rejected it on sight: the original single
         slide "looked much better" (2026-07-29). The "stuck black screen"
         itself turned out to be the keyboard-pan bounce, fixed at the
         source by the focus freeze in the boot script — so the dim never
         had a job. Do not bring it back. */
    </style>
"""

# Markup and script are SEPARATE chunks since v10: the markup goes to the TOP
# of <body> so the splash is paintable as soon as its bytes arrive (the whole
# point of the progressive-paint fix), while the script stays at the END of
# <body> — it wires listeners and wraps WebSocket, and running it during early
# parse would find half a DOM. The trailing comment anchor is what _strip
# removes up to; do not drop it.
_SPLASH_HTML = """
    <div id="cai-boot-splash" dir="rtl">
      <div class="id" role="img" aria-label="CommandAI"></div>
      <div class="wait">
        <div class="m"></div>
        <button class="r" type="button">נסה שוב</button>
        <div class="w"></div>
      </div>
    </div><script id="cai-painted">
      // Stamp the shell's presence on <html> before ANY app CSS can matter.
      // app.py keys boot-only styling off html.cai-shell (the entry screen's
      // entrance stagger is disabled under the curtain — it played to nobody
      // and the probe rerun REPLAYED it after the lift, which is the
      // "the opening assembles in two stages" report of 2026-08-31). The
      // class is permanent for the page's life: entry only ever renders at
      // boot or after logout, and both are exactly the moments the stagger
      // must not replay.
      try { document.documentElement.classList.add('cai-shell'); } catch (e) {}
      // cai-curtain lives exactly as long as the curtain: app.py keeps its
      // frosted overlays (composer strip, header band) unpainted under it —
      // a new backdrop-filter layer showed through the opaque curtain for
      // 5 frames on the 2026-09-06 20:21 device video. lift() drops it.
      try { document.documentElement.classList.add('cai-curtain'); } catch (e) {}
      // No veil (v42): the launch image carries the logo and the splash paints
      // the same raster from its first frame — see the .id note in the CSS.
      // THE CANVAS IS OLIVE FROM THE FIRST FRAME (2026-09-06, videos 17:11/
      // 17:14 and every device video back to 04.09): for 0.4-1.5s after the
      // launch-image dissolve a band the height of the status bar showed at
      // the bottom in rgb(16,17,20) — Streamlit's stock dark background
      // (#0E1117), which its bundle puts on <body> until the server's theme
      // (#14170E) arrives. iOS paints the web view taller than the layout
      // viewport during that window, so the canvas below the fixed splash is
      // on the glass. lift() already hands the document over inline-important
      // at the END of the boot; do it here, at parse time, so the stock
      // colour never gets a frame. Inline-important outranks the bundle's
      // class; app.py's syncCanvas writes inline-important too and lands
      // later, so dialog/drawer canvases still win.
      try {
        document.documentElement.style.setProperty('background', '#14170E', 'important');
        if (document.body) document.body.style.setProperty('background', '#14170E', 'important');
      } catch (e) {}
      // TELL THE WORKER THE MOMENT THE SPLASH IS ON THE GLASS.
      //
      // The worker holds this response open so iOS cannot start dissolving its
      // launch image before the splash is painted (see held() in _SW_JS). It
      // used to hold for a fixed 250ms, and a fixed number is a guess: measured
      // on the 2026-08-09 20:47 video, four launches, the paint lands within a
      // frame of the response ending either way, so two launches came out clean
      // (1.01x, 1.08x) and two caught a single white frame (1.31x, 1.43x). The
      // guess bought two frames of the original three; it cannot buy the third,
      // because the thing it is racing varies per launch.
      //
      // So stop guessing and report. Double rAF is "after the frame that
      // painted what has been parsed so far", and this script sits immediately
      // after the complete splash markup, so that frame IS the splash.
      //
      // Deliberately NOT deferred to the boot script at the end of <body>:
      // that lives after the split and would not arrive until the hold is
      // already over — it could never report anything in time.
      (function () {
        try {
          var sw = navigator.serviceWorker;
          if (!sw || !sw.controller) return;
          var ping = function () {
            try { sw.controller.postMessage('cai-painted'); } catch (e) {}
          };
          // A THIRD frame since 2026-09-04: the 21:58 device video still caught
          // one lifted frame (+15 levels on the backdrop, 16ms) right as the
          // dissolve began — the web process had painted, the UI process had
          // not yet composited it. Double rAF reports "painted", not "on the
          // glass"; one more frame covers the commit. Costs 16ms of hold.
          if (window.requestAnimationFrame) {
            // FIVE frames since 2026-09-06: with three, the 15:56 device video
            // still caught one frame of WebKit's pre-paint canvas dissolving in
            // (+6 levels on the backdrop, every launch) — the composited splash
            // had not reached the glass when the response ended. Two more
            // frames of hold (~33ms) for the UI-process commit.
            var hops = 5, hop = function () { if (--hops <= 0) ping(); else requestAnimationFrame(hop); };
            requestAnimationFrame(hop);
          } else {
            setTimeout(ping, 50);
          }
        } catch (e) {}
      })();
    </script><!--/cai-boot-splash-->
"""

_BOOT_JS = """
    <script id="cai-boot-js">
      // ── connection watchdog ──
      // A dropped websocket was COMPLETELY invisible. Streamlit's only
      // disconnect indicator is [data-testid="stStatusWidget"], and both
      // this shell and the app CSS hide it with display:none to keep the
      // platform chrome off the screen. Verified live on the deployed app
      // 2026-07-28: close the socket and the page stays fully painted,
      // undimmed, with no toast and no dialog — the widget is there,
      // reading "Connecting", invisible — while every server-backed
      // control is dead. The drawer keeps sliding because it is pure
      // client-side JS. That is precisely the "the whole screen was stuck,
      // nothing was clickable except moving the menu tab" report from that
      // morning, and why closing the tab and reopening it fixed it.
      //
      // This script is a CLASSIC script inside <body>, so it runs during
      // parsing — before Streamlit's deferred <script type="module">
      // bundle. Wrapping window.WebSocket here is therefore guaranteed to
      // catch the app's socket, no matter when the bundle opens it.
      (function () {
        try {
          var OW = window.WebSocket;
          if (!OW || OW.__cai) return;
          var live = 0, armed = false, timer = null, bar = null;
          // force=true means "the radio is off, do not consult `live`" — the
          // socket has not fired `close` yet at that moment, so the usual
          // guard below would suppress the one case we are certain about.
          var show = function (force) {
            // never over the curtain — the boot splash speaks for itself
            if (document.getElementById('cai-boot-splash')) {
              setTimeout(function () { show(force); }, 1000); return;
            }
            if (!bar) {
              bar = document.createElement('div');
              bar.id = 'cai-net-bar';
              bar.setAttribute('dir', 'rtl');
              var t = document.createElement('span');
              t.textContent = 'אין חיבור לשרת — מנסים להתחבר מחדש…';
              var b = document.createElement('button');
              b.type = 'button';
              b.textContent = 'רענון';
              b.addEventListener('click', function () { location.reload(); });
              bar.appendChild(t); bar.appendChild(b);
              document.body.appendChild(bar);
            }
            if (force || live <= 0) bar.classList.add('on');
          };
          var arm = function () {
            clearTimeout(timer);
            // a rerun-time blip reconnects in well under a second; only a
            // real outage survives this
            timer = setTimeout(function () { if (live <= 0) show(); }, 4000);
          };
          var CW = function (url, protocols) {
            var w = (protocols === undefined) ? new OW(url) : new OW(url, protocols);
            try {
              if (String(url).indexOf('_stcore/stream') >= 0) {
                var up = false;
                w.addEventListener('open', function () {
                  up = true; armed = true; live++;
                  clearTimeout(timer);
                  if (bar) bar.classList.remove('on');
                });
                w.addEventListener('close', function () {
                  if (up) { up = false; live--; }
                  if (armed) arm();
                });
                w.addEventListener('error', function () { if (armed) arm(); });
              }
            } catch (e) {}
            return w;
          };
          CW.prototype = OW.prototype;
          CW.__cai = true;
          ['CONNECTING', 'OPEN', 'CLOSING', 'CLOSED'].forEach(function (k) { CW[k] = OW[k]; });
          window.WebSocket = CW;
          // a FIRST connection that never lands must also speak up — armed
          // only ever flips on a successful open, so without this the bar
          // could never appear on a boot that fails outright
          setTimeout(function () { if (live <= 0) { armed = true; arm(); } }, 25000);
          // A radio that is plainly off does not need the 4s debounce: that
          // delay exists to ride out rerun-time socket blips, and this is not
          // one. navigator.onLine going false is unambiguous, so say so at
          // once instead of making the user wait out a timer meant for a
          // different failure. Measured on 2026-08-10: without show(true) the
          // bar stayed hidden here, because `close` has not fired yet at this
          // point and the guard inside show() still saw live === 1.
          window.addEventListener('offline', function () {
            if (armed) { clearTimeout(timer); show(true); }
          });
          window.addEventListener('online', function () {
            // A blink short enough that the socket never fired `close` leaves
            // `live` at 1, so the open-handler that normally clears the bar
            // will never run again — without this the forced bar would stay
            // up for the rest of the session. If the socket DID die, fall
            // back to the normal debounce rather than hiding prematurely.
            if (live > 0) { if (bar) bar.classList.remove('on'); }
            else arm();
          });
        } catch (e) {}
      })();
      // ── focus freeze, curtain scope only ──
      // PREVENTION, not correction. v10's guard blurred the focus and
      // snapped the viewport back AFTER iOS had already panned — which
      // traded "the screen sticks shifted" (2026-07-29 morning video) for
      // "the screen hops up and back down" (same day, 13:19 videos: an
      // identical diff signature at t=3.95 in one and t=45.2 in the other —
      // both the moment Streamlit mounts its chat input behind the curtain
      // and calls .focus() on it). The only version with NO visible artifact
      // is the one where the pan never starts: while the splash exists,
      // .focus() on anything outside it is a no-op. The wrapper delegates
      // untouched once the splash is gone, so real keyboard use after boot
      // is unaffected. __caiFocusFrozen counts suppressions — a debug
      // window into whether the app really does grab focus mid-boot.
      (function () {
        try {
          var OF = HTMLElement.prototype.focus;
          window.__caiFocusFrozen = 0;
          HTMLElement.prototype.focus = function () {
            var sp = document.getElementById('cai-boot-splash');
            if (sp && !sp.contains(this)) { window.__caiFocusFrozen++; return; }
            return OF.apply(this, arguments);
          };
        } catch (e) {}
      })();
      // ── keyboard-pan backstop, curtain scope only ──
      // The freeze above stops focus() calls; this catches what it cannot —
      // an autofocus attribute processed by the UA, or focus from inside an
      // iframe — and repairs the pan before it can stick. With the freeze in
      // place this should never fire; it is insurance, not the mechanism.
      (function () {
        var unpan = function () {
          if (window.scrollX || window.scrollY) window.scrollTo(0, 0);
        };
        var live = function () { return !!document.getElementById('cai-boot-splash'); };
        var onFocus = function (e) {
          if (!live()) return;
          var sp = document.getElementById('cai-boot-splash');
          var t = e.target;
          if (t && t.blur && !(sp && sp.contains(t))) { try { t.blur(); } catch (x) {} }
          setTimeout(unpan, 50); setTimeout(unpan, 350);
        };
        var onScroll = function () { if (live()) unpan(); };
        document.addEventListener('focusin', onFocus, true);
        window.addEventListener('scroll', onScroll, true);
        var sweep = setInterval(function () {
          if (live()) { unpan(); return; }
          // curtain gone: one last reset, then leave the page alone
          unpan();
          clearInterval(sweep);
          document.removeEventListener('focusin', onFocus, true);
          window.removeEventListener('scroll', onScroll, true);
        }, 400);
      })();
      (function () {
        var el = document.getElementById('cai-boot-splash');
        if (!el) return;
        var gone = false;
        // Curtain, not a fade. This shell is now the ONE loading screen — the
        // app no longer draws a second, near-identical splash underneath it
        // (see splash_active in app.py), so there is nothing to cross-fade to
        // and the reveal can be the real thing: the screen slides up off the
        // glass and the app is simply there behind it.
        // Escalating copy for a boot that drags. On the 2026-07-28 launch
        // index.html alone took 13.7s (365ms warm) and the first complete
        // screen 51s; the splash said nothing the whole time. These fire
        // only past the point where the load is already abnormal, so a
        // healthy boot never shows any of them.
        var msg = el.querySelector('.m'), rtry = el.querySelector('.r');
        var say = function (t) {
          if (!msg || gone) return;
          msg.textContent = t; msg.style.display = 'block';
          setTimeout(function () { msg.classList.add('on'); }, 20);
        };
        if (rtry) rtry.addEventListener('click', function () { location.reload(); });
        var slow = [
          // 2.5s, together with the ring — deliberately NOT the "only past
          // the point where the load is abnormal" threshold this used to sit
          // at (12s). On the pilot's phone a cold boot is 25-37s, so abnormal
          // IS normal, and 12s of a silent spinner was the complaint. Both
          // fade in on the same tick, so the wait acquires a voice in one
          // move instead of two.
          setTimeout(function () { say('מכינים את המערכת…'); }, 2500),
          setTimeout(function () { say('החיבור איטי מהרגיל — עדיין טוענים'); }, 28000),
          setTimeout(function () { if (!gone && rtry) rtry.style.display = 'block'; }, 45000)
        ];
        // COMPOSER RE-MEASURE (2026-09-06, device videos 02:29 and 15:18 vs
        // 21:58 the day before). Streamlit's chat textarea is sized by
        // react-textarea-autosize: it measures a hidden clone carrying the
        // PLACEHOLDER text at the textarea's width of THAT moment, writes the
        // result as an inline height, and re-measures only on a window
        // resize or a value change. Since the boot got faster (deflate, no
        // watcher stall) the composer mounts earlier in the layout, and on
        // the device the first measurement came out three rows tall — the
        // capsule opened into a box with the placeholder at the top and the
        // arrow at the bottom, and it stayed that way (nothing ever re-
        // measured). No app CSS changed between the two states. So: give
        // autosize a resize to re-measure with the settled width (once under
        // the curtain, once after it), and if an EMPTY composer still holds
        // an inline height above one row, drop the stale height — the next
        // measurement starts from the natural single row.
        var composerRemeasure = function () {
          try {
            // the empty-capsule class (app.py .cai-empty) before the dwell
            // paints the strip — the engine's heal() keeps it in sync later
            var ta0 = document.querySelector('[data-testid="stChatInput"] textarea');
            var ci0 = ta0 && ta0.closest('[data-testid="stChatInput"]');
            if (ci0) ci0.classList.toggle('cai-empty', ta0.value === '');
            // v44: the engine's guard pins the one-row geometry on every
            // frame for 2.5s from here (see COMPOSER GUARD in app.py)
            try { if (window.__caiPinBurst) window.__caiPinBurst(); } catch (e) {}
            window.dispatchEvent(new Event('resize'));
            setTimeout(function () {
              try {
                var ta = document.querySelector('[data-testid="stChatInput"] textarea');
                if (!ta || ta.value) return;
                if (ta.getBoundingClientRect().height > 40) {
                  ta.style.removeProperty('height');
                  window.dispatchEvent(new Event('resize'));
                }
              } catch (e) {}
            }, 120);
          } catch (e) {}
        };
        var lift = function () {
          if (gone) return; gone = true;
          slow.forEach(clearTimeout);
          composerRemeasure();
          // The wait ring must NOT ride the curtain: it kept spinning during
          // the slide and lingered as a lone circle over the revealed home
          // (user's 60fps slow-motion, 2026-09-03). Fade it during the paint
          // dwell below, so by the time the slide starts the curtain carries
          // only the identity block.
          try {
            var w = el.querySelector('.wait');
            if (w) { w.style.transition = 'opacity .15s ease'; w.style.opacity = '0'; }
          } catch (e) {}
          // Hand the DOCUMENT background over to the app's dark, now, while
          // the curtain still covers the screen — so the change itself is
          // invisible and what the curtain uncovers is one consistent colour.
          //
          // The document is olive for the whole boot (see _MICRO), and with
          // black-translucent the web view extends UNDER the status bar into
          // a strip the app does not paint. So the olive survived the lift:
          // a green band stuck behind the clock until app.py's darkenShell
          // component mounted and set html/body to #14170E. Measured on the
          // 2026-07-29 14:55 video — app goes dark at t=3.84, the band only
          // follows at t=4.24. 400ms of a stale splash colour framing a dark
          // app, on every single launch.
          //
          // !important because darkenShell sets it that way too; matching
          // colour and priority means its later write is a no-op rather than
          // a second repaint.
          try {
            document.documentElement.style.setProperty('background', '#14170E', 'important');
            document.body.style.setProperty('background', '#14170E', 'important');
          } catch (e) {}
          // ...and MAKE IOS ACT ON IT NOW, before the slide starts.
          //
          // Setting the colour is not enough on its own: measured on the
          // 2026-07-30 10:58 video, the status-bar strip held its olive for
          // the entire slide and only turned dark two frames after
          // el.remove(). While an OPAQUE element covers the whole layout
          // viewport, WebKit skips painting the canvas behind it — so the
          // handover above sits queued and the strip keeps showing the stale
          // olive raster until the element goes away. That is why the colour
          // change kept landing AFTER the app was already revealed, which is
          // exactly what reads as "the top is still settling".
          //
          // Dropping opacity a hair below 1 for a single frame makes the
          // curtain a non-opaque layer, which forces that skipped paint to
          // happen. 0.999 is imperceptible (the app beneath shows at 0.1%) and
          // it is set back before the transform starts, so the slide itself is
          // fully opaque — no double exposure (2026-07-27 video #5). Cost: one
          // frame, 16ms, before the motion begins.
          //
          // The win is not less total change, it is WHERE the change sits: the
          // strip darkens under the still-covering curtain instead of after
          // the reveal, so iOS re-renders its clock during the sweep rather
          // than on top of a finished screen.
          // Travel in PIXELS, computed here — never a percentage and never
          // calc(). translateY(calc(-100% - 90px)) was tried twice and never
          // worked: WebKit will not interpolate a transform transition whose
          // end value mixes % and px, and falls back to the percentage term
          // with nothing logged anywhere. Math.max with a floor because a
          // hidden/backgrounded web view can report screen.height AND
          // innerHeight as 0, and a 40px "slide" would never leave the glass.
          //
          // OVERSHOOT ×2, because the easing's TAIL has to land off the glass.
          // cubic-bezier(.7,0,.3,1) is symmetric: it decelerates through its
          // entire second half. Travelling only screen+40 put the moment the
          // bottom edge crosses the top of the glass at 89% of the distance —
          // i.e. deep inside that tail. Measured on the 2026-07-31 video: the
          // edge peaked at 97 rows/frame and was down to 43 by the time it
          // reached the top, 103ms of visibly slowing curtain, and THAT is
          // what "it gets stuck a bit at the end and doesn't lift perfectly"
          // is. Nothing to do with the strip (that case is closed) — the
          // curtain was easing out ON SCREEN.
          //
          // Doubling the distance moves the crossing to under 50% of the
          // travel on EVERY geometry — the glass is at most `screen` tall and
          // the travel is always 2*(screen+40) — so the edge now leaves at
          // ~98% of peak speed and the whole deceleration happens above the
          // notch, where there is nothing to see. LIFT_MS grows to match so
          // the VISIBLE sweep keeps its old tempo: 367ms against 378ms
          // before, i.e. the same lift, minus the stall. The extra ~390ms of
          // transition is never played — `reap` below drops the element on
          // the frame its bottom clears 0.
          var LIFT_MS = 760, LIFT_OVERSHOOT = 2;
          var travel = (Math.max(
            (window.screen && screen.height) || 0,
            window.innerHeight || 0,
            el.offsetHeight || 0,
            900) + 40) * LIFT_OVERSHOOT;
          var slide = function () {
            el.style.opacity = '';
            el.style.transition = 'transform ' + LIFT_MS + 'ms cubic-bezier(.7,0,.3,1)';
            el.style.transform = 'translateY(-' + travel + 'px)';
            // REAP ON THE FIRST FRAME IT IS GENUINELY GONE, not on a timer.
            // The strip turns dark when this element is removed, so a fixed
            // delay is a fixed delay of stale olive: the transform needs ~48%
            // of its 760ms to carry the box out of view, and the old 620ms
            // timer then sat on the stale raster for another ~264ms with the
            // app already revealed. Measured 284ms on the 2026-07-29 video,
            // predicted 264ms — the model and the phone agree. Polling the
            // rect cuts it to a single frame. The timer stays as a safety net
            // for a transition that never fires (backgrounded tab, reduced
            // motion) — it is deliberately SHORTER than LIFT_MS: by 700ms a
            // running transition is 1776px up, long gone, so removing there
            // is invisible, while a transition that never started must not be
            // left on screen a moment longer than it already is.
            var reap = function () {
              if (!el.parentNode) return;
              var b = 1;
              try { b = el.getBoundingClientRect().bottom; } catch (e) { b = -1; }
              if (b <= 0) { el.remove(); composerRemeasure(); return; }
              requestAnimationFrame(reap);
            };
            requestAnimationFrame(reap);
            setTimeout(function () { if (el.parentNode) el.remove(); }, 700);
          };
          // One PAINTED frame at 0.999 to force the skipped canvas paint, then
          // slide.
          //
          // DOUBLE rAF, and that is the whole point. A single
          // requestAnimationFrame does NOT mean "after the next paint" — the
          // callback runs inside the next frame's rendering steps, BEFORE
          // style/layout/paint. `slide()` clears the opacity on its first
          // line, so with one rAF the 0.999 was set and unset within the same
          // frame and never composited: the two writes collapse and the whole
          // manoeuvre is a no-op. That is why the status-bar strip kept its
          // stale olive for the entire slide on the 2026-07-31 video, exactly
          // as it did before this was added. Nesting the second rAF gives the
          // 0.999 one real frame on screen, which is what makes WebKit repaint
          // the canvas it skips while an opaque element covers the layout
          // viewport — and iOS only learns the new colour from that repaint.
          //
          // The timer is NOT redundant with the rAF. requestAnimationFrame does
          // not fire at all in a hidden or backgrounded web view, and handing
          // the slide to it alone meant the curtain never lifted in that case —
          // caught here because the test browser's pane is hidden and
          // reproduced it exactly: splash still on screen, app fully booted
          // behind it. Whichever fires first wins; `started` keeps it to one.
          // 80ms because the rAF path now needs TWO frames (33ms) and must be
          // allowed to win whenever frames are being produced at all.
          // ...and DWELL there, because one frame at 0.999 is enough for the
          // canvas and not for the app.
          //
          // Measured on the 2026-08-09 18:31 video, four launches: the curtain
          // finishes its slide and the app then assembles itself ON SCREEN over
          // 117ms — composer, then chips, then the greeting, then the header,
          // bottom to top, trailing the curtain edge. For ~60ms of that the
          // screen is nearly empty (luma 24.7) before filling to 31.5-33.0.
          // That is the "it doesn't come up clean" report, and it is the same
          // mechanism this manoeuvre already documents for the status bar:
          // WebKit skips painting whatever an opaque full-screen element
          // covers, so nothing behind the curtain has EVER been painted when
          // the slide starts.
          //
          // 117ms in all four launches, to the millisecond, while the boots
          // themselves ranged 3.08-3.30s — a fixed pipeline cost, not a race
          // with Streamlit. A rerun-timing race would have varied.
          //
          // So hold the curtain non-opaque long enough for that paint to
          // happen underneath it, where it is invisible, and let the slide
          // uncover a finished screen. PAINT_MS has ~55% headroom over the
          // measured 117ms. It costs that long before the slide begins and
          // buys back the 117ms of assembly at the end, so the boot grows by
          // well under the number the eye actually notices.
          //
          // The rAF pair still comes first: the dwell has to start from a
          // frame that was really composited at 0.999, which is the whole
          // point of the double rAF above. The timer fallback has to outlast
          // the dwell or it would pre-empt it — but it must still fire in a
          // hidden web view where rAF never runs at all.
          var PAINT_MS = 180;
          var started = false;
          var go = function () { if (started) return; started = true; slide(); };
          // the frosted overlays may paint now — under the curtain, during
          // the dwell below, never on the glass (see html.cai-curtain in app.py)
          try { document.documentElement.classList.remove('cai-curtain'); } catch (e) {}
          el.style.opacity = '0.999';
          if (window.requestAnimationFrame) {
            requestAnimationFrame(function () {
              requestAnimationFrame(function () { setTimeout(go, PAINT_MS); });
            });
          }
          setTimeout(go, PAINT_MS + 80);
        };
        // Wait for a COMPLETE screen, not for any markdown: the app emits its
        // CSS as a markdown element long before it renders anything a person
        // can read, and lifting early exposes a half-painted app (the
        // "Missing Submit Button" frame, video #1). On the chat home the
        // composer must exist too — a reveal without the question bar reads
        // as broken (video #3). The anchor element doubles as the stability
        // probe below.
        var PIN_WAIT_MS = 4000, pinWaitSince = 0;
        var ready = function () {
          // The SETTLED marker comes first: app.py emits it only on a run
          // whose device profile is resolved (cookie fast-path, or the
          // profile probe's round-trip completed). Without it the curtain
          // lifted on the PRE-probe run — .cai-entry existed and held still,
          // the lift fired, and the probe's rerun then rebuilt the screen in
          // the open (the "two screens" opening, 2026-08-31). The 90s
          // failsafe below still covers a probe that never answers.
          if (!document.querySelector('[data-cai-settled]')) return null;
          // VIEWPORT PIN GATE (2026-09-05). In standalone the app's viewport
          // engine pins --cai-vvh on <html> once the glass height is
          // confirmed; the composer strip is positioned by it. The 02:29
          // device video (four launches) showed the pin landing ~200ms
          // AFTER the lift — the strip and the disclaimer dropped one status
          // bar in the open. So a standalone boot waits for the pin, bounded:
          // the engine confirms within ~300ms of its first sample (cover
          // fast path in app.py), and PIN_WAIT_MS caps a device where it
          // never arrives so the 90s failsafe is not the only way out.
          if (window.__caiSA && !document.documentElement.style.getPropertyValue('--cai-vvh')) {
            if (!pinWaitSince) pinWaitSince = Date.now();
            if (Date.now() - pinWaitSince < PIN_WAIT_MS) return null;
          }
          var scr = document.querySelector('.cai-entry, .st-key-cai_name_card, .cai-splash');
          if (scr) return scr;
          var chat = document.querySelector('.cai-greet, .cai-header');
          if (!chat) return null;
          return document.querySelector('[data-testid="stChatInput"]') ? chat : null;
        };
        // Lift only once the layout is SETTLED: the anchor's position must
        // hold still for 3 consecutive samples — a Streamlit rerun
        // replacing the DOM mid-boot resets the count, so the curtain never
        // rises over a page that is still being rebuilt (the dark-flash +
        // popping-in reveal of video #3).
        // 100ms samples since 2026-09-06 (were 150): the settled marker, the
        // script-state check and the viewport-pin gate above now carry the
        // "is it really built" question, so the geometry watch only has to
        // catch a late layout shift — 300ms of stillness does that, and the
        // ~350ms it gives back is the largest lever left on a warm boot
        // (measured: ready→dwell 700ms → ~350ms).
        var TICK_MS = 100, STABLE_N = 3, POST_MS = 100;
        var lastY = -1e9, stable = 0;
        var tick = setInterval(function () {
          // a rerun mid-boot dims the whole app (stale elements) — lifting
          // during one reveals a grey half-page; hold until the script run
          // settles. Attribute absent (older Streamlit) → never 'running',
          // check degrades to geometry-only.
          var app = document.querySelector('.stApp');
          if (app && app.getAttribute('data-test-script-state') === 'running') {
            stable = 0; return;
          }
          var a = ready();
          if (!a) { lastY = -1e9; stable = 0; return; }
          var y = 0;
          try { y = a.getBoundingClientRect().top; } catch (e) {}
          stable = (Math.abs(y - lastY) < 1) ? stable + 1 : 0;
          lastY = y;
          if (stable >= STABLE_N) { clearInterval(tick); setTimeout(lift, POST_MS); }
        }, TICK_MS);
        setTimeout(function () { clearInterval(tick); lift(); }, 90000);
      })();
    </script>
"""


def _index_path() -> Path:
    return Path(inspect.getfile(st)).parent / "static" / "index.html"


def publish_root(name: str, data: bytes) -> str:
    """Write a file into the document ROOT, so it is served at /<name>.

    publish_static puts things under /static/cai/, which is right for assets
    and wrong for a service worker: a worker may only control URLs at or below
    its own path, and the thing worth controlling is the document at /. So this
    one writes beside index.html instead.

    Streamlit's handler falls back to the SPA document for unknown paths, so
    /sw.js answered 200 with text/html long before this existed — the same trap
    publish_static documents. Presence is proved by content-type, never status.
    """
    try:
        p = _index_path().parent / name
        if not p.exists() or p.read_bytes() != data:
            p.write_bytes(data)
        return "/" + name
    except Exception:
        return ""


def publish_static(name: str, data: bytes) -> str:
    """Write a PWA asset beside Streamlit's bundle and return its STABLE URL.

    Streamlit's MediaFileManager hands out /media/<content-hash>.<ext>, and
    those URLs are per-process and garbage-collected — which is fine for an
    image inside a running session and completely wrong for anything the
    OPERATING SYSTEM remembers. iOS snapshots the manifest URL at
    add-to-home-screen time and re-reads it on later launches; ours was
    already dead by the next deploy (verified 2026-07-29: the exact URL the
    pilot's icon was installed with answered 404). A standalone web app with
    an unreachable manifest has no background_color, so iOS falls back to a
    WHITE web-view backdrop — and the launch-image dissolve then blends into
    white instead of olive. That is the residual flash, and no amount of
    paint-timing work could have fixed it.

    Streamlit's document root is the directory holding index.html, and the
    bundle it references as ./static/js/... lives one level deeper — so the
    URL /static/cai/<name> maps to <docroot>/static/cai/<name>, NOT to
    <docroot>/cai/<name>. Writing to the shallower path silently produced a
    directory nothing served: every probe came back 200 with index.html,
    because Streamlit's handler falls back to the SPA document for unknown
    paths. A 200 is therefore NOT proof an asset exists — always compare the
    bytes or the content-type.

    The path stays stable forever (an installed icon must never meet a 404),
    but the returned URL carries ?v=<content-hash>, because Streamlit serves
    /static/ as `public, immutable, max-age=31536000`. `immutable` is a promise
    that the bytes at this URL will never change — and we were breaking it on
    every deploy, so WebKit kept the ones it already had and never even sent a
    conditional request. That is why the 2026-08-04 dark launch chain was
    invisible on the pilot's phone: the sage PNG was cached under the exact URL
    the dark one was published to, and removing + re-adding the home-screen
    icon does not purge WebKit's shared HTTP cache (verified 2026-08-05 from a
    device video — 665-1300ms of sage while production served #14170E). Same
    gotcha, same fix as the sw.js registration.
    """
    try:
        d = _index_path().parent / "static" / "cai"
        d.mkdir(parents=True, exist_ok=True)
        p = d / name
        if not p.exists() or p.read_bytes() != data:
            p.write_bytes(data)
        return "/static/cai/" + name + "?v=" + hashlib.sha1(data).hexdigest()[:8]
    except Exception:
        return ""


# Streamlit's own stylesheet link, matched attribute-order-agnostically because
# the bundle hash — and, across versions, the attribute order — moves.
_CSS_LINK_RE = re.compile(
    r'<link\b(?=[^>]*\brel="stylesheet")'
    r'(?=[^>]*\bhref="(?P<href>\./static/css/[^"]+)")[^>]*>'
)
# ...and the swapped form this module leaves in its place, with the untouched
# original parked inside the <noscript> so _strip can put it back verbatim.
_CSS_SWAP_RE = re.compile(
    r'<link\b[^>]*\bid="cai-css-swap"[^>]*>\s*<noscript>(?P<orig><link\b[^>]*>)</noscript>'
)


def _deblock_css(src: str) -> str:
    """Make Streamlit's stylesheet non-render-blocking.

    THE white-flash fix. index.html ships

        <link rel="stylesheet" crossorigin href="./static/css/index.<hash>.css">

    in <head>, and a render-blocking stylesheet means the browser paints NOTHING
    — not even a background colour — until it resolves. On a cold PWA launch iOS
    dismisses its launch image on its own schedule, so it hands over to a web
    view that has not painted yet: white. Measured on the 2026-07-28 device video
    at 60fps, mean frame brightness runs 128 (olive) → 205 → snaps back to 127
    over 130ms at t=11.75s, right where the launch image gives way. Same shape of
    bug as the render-blocking Google Fonts <link> deleted on 2026-07-27, and the
    last one left on the boot path.

    preload+swap rather than the media="print" trick: both are non-blocking, but
    media="print" also drops the request's priority, and this stylesheet is
    wanted as soon as possible — just not *before the first pixel*. The
    <noscript> copy keeps the page styled with JS off and doubles as _strip's
    restore source.

    Streamlit mounting for a moment without its CSS is invisible: the boot splash
    is an opaque full-screen curtain at z-index 2147483000, and the lift waits on
    three consecutive stable geometry samples — the reflow when the CSS lands
    resets that counter instead of revealing a half-styled page.
    """
    def swap(m: re.Match) -> str:
        href = m.group("href")
        return (
            '<link id="cai-css-swap" rel="preload" as="style" crossorigin '
            f'href="{href}" onload="this.onload=null;this.rel=\'stylesheet\'">'
            f'<noscript>{m.group(0)}</noscript>'
        )

    return _CSS_LINK_RE.sub(swap, src, count=1)


def _strip(src: str) -> str:
    """Remove any previously injected boot shell, of any version.

    Anchored on ids that only ever appear in our own block, so this cannot
    touch Streamlit's markup. Covers the v1 shape too — its render-blocking
    <link id="cai-boot-font"> is exactly what v2 exists to delete.

    ORDER MATTERS for the splash block. Through v9, markup and script were one
    contiguous chunk and the pattern spanned <div id="cai-boot-splash"> to the
    first </script>. Since v10 they are two chunks with Streamlit's own markup
    (including <div id="root">) BETWEEN them — running the old spanning
    pattern on a v10 file would swallow #root and everything else in between.
    So: the legacy pattern runs only when the v10 end-comment anchor is
    absent, and the v10 patterns are anchored on that comment and on the
    script's own id.
    """
    src = re.sub(r'\s*<link id="cai-boot-font"[^>]*>', "", src)
    src = re.sub(r'\s*<meta id="cai-theme"[^>]*>', "", src)
    src = re.sub(r'\s*<meta id="cai-scheme"[^>]*>', "", src)
    for mid in ("cai-capable", "cai-mcapable", "cai-sbstyle", "cai-apptitle"):
        src = re.sub(r'\s*<meta id="' + mid + '"[^>]*>', "", src)
    # the static PWA links (manifest / icon / launch images) — anchored on our
    # own id/class, glued back-to-back by _pwa_links so no whitespace to eat
    src = re.sub(r'<link id="cai-manifest"[^>]*>', "", src)
    src = re.sub(r'<link id="cai-icon"[^>]*>', "", src)
    src = re.sub(r'<link rel="apple-touch-startup-image" class="cai-launch"[^>]*>',
                 "", src)
    # the lazy-chunk hints — glued back-to-back like the PWA links above, so
    # this eats exactly the bytes the insert added and the round-trip stays
    # byte-exact
    src = re.sub(r'<link rel="modulepreload" class="cai-preload"[^>]*>', "", src)
    src = re.sub(r'<style id="cai-micro"[^>]*>.*?</style>', "", src, flags=re.S)
    src = re.sub(r'<script id="cai-pad">.*?</script>', "", src, flags=re.S)
    src = re.sub(r'\s*<style id="cai-boot".*?</style>', "", src, flags=re.S)
    if "<!--/cai-boot-splash-->" in src:
        # v10+: markup (to its end-comment anchor) and script, separately.
        # Each pattern removes EXACTLY the bytes the insert added — one
        # leading newline, not \s*: a greedy whitespace prefix here swallowed
        # the blank lines that belong to the host file and broke the
        # byte-exact round-trip (caught by the invariant test, 2026-07-29)
        src = re.sub(r'\n[ \t]*<div id="cai-boot-splash".*?<!--/cai-boot-splash-->\n',
                     "", src, flags=re.S)
        src = re.sub(r'\n[ \t]*<script id="cai-boot-js">.*?</script>\n',
                     "", src, flags=re.S)
    else:
        # ≤v9: one contiguous div..script chunk
        src = re.sub(r'\s*<div id="cai-boot-splash".*?</script>\n?',
                     "", src, flags=re.S)
    # restore Streamlit's stylesheet link from the <noscript> copy, so a
    # re-patch starts from pristine markup instead of stacking swaps
    src = _CSS_SWAP_RE.sub(lambda m: m.group("orig"), src)
    # legacy (v40 only): a dev venv patched by v40 carries the inert bundle tag;
    # restore Streamlit's own tag so the round trip lands on pristine markup
    src = src.replace('<script id="cai-bundle" type="cai/module" crossorigin src="',
                      '<script type="module" crossorigin src="', 1)

    src = _uncover_viewport(src)
    return src


# ── Service worker ───────────────────────────────────────────────────────────
# WHY: on the pilot's link index.html takes ~3.9s to arrive, and iOS drops its
# launch image the moment that download ends — handing over to a web view whose
# first paint is still 100-160ms away. Measured on the 2026-07-31 21:10 video:
# the screen is pixel-identical (frame delta 0.000) from 1.0s to 4.7s, which is
# the launch PNG; then a linear dissolve to a base fitted at #F0F0E8 (0.82/ch
# rms) over 100ms; then a one-frame snap back to olive as the web view finally
# paints. Nothing on the page can shorten that gap, because the gap exists
# before the page exists.
#
# Serving the document from a cache removes the download, so the first paint
# lands in ~100ms — long before iOS lets go — and the gap has nothing to show.
# It also takes ~3.9s off every launch, which matters more than the flash.
#
# Cache-first on the document, revalidating behind it. Network-first would be
# correct-by-construction and worth nothing here: the whole problem IS that the
# network takes four seconds, so waiting for it defeats the purpose.
#
# The staleness that buys is bounded on purpose:
#   * the cache name carries the shell stamp, so any shell edit starts a new
#     cache and drops the old one whole;
#   * caching a document ALSO pre-caches the bundle URLs it names, so a cached
#     index.html can never reference a bundle the cache lacks — the one failure
#     that would white-screen the app after a deploy;
#   * a /static/ asset that 404s means the pair went stale anyway, so the caches
#     are purged and the next launch is clean.
#
# Streamlit's transport is never touched. /_stcore carries the websocket and the
# health probe, /media and /component are per-session — all pass straight
# through, and non-GET never reaches the handler at all.
_SW_JS = """/* CommandAI service worker — __VER__ */
var SHELL = 'cai-__VER__', DOC = '/';

/* ── the launch flash, and why the document is served in two pieces ──
 *
 * Measured on four device videos (2026-07-31, 08-05, 08-06, 08-09), same
 * estimator each time: iOS cross-fades its launch image out over a web view
 * that has not painted, so what washes in is the view's own WHITE canvas.
 * Solved from 220 screen patches at once as obs = (1-a)*base + a*C, the
 * 2026-08-09 clip gives C = (251,253,237) — white, the green tint being
 * WhatsApp's chroma bleed — with a climbing 0.017 -> 0.063 -> 0.151 over three
 * frames and then a one-frame snap back to dark. Luma 18.5 -> 53.3, i.e. the
 * screen reads 2.88x brighter for 50ms. a is flat across the screen (0.135 to
 * 0.158 by fifths) and the status bar washes with it: a full-screen composite,
 * nothing in the DOM can reach it.
 *
 * THE FACT THAT PICKS THIS FIX. Between 08-06 and 08-09 the boot got twice as
 * fast — the flash moved from t=1.51s to t=0.80s — and it stayed THREE FRAMES
 * long. The gap between 'iOS starts the dissolve' and 'WebKit paints' is a
 * constant ~2-3 frames, so loading faster can never close it: it moves both
 * ends together. That is why every earlier lever failed (the 27KB diet,
 * color-scheme:dark, progressive paint, the inlined font, the static
 * theme-color, the manifest background_color, and this very worker, which cut
 * first paint 433ms -> 67ms and changed the flash by zero).
 *
 * But the dissolve EASES IN, so a roughly doubles every frame — 3 frames
 * reached 0.261 on 08-06, 2 frames 0.151 on 08-09. Each single frame (16.7ms)
 * won cuts the flash about in half. So the lever is not 'paint sooner', it is
 * 'do not let the response finish until the splash is on the glass'.
 *
 * Hence: flush everything up to and including the splash markup, hold, then
 * send the rest. The first chunk is a COMPLETE paintable screen on its own —
 * cai-micro, cai-pad, the cai-boot style with the inlined font, and the splash
 * div all live above the split — so WebKit parses and paints it during the
 * hold, and only then does the response end. Both ends of the gap move the
 * right way, which is what none of the earlier attempts did.
 *
 * HOLD_MS is deliberately generous for the first deployment. The measured gap
 * is 33-50ms and ~100ms should do, but the first video has to answer one
 * question unambiguously — does iOS time the hand-off on response end at all?
 * That is a correlation from three videos, not a proof; if the answer is no,
 * this costs 250ms and buys nothing, and the escape hatch is ?nosw=1.
 *
 * Cache hits only. On a miss the response streams off the network anyway and
 * holding would add latency for nothing.
 */
/* HOLD_MAX is a backstop, not the mechanism. The page reports its first paint
 * (see the cai-painted script in the splash markup) and that is what releases
 * the rest of the document; this only covers the case where the message never
 * arrives — an uncontrolled load, rAF never firing in a backgrounded view, a
 * shell too old to carry the reporter. It has to be comfortably longer than any
 * real paint, because expiring early is exactly the old fixed-guess failure.
 *
 * GRACE_MS covers the gap between "rAF says the frame is painted" and the
 * pixels actually being composited to the glass — a frame or two. Cheap
 * insurance on the only thing this whole exercise is trying to win. */
var HOLD_MAX = 600, GRACE_MS = 50, SPLIT = '<!--/cai-boot-splash-->';

/* Resolvers for documents currently held open, released by the paint message.
 * An array, not a single slot: a reload can overlap the previous navigation,
 * and a stranded resolver would hold that response until its backstop. */
var waitingForPaint = [];
self.addEventListener('message', function (e) {
  if (e.data !== 'cai-painted') return;
  var w = waitingForPaint;
  waitingForPaint = [];
  w.forEach(function (f) { setTimeout(f, GRACE_MS); });
});

function held(res, e) {
  if (typeof ReadableStream === 'undefined' || typeof TextEncoder === 'undefined') {
    return Promise.resolve(res);
  }
  var copy = res.clone();
  return res.text().then(function (html) {
    var i = html.indexOf(SPLIT);
    if (i < 0) return copy;                      /* unpatched shell — leave alone */
    var cut = i + SPLIT.length;
    var enc = new TextEncoder();
    var head = enc.encode(html.slice(0, cut)), tail = enc.encode(html.slice(cut));
    /* Keep the worker alive across the hold. respondWith settles as soon as the
     * headers are ready, so without this the body could be cut off by a worker
     * shutdown mid-pause. */
    var done;
    e.waitUntil(new Promise(function (r) { done = r; }));
    var body = new ReadableStream({
      start: function (ctl) {
        ctl.enqueue(head);
        var sent = false;
        var finish = function () {
          if (sent) return;
          sent = true;
          try { ctl.enqueue(tail); ctl.close(); } catch (err) {}
          done();
        };
        waitingForPaint.push(finish);
        setTimeout(finish, HOLD_MAX);
      },
      cancel: function () { done(); }
    });
    /* content-length and content-encoding MUST go. The cache stores what the
     * server sent — Streamlit gzips — and .text() has already decoded it, so
     * re-emitting plain UTF-8 under the original headers would have the engine
     * try to gunzip cleartext, and the stale length would truncate the page. */
    var h = new Headers(res.headers);
    h['delete']('content-length');
    h['delete']('content-encoding');
    return new Response(body, {status: res.status, statusText: res.statusText, headers: h});
  }).catch(function () { return copy; });
}

function bypass(u) {
  var p = u.pathname;
  return p.indexOf('/_stcore') === 0 || p.indexOf('/media') === 0
      || p.indexOf('/component') === 0 || p.indexOf('/vendor') === 0;
}
function purge() {
  return caches.keys().then(function (ks) {
    return Promise.all(ks.map(function (k) { return caches.delete(k); }));
  });
}
/* Pull the bundle out of a freshly cached document, so the two never drift. */
function precache(c, res) {
  return res.clone().text().then(function (html) {
    var re = /(?:src|href)="\\.(\\/static\\/[^"]+)"/g, urls = [], m;
    while ((m = re.exec(html))) if (urls.indexOf(m[1]) < 0) urls.push(m[1]);
    return Promise.all(urls.map(function (p) {
      return c.match(p).then(function (h) {
        return h ? null : c.add(p).catch(function () {});
      });
    }));
  }).catch(function () {});
}

self.addEventListener('install', function (e) {
  e.waitUntil(caches.open(SHELL).then(function (c) {
    return c.add(DOC).then(function () {
      return c.match(DOC).then(function (r) { return r ? precache(c, r) : null; });
    });
  }).catch(function () {}).then(function () { return self.skipWaiting(); }));
});

self.addEventListener('activate', function (e) {
  e.waitUntil(caches.keys().then(function (ks) {
    return Promise.all(ks.map(function (k) {
      return k === SHELL ? null : caches.delete(k);
    }));
  }).catch(function () {}).then(function () { return self.clients.claim(); }));
});

self.addEventListener('fetch', function (e) {
  var req = e.request;
  if (req.method !== 'GET') return;
  var u;
  try { u = new URL(req.url); } catch (err) { return; }
  if (u.origin !== self.location.origin || bypass(u)) return;

  if (req.mode === 'navigate') {
    e.respondWith(caches.open(SHELL).then(function (c) {
      return c.match(DOC).then(function (hit) {
        var net = fetch(req).then(function (res) {
          if (res && res.ok) { c.put(DOC, res.clone()); precache(c, res); }
          return res;
        });
        if (hit) { net.catch(function () {}); return held(hit, e); }
        return net;
      });
    }));
    return;
  }

  if (u.pathname.indexOf('/static/') === 0) {
    e.respondWith(caches.open(SHELL).then(function (c) {
      return c.match(req).then(function (hit) {
        if (hit) return hit;
        return fetch(req).then(function (res) {
          if (res && res.ok) c.put(req, res.clone());
          else if (res && res.status === 404) purge();
          return res;
        });
      });
    }));
  }
});
"""

# Registered on `load`, never before: the worker's install fetches the document
# again, and on a 13KB/s link that must not compete with the boot it is meant to
# speed up. The win starts at the NEXT launch either way.
#
# ?v=<stamp> is not decoration, it is the update mechanism. Streamlit's static
# handler serves everything beside index.html as `public, immutable,
# max-age=31536000` — a bundle policy, applied to our worker because it lives in
# the same directory, and we do not own that handler. Modern engines bypass the
# HTTP cache when they poll a worker script, but `immutable` is exactly the hint
# that tells one not to, and a worker that never updates pins the app to a stale
# shell permanently. Versioning the REGISTRATION URL sidesteps the question:
# a new stamp is a new script URL, which is an unconditional install no cache
# can intercept. Query strings do not affect scope, so this still controls /.
_SW_REG = """
      // isSecureContext, not a protocol test: it is true for https AND for
      // localhost, which is the only way this is testable before it ships.
      (function () {
        if (!('serviceWorker' in navigator) || !window.isSecureContext) return;
        // ESCAPE HATCH. A worker that goes wrong on a phone I cannot reach is
        // the one failure mode with no remote fix — the stale shell it serves
        // is also the shell that would carry the repair. ?nosw=1 tears the
        // whole thing out from the address bar; ?nosw=0 puts it back.
        //
        // The flag PERSISTS, and that is the point. Tearing down without it
        // buys exactly one worker-free load, because the very next launch
        // re-registers and re-breaks whatever was broken. Verified locally:
        // after unregister() the page keeps its controller until it is
        // replaced, so the old worker goes on serving — and re-creates its own
        // cache on the next caches.open — for the remainder of that load. Only
        // the following navigation is genuinely clean.
        var KILL = 'cai-nosw';
        try {
          if (location.search.indexOf('nosw=0') > -1) localStorage.removeItem(KILL);
          else if (location.search.indexOf('nosw=1') > -1) localStorage.setItem(KILL, '1');
        } catch (e) {}
        var killed = false;
        try { killed = localStorage.getItem(KILL) === '1'; } catch (e) {}
        if (killed) {
          try {
            navigator.serviceWorker.getRegistrations().then(function (rs) {
              rs.forEach(function (r) { r.unregister(); });
            });
            if (window.caches) caches.keys().then(function (ks) {
              ks.forEach(function (k) { caches.delete(k); });
            });
          } catch (e) {}
          return;
        }
        window.addEventListener('load', function () {
          setTimeout(function () {
            try { navigator.serviceWorker.register('/sw.js?v=__VER__'); } catch (e) {}
          }, 1200);
        });
      })();
"""


def _pwa_links() -> str:
    """The manifest / icon / launch-image <link>s, as STATIC markup.

    These lived only in app.py's runtime injector, and the 2026-08-10 reinstall
    exposed what that costs: iOS builds a home-screen icon from the RAW HTML it
    fetches at add-to-home-screen time — JS-injected links are invisible to it.
    A fresh install therefore had no launch image at all, and the measured
    result was 0.9s of pure black (luma 0.7 — darker than #14170E's ~20) from
    tap to first paint, on every launch. The old icon only ever had art because
    iOS *refreshes* an installed webclip from the live DOM across launches —
    which is also exactly how the mitered-chevron PNG propagated in 3 launches
    on the 2026-08-09 18:37 video. Fresh installs never got that grace, and the
    pilot will be nothing but fresh installs.

    Baked here, the links ride in the first streamed chunk with everything
    else, so the A2HS parser and the boot path see the same truth. URLs are
    content-hashed by publish_static, so a changed PNG re-stamps the shell.
    Lazy import: pwa_assets imports publish_static from this module.
    """
    try:
        import pwa_assets
        pub = pwa_assets.publish_all("/")
        if not pub:
            return ""
        links = (f'<link id="cai-manifest" rel="manifest" href="{pub["manifest"]}">'
                 f'<link id="cai-icon" rel="apple-touch-icon" href="{pub[180]}">')
        for (w, h, r, u) in pub["startup"]:
            links += ('<link rel="apple-touch-startup-image" class="cai-launch" '
                      f'media="(device-width: {w // r}px) and '
                      f'(device-height: {h // r}px) and '
                      f'(-webkit-device-pixel-ratio: {r}) and '
                      f'(orientation: portrait)" href="{u}">')
        return links
    except Exception:
        # a failed publish must never break the boot shell — the runtime
        # injector still covers installed icons, exactly as before
        return ""


# ── Lazy-chunk preloads ──────────────────────────────────────────────────────
# index.html names exactly ONE script: Streamlit's entry bundle. Every other
# chunk the first screen needs is discovered by RUNNING it — the entry asks for
# IFrame, that asks for the markdown pipeline, and so on — so the boot is four
# SERIALISED round-trips, not one download.
#
# Measured on production 2026-08-26, cold Chromium on a fast desktop line: the
# entry lands at 2.14s and the last chunk at 6.15s. Four seconds for 457KB that
# were all knowable up front. The control that makes it actionable is the same
# four waves on a WARM load, every chunk a cache hit: 0.45s. So ~3.5s of it is
# round-trips, not main-thread work — and a phone on cellular has more of them,
# not fewer.
#
# This is the one remaining lever on the FIRST-EVER launch, which is the only
# launch a store user gets to judge, and the one that overruns iOS's ~10s
# launch-image budget and shows white. It does NOT touch the ~1.7s of parsing
# the 2.36MB entry bundle, which is measurable warm and is Streamlit's, not
# ours.
#
# WHY A PINNED LIST AND NOT A SCAN. What the first screen loads is a property
# of the running app, not of the directory — static/js holds 111 chunks and
# 16MB, and preloading one the boot never asks for is bandwidth stolen from one
# it does. This list was read off the live worker's cache after a real
# production boot: it is exactly what launch 1 fetches, no more.
_PRELOAD_CHUNKS = (
    # the widget layer the first screen renders
    "IFrame", "IFrameUtil", "ComponentInstance", "withCalculatedWidth", "urls",
    "Button", "iconPosition",
    # the markdown pipeline — 447 of the 457KB, and the LAST wave to arrive,
    # i.e. the one that holds up first content
    "lib", "rehype-raw", "hastscript", "web-namespaces", "remark-emoji",
)


def _modulepreload_links() -> str:
    """The lazy chunks the first screen needs, as parallel-fetch hints.

    crossorigin is not decoration. Streamlit's entry tag is
    <script type="module" crossorigin>, so the whole module graph is fetched in
    CORS mode; a hint whose mode disagrees is not a hint for that fetch at all,
    it is a SECOND download of the same bytes. Wrong here is worse than absent.

    fetchpriority=low is the safety valve for a bandwidth-bound link — the
    pilot's measured 13KB/s. There, 457KB pulled alongside the entry would
    steal from the one download everything else waits for, and removing
    round-trips does not pay for that. Low says "start these now, never ahead
    of the bundle": the win survives, the risk does not. Safari honours it from
    17.2; before that it is ignored, which is exactly today's behaviour.

    A name that does not resolve to exactly one file is SKIPPED, not guessed.
    Streamlit re-hashes these on every frontend build, and a hint pointing at a
    chunk that is gone would 404 — which the worker reads as a stale pair and
    answers by purging every cache it has. Silence is the safe failure;
    tests/test_boot_preload.py is what makes it a loud one.
    """
    try:
        js = _index_path().parent / "static" / "js"
        links = ""
        for name in _PRELOAD_CHUNKS:
            hits = sorted(p.name for p in js.glob(name + ".*.js"))
            if len(hits) != 1:
                continue
            links += ('<link rel="modulepreload" class="cai-preload" crossorigin '
                      f'fetchpriority="low" href="./static/js/{hits[0]}">')
        return links
    except Exception:
        return ""


def patch_index_html() -> bool:
    """Inject the olive boot splash into Streamlit's static index.html.

    Idempotent: a file already carrying THIS version is left alone; an older
    one is stripped and re-injected. Returns True when the file carries (or
    already carried) the current patch, False if it could not be written
    (read-only install) or lacks the expected anchors.
    """
    try:
        index = _index_path()
        src = index.read_text(encoding="utf-8")
        face = ""
        b64 = _font_data_uri()
        if b64:
            face = ("@font-face { font-family: 'Suez One'; font-style: normal; "
                    "font-weight: 400; src: url(data:font/woff2;base64," + b64 +
                    ") format('woff2'); }")
        head_raw = _HEAD_TEMPLATE.replace("__FACE__", face)
        # the identity raster, per device pixel ratio, as data URIs — hashed
        # into the stamp with everything else, so redrawing it re-patches
        uris = __import__("pwa_assets").identity_data_uris()
        head_raw = head_raw.replace("__ID2X__", uris[2]).replace("__ID3X__", uris[3])
        # OFF BY DEFAULT, opt in with CAI_SW=1.
        #
        # The worker was built to kill the launch flash. An earlier note here
        # claimed re-adding the icon had killed it first — that was wrong, and
        # the 2026-08-05 17:37 device video (shot on a fully dark chain, after
        # the ?v= cache fix) shows why: the launch image cross-fades out over
        # three frames at ~1.7s, and solving the blend from two regions gives
        # alpha 0.92 -> 0.84 over a backdrop of (249,253,246). That backdrop is
        # the web view's own white canvas, not a colour we set anywhere, so no
        # page-side change reaches it — the gap opens before the document
        # exists. Every cheap lever was already spent between v10 and v21 (dark
        # html, color-scheme, progressive paint, inlined font, static
        # theme-color); this worker is the only remaining one, because serving
        # the shell from cache makes first paint beat the fade.
        #
        # It stays off anyway: the flash is ~50ms, and shipping an
        # unproven-on-iOS-Safari worker days before the pilot is the worse
        # trade — especially now that we know this codebase's caching
        # assumptions can bite (the immutable /static/ incident, same day).
        # Turn it on after the pilot, when the ~3.9s-a-launch win comes with it,
        # and verify cache invalidation across a deploy BEFORE trusting it.
        sw_on = os.environ.get("CAI_SW") == "1"
        # the registration rides at the end of the boot script, so it is part of
        # the stamped payload like everything else
        boot_js = (_BOOT_JS.replace("    </script>", _SW_REG + "    </script>", 1)
                   if sw_on else _BOOT_JS)
        # The PWA links join the stamped payload: their hrefs carry the assets'
        # content hashes, so editing a launch PNG re-patches the shell exactly
        # like editing the font does.
        pwa_links = _pwa_links()
        # the PNG-table anchor script (see _PAD_JS_TEMPLATE) — rendered here
        # so its table is hashed into the stamp with everything else
        pad_js = _pad_js()
        # hashed with everything else: a Streamlit upgrade re-hashes the
        # chunk filenames, which re-stamps the shell, which drops the old
        # worker cache — the hints and the cache can never disagree.
        preloads = _modulepreload_links()
        # stamped with a hash of exactly what is about to be written — including
        # the font bytes — so a swapped font file re-patches too
        stamp = _VERSION + "-" + hashlib.sha256(
            (_MICRO + pad_js + head_raw + pwa_links + _SPLASH_HTML
             + preloads + boot_js
             + (_SW_JS if sw_on else "")
             + "".join(_STATIC_VIEWPORT_TOKENS)).encode("utf-8")
        ).hexdigest()[:8]
        # The worker carries the stamp as its cache name, so publishing it here
        # — before the early return — is what makes a shell edit drop the old
        # cache. Unconditional while enabled: the index may already be current
        # while the worker file is missing (fresh container, read-only rebuild).
        if sw_on:
            publish_root("sw.js", _SW_JS.replace("__VER__", stamp).encode("utf-8"))
        # __VER__ is stamped in AFTER hashing, like head_raw's: the hash covers
        # the placeholder, so it stays a fixed point instead of chasing itself.
        boot_js = boot_js.replace("__VER__", stamp)
        if f'data-cai-ver="{stamp}"' in src:
            return True
        src = _strip(src)
        if ("</head>" not in src or "<body>" not in src
                or '<meta charset="UTF-8" />' not in src
                or '<div id="root"></div>' not in src):
            return False
        head = head_raw.replace("__VER__", stamp)
        # Byte order IS the design (see _MICRO): micro-style right after the
        # charset meta so the first possible paint is olive; the big style
        # block still at the END of <head> — hoisting its ~9KB of base64 font
        # above <meta charset> would push the charset past the 1024-byte scan
        # window and Tornado does not always send a charset header, so the
        # Hebrew could mojibake; splash markup at the TOP of <body> so the
        # complete splash is paintable at ~15KB into the stream; the wiring
        # script at the END of <body> where the DOM it touches exists.
        patched = _cover_viewport(src)
        # (v40 put Streamlit's module bundle behind `load` so iOS would drop the
        # launch screen sooner. It did not — the launch screen tracks the app
        # process, not the page — and the 17:07 device video brought the
        # one-frame handover brightening BACK (+1..+4 in 4 of 6 launches, after
        # 0.0 in 5 of 5 on v39). Reverted in v41; the bundle is parser-inserted
        # again.)
        patched = patched.replace('<meta charset="UTF-8" />',
                                  '<meta charset="UTF-8" />' + _MICRO + pad_js, 1)
        patched = patched.replace("</head>", head + pwa_links + "  </head>", 1)
        patched = _deblock_css(patched)
        # _SPLASH_HTML ends with the split marker, so appending here puts the
        # hints in the FIRST BYTES OF THE TAIL: the splash is already on the
        # glass, the entry <script> up in <head> has had the whole hold as a
        # head start, and the first flushed chunk has not grown by a byte.
        patched = patched.replace("<body>",
                                  "<body>" + _SPLASH_HTML + preloads, 1)
        # regex, not a string replace: </body> carries the host file's own
        # indentation, and gluing our block to a bare "</body>" left the old
        # indent orphaned before the script — off-by-two-spaces per cycle,
        # caught by the byte-exact round-trip test
        patched = re.sub(r"([ \t]*)</body>",
                         lambda m: boot_js + m.group(0), patched, count=1)
        # id="cai-pad" is back (2026-09-04, the PNG-table anchor); viewport-fit
        # =cover joined on 2026-09-01 — a viewport regex that silently missed
        # the meta would otherwise ship a shell whose whole point is missing.
        for marker in ('id="cai-micro"', 'id="cai-pad"', 'id="cai-boot"',
                       'id="cai-sbstyle"', 'id="cai-capable"',
                       'id="cai-boot-splash"', 'id="cai-boot-js"',
                       "maximum-scale=1", "viewport-fit=cover",
                       "var(--cai-pad"):
            if marker not in patched:
                return False
        # only when the assets actually published — a read-only venv where
        # publish fails still gets a valid (linkless) shell
        if pwa_links and patched.count('class="cai-launch"') != len(
                __import__("pwa_assets")._STARTUP_SIZES):
            return False
        # Every hint must land BELOW the split. Everything above it rides in
        # the worker's first flushed chunk, and that chunk being a small,
        # complete, paintable screen IS the launch-flash argument — a hint that
        # drifts into <head> trades the fix for the optimisation.
        if preloads:
            if patched.count('class="cai-preload"') != preloads.count("<link"):
                return False
            if (patched.index("<!--/cai-boot-splash-->")
                    > patched.index('class="cai-preload"')):
                return False
        # (the July "assert cover never creeps back in" guard lived here until
        # 2026-09-01 — cover is now shipped DELIBERATELY, and the marker loop
        # above asserts its presence instead)
        index.write_text(patched, encoding="utf-8")
        return True
    except Exception:
        return False


if __name__ == "__main__":
    print("boot-shell branded:", patch_index_html())
