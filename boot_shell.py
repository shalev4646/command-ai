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
_VERSION = "v9"


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
    <style id="cai-boot" data-cai-ver="__VER__">
      __FACE__
      html, body { background: #99A26B; }
      #cai-boot-splash { position: fixed; inset: 0; z-index: 2147483000; background: #99A26B;
        display: flex; flex-direction: column; align-items: center; justify-content: flex-start;
        padding-top: calc(env(safe-area-inset-top, 0px) + 14vh);
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
         video #2. Neither is needed once the two screens are identical.) */
      #cai-boot-splash .s { font: 400 11px 'Suez One', serif; letter-spacing: 4.8px;
        color: rgba(23,26,18,.4); }
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
    </style>
"""

_BODY_ADD = """
    <div id="cai-boot-splash" dir="rtl">
      <div class="chev"><span></span><span></span></div>
      <div class="t">CommandAI</div>
      <div class="s">מערכת פקודות · בלמ"ס</div>
      <div class="wait">
        <div class="m"></div>
        <button class="r" type="button">נסה שוב</button>
        <div class="w"></div>
      </div>
    </div>
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
          // SLIDE ONLY — never fade while sliding: a curtain whose opacity
          // drops mid-motion is see-through, and the app showed THROUGH the
          // moving splash as a smeared double-exposure (two crossing
          // wordmarks, 2026-07-27 video #5, t≈11s). The curtain stays fully
          // opaque and simply leaves the glass.
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
    """
    src = re.sub(r'\s*<link id="cai-boot-font"[^>]*>', "", src)
    src = re.sub(r'\s*<meta id="cai-theme"[^>]*>', "", src)
    src = re.sub(r'\s*<style id="cai-boot".*?</style>', "", src, flags=re.S)
    # the trailing \n? matters: _BODY_ADD ends in a newline of its own, so
    # without it every strip+repatch cycle leaves one more blank line before
    # </body> and the round-trip stops being byte-exact
    src = re.sub(r'\s*<div id="cai-boot-splash".*?</script>\n?', "", src, flags=re.S)
    # restore Streamlit's stylesheet link from the <noscript> copy, so a
    # re-patch starts from pristine markup instead of stacking swaps
    src = _CSS_SWAP_RE.sub(lambda m: m.group("orig"), src)
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
            (head_raw + _BODY_ADD).encode("utf-8")).hexdigest()[:8]
        if f'data-cai-ver="{stamp}"' in src:
            return True
        src = _strip(src)
        if "</head>" not in src or '<div id="root"></div>' not in src:
            return False
        head = head_raw.replace("__VER__", stamp)
        # The style block stays at the END of <head>, on purpose. Hoisting it to
        # the top would push Streamlit's <meta charset="UTF-8"> past the 1024
        # bytes browsers scan for it — this block carries ~92KB of base64 font —
        # and Tornado's static handler does not always send a charset header, so
        # the Hebrew could mojibake. Position buys nothing now that _deblock_css
        # has removed the only thing in <head> that was holding up the paint.
        patched = src.replace("</head>", head + "  </head>", 1)
        patched = _deblock_css(patched)
        patched = patched.replace('<div id="root"></div>',
                                  '<div id="root"></div>' + _BODY_ADD, 1)
        index.write_text(patched, encoding="utf-8")
        return True
    except Exception:
        return False


if __name__ == "__main__":
    print("boot-shell branded:", patch_index_html())
