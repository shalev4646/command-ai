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
_VERSION = "v14"


# Streamlit ships `width=device-width, initial-scale=1, shrink-to-fit=no` — no
# viewport-fit. On iOS standalone that means the web view is INSET into the safe
# area, so env(safe-area-inset-*) all resolve to 0 and 100vh is the inset height,
# not the screen height. app.py has been patching `, viewport-fit=cover` onto
# this meta AT RUNTIME since the PWA work — from a Streamlit component that
# mounts long after first paint — and that single line is the source of the
# "screen jumps up then comes back down" the pilot filmed three rounds running:
#
#   * launch image: drawn at sat + 0.14 * SCREEN height (full screen, from
#     _STARTUP_SAT) => chevron ink at video row 171
#   * shell, pre-mutation: env()=0, vh = inset height => same chevron at row
#     164 — the content JUMPS UP ~8px the instant the shell takes over
#   * shell, post-mutation: cover applies, the web view grows to full screen,
#     env(top) becomes real, vh becomes the screen height => the content drops
#     back DOWN to where the launch image had it
#
# Applying it here, statically, collapses all three into one geometry: the shell
# and the launch image compute the identical offset from byte zero, there is no
# mid-boot web-view resize, and the 18 env(safe-area-inset-*) rules in app.py
# finally mean what they were written to mean.
_VIEWPORT_RE = re.compile(
    r'(<meta[^>]*\bname="viewport"[^>]*\bcontent=")(?P<val>[^"]*)(")'
)


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
# the other way round — by _PAD_JS below, which changes only the splash.
# maximum-scale=1 stays: it caps zoom, never geometry, and shipping it here
# spares one more runtime write to this meta during boot.
_STATIC_VIEWPORT_TOKENS = (", maximum-scale=1",)

# The alignment fix, replacing viewport-fit=cover. In an iOS standalone web app
# WITHOUT cover, the web view starts exactly at the top safe-area inset and
# env(safe-area-inset-top) reads 0 — so the splash's `env(top) + 14vh` lands at
# `sat + 0.14 * (screen - sat)` on screen, while _startup_png draws at
# `sat + 0.14 * screen`. That difference is the ~8px jump, and it is 0.14*sat:
# 8.26px predicted for the pilot's phone, 7.7-8.2 measured.
#
# screen.height is the FULL screen height in CSS px in either mode, so
# `0.14 * screen.height` as the web-view-relative padding puts the content at
# `sat + 0.14 * screen` — the launch image's formula exactly, with no device
# table and no magic numbers. Gated on navigator.standalone, which is true in
# precisely the case that has a launch image at all; everywhere else the CSS
# fallback keeps today's behaviour. Synchronous and in <head>, so it lands
# before first layout and cannot itself cause a reflow.
_PAD_JS = ('<script id="cai-pad">try{if(navigator.standalone){'
           'document.documentElement.style.setProperty('
           '"--cai-pad",(0.14*screen.height)+"px")}}catch(e){}</script>')

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
# permanently stuck dev venv. Retired tokens go here, they never come out.
_LEGACY_VIEWPORT_TOKENS = (", viewport-fit=cover",)


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
_MICRO = ('<style id="cai-micro">:root{color-scheme:dark}'
          'html,body{background:#99A26B;margin:0}</style>')

# __FACE__ is substituted at patch time. A plain placeholder, not an f-string:
# this block is nearly all CSS braces and escaping them all would bury it.
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
    <style id="cai-boot" data-cai-ver="__VER__">
      __FACE__
      html, body { background: #99A26B; }
      #cai-boot-splash { position: fixed; inset: 0; z-index: 2147483000; background: #99A26B;
        display: flex; flex-direction: column; align-items: center; justify-content: flex-start;
        padding-top: var(--cai-pad, calc(env(safe-area-inset-top, 0px) + 14vh));
        gap: 18px; transition: opacity .4s ease; pointer-events: none; }
      #cai-boot-splash .chev span { display: block; width: 26px; height: 26px;
        border-top: 6px solid #171A12; border-left: 6px solid #171A12; transform: rotate(45deg); }
      #cai-boot-splash .chev span + span { border-color: rgba(23,26,18,.45); margin-top: -9px; }
      #cai-boot-splash .t { font: 400 34px 'Suez One', serif; color: #171A12; }
      /* SUEZ ONE, NOT ui-monospace — and this is load-bearing, not taste.
         The launch image now paints this line too (see _startup_png), so the
         two must agree to the pixel or the hand-off shows the subtitle
         swapping typefaces. Menlo and friends carry NO Hebrew: iOS silently
         fell back to its system Hebrew face, which Pillow cannot reproduce
         server-side, so identity was unreachable while this said monospace.
         Suez One is already inlined above as a data URI — same file the PNG
         draws with, zero extra bytes on the critical path.

         The numbers follow from that swap. Tracking 4.8px (was 3px) keeps the
         line at the ~192px width the layout was composed around, since Suez
         One is the narrower face. Alpha .4 (was .6) because its stroke is
         markedly heavier at this size — .6 read as bold next to the old line.
         The .4 is bisected, not eyeballed: it reproduces the ink energy of the
         real subtitle in the 2026-07-28 device video to within 1%. It must stay
         in step with app._SUB_ALPHA (round(255 * .4) = 102).

         NO entrance animation. The subtitle is already on screen, painted
         into the launch image, before this stylesheet exists; fading it in
         makes it blink out and back at the exact moment the hand-off has to
         be invisible. (The .8s fade this replaces was itself the fix for an
         earlier slide-up that the pilot read as a screen switch — 2026-07-27
         video #2. Neither is needed once the two screens are identical.)

         Layout D, the user's pick from four rendered candidates (2026-07-28
         evening): "מערכת פקודות" flanked by thin rules, "בלמ״ס" spaced out
         beneath. The rules are ::before/::after on .s1 — flex children, so
         they stay symmetric in RTL and the trailing-letter-spacing quirk
         (see app._draw_subtitle) affects only the TEXT ink inside the row,
         exactly as the PNG reproduces it. */
      #cai-boot-splash .s { display: flex; flex-direction: column; align-items: center;
        gap: 6px; }
      #cai-boot-splash .s1 { display: flex; align-items: center; gap: 12px;
        font: 400 13px 'Suez One', serif; letter-spacing: 2px;
        color: rgba(23,26,18,.59); }
      #cai-boot-splash .s1::before, #cai-boot-splash .s1::after {
        content: ''; width: 26px; height: 1px; background: rgba(23,26,18,.31); }
      #cai-boot-splash .s2 { font: 400 9.5px 'Suez One', serif; letter-spacing: 7px;
        color: rgba(23,26,18,.37); }
      /* NO lift choreography. A staggered per-element entrance was tried
         (2026-07-27, shell v4) and it FOUGHT Streamlit: reruns replace the
         DOM mid-cascade, so the curtain lifted onto a dark screen of
         opacity-0 elements and the composer popped in ~1.5s late (video #3,
         "פותח ומעלים את השאלה"). The Claude-app smoothness comes from the
         opposite move — the curtain waits until the screen is COMPLETE and
         geometrically settled (see ready()/stability in the script below),
         then lifts once over a finished static page. */
      /* Bottom-anchored waiting ring, matching .cai-splash-wait in app.py so the
         hand-off does not move it. Fades in at 2.5s: a fast load never shows it,
         a slow one stops looking frozen. This is the ONLY moving thing on screen
         during the wait — the OS launch image before it cannot animate at all. */
      @keyframes caiBootSpin { to { transform: rotate(360deg); } }
      @keyframes caiBootFade { from { opacity: 0; } to { opacity: 1; } }
      /* The wait stack is bottom-anchored and GROWS UPWARD: the ring is its
         last child, so its distance from the bottom (14vh) is identical
         whether or not the long-wait copy above it is showing. The ring
         must not move — a splash element that shifts position mid-wait is
         exactly the "it keeps switching screens" the pilot reported. */
      #cai-boot-splash .wait { margin: auto auto 14vh; display: flex;
        flex-direction: column; align-items: center; gap: 13px; }
      #cai-boot-splash .w { width: 22px; height: 22px; margin: 0;
        border: 2px solid rgba(23,26,18,.20); border-top-color: rgba(23,26,18,.55);
        border-radius: 50%;
        animation: caiBootSpin .9s linear infinite, caiBootFade .5s ease both;
        animation-delay: 0s, 2.5s; }
      /* SAY SOMETHING when the boot drags. 2026-07-28 device video: 51s of
         splash with a silent spinner (index.html alone took 13.7s to land)
         — indistinguishable from a hang, and the pilot had no way to tell
         whether to keep waiting or force-quit. Hidden until armed, so a
         normal load never sees it and the geometry is untouched. */
      #cai-boot-splash .m { display: none; max-width: 78vw; text-align: center;
        font: 600 12px ui-monospace, Menlo, monospace; line-height: 1.7;
        color: rgba(23,26,18,.62); opacity: 0; transition: opacity .5s ease; }
      #cai-boot-splash .m.on { opacity: 1; }
      #cai-boot-splash .r { display: none; pointer-events: auto;
        font: 700 12px ui-monospace, Menlo, monospace; color: #171A12;
        background: rgba(23,26,18,.10); border: 1px solid rgba(23,26,18,.30);
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
      <div class="chev"><span></span><span></span></div>
      <div class="t">CommandAI</div>
      <div class="s"><span class="s1">מערכת פקודות</span><span class="s2">בלמ"ס</span></div>
      <div class="wait">
        <div class="m"></div>
        <button class="r" type="button">נסה שוב</button>
        <div class="w"></div>
      </div>
    </div><!--/cai-boot-splash-->
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
          var show = function () {
            // never over the curtain — the boot splash speaks for itself
            if (document.getElementById('cai-boot-splash')) {
              setTimeout(show, 1000); return;
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
            if (live <= 0) bar.classList.add('on');
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
        var lift = function () {
          if (gone) return; gone = true;
          slow.forEach(clearTimeout);
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
          // SLIDE ONLY — never fade while sliding: a curtain whose opacity
          // drops mid-motion is see-through, and the app showed THROUGH the
          // moving splash as a smeared double-exposure (two crossing
          // wordmarks, 2026-07-27 video #5). A dim-before-slide variant was
          // also tried (v10) and the user asked for THIS animation back by
          // name (2026-07-29) — the single opaque slide is the keeper.
          el.style.transition = 'transform .55s cubic-bezier(.7,0,.3,1)';
          el.style.transform = 'translateY(-102%)';
          setTimeout(function () { el.remove(); }, 620);
        };
        // Wait for a COMPLETE screen, not for any markdown: the app emits its
        // CSS as a markdown element long before it renders anything a person
        // can read, and lifting early exposes a half-painted app (the
        // "Missing Submit Button" frame, video #1). On the chat home the
        // composer must exist too — a reveal without the question bar reads
        // as broken (video #3). The anchor element doubles as the stability
        // probe below.
        var ready = function () {
          var scr = document.querySelector('.cai-entry, .st-key-cai_name_card, .cai-splash');
          if (scr) return scr;
          var chat = document.querySelector('.cai-greet, .cai-header');
          if (!chat) return null;
          return document.querySelector('[data-testid="stChatInput"]') ? chat : null;
        };
        // Lift only once the layout is SETTLED: the anchor's position must
        // hold still for 3 consecutive samples (~450ms) — a Streamlit rerun
        // replacing the DOM mid-boot resets the count, so the curtain never
        // rises over a page that is still being rebuilt (the dark-flash +
        // popping-in reveal of video #3).
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
          if (stable >= 3) { clearInterval(tick); setTimeout(lift, 150); }
        }, 150);
        setTimeout(function () { clearInterval(tick); lift(); }, 90000);
      })();
    </script>
"""


def _index_path() -> Path:
    return Path(inspect.getfile(st)).parent / "static" / "index.html"


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
    """
    try:
        d = _index_path().parent / "static" / "cai"
        d.mkdir(parents=True, exist_ok=True)
        p = d / name
        if not p.exists() or p.read_bytes() != data:
            p.write_bytes(data)
        return "/static/cai/" + name
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
    src = _uncover_viewport(src)
    return src


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
        # stamped with a hash of exactly what is about to be written — including
        # the font bytes — so a swapped font file re-patches too
        stamp = _VERSION + "-" + hashlib.sha256(
            (_MICRO + _PAD_JS + head_raw + _SPLASH_HTML + _BOOT_JS
             + "".join(_STATIC_VIEWPORT_TOKENS)).encode("utf-8")
        ).hexdigest()[:8]
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
        patched = patched.replace('<meta charset="UTF-8" />',
                                  '<meta charset="UTF-8" />' + _MICRO + _PAD_JS, 1)
        patched = patched.replace("</head>", head + "  </head>", 1)
        patched = _deblock_css(patched)
        patched = patched.replace("<body>", "<body>" + _SPLASH_HTML, 1)
        # regex, not a string replace: </body> carries the host file's own
        # indentation, and gluing our block to a bare "</body>" left the old
        # indent orphaned before the script — off-by-two-spaces per cycle,
        # caught by the byte-exact round-trip test
        patched = re.sub(r"([ \t]*)</body>",
                         lambda m: _BOOT_JS + m.group(0), patched, count=1)
        for marker in ('id="cai-micro"', 'id="cai-pad"', 'id="cai-boot"',
                       'id="cai-boot-splash"', 'id="cai-boot-js"',
                       "maximum-scale=1", "var(--cai-pad"):
            if marker not in patched:
                return False
        # cover would resize the app, not just the splash — see the comment on
        # _STATIC_VIEWPORT_TOKENS. Assert it never creeps back in.
        if "viewport-fit" in patched:
            return False
        index.write_text(patched, encoding="utf-8")
        return True
    except Exception:
        return False


if __name__ == "__main__":
    print("boot-shell branded:", patch_index_html())
