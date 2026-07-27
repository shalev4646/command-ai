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
import inspect
import re
from pathlib import Path

import streamlit as st

# Bump when the injected markup changes. A patch carrying an older version is
# STRIPPED and re-injected rather than nursed along with targeted swaps: a
# long-lived dev venv keeps its patched index.html forever, and silently
# testing last week's boot shell is worse than the cost of a rewrite.
_VERSION = "v5"


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
    """
    try:
        p = Path(__file__).parent / "branding" / "fonts" / "SuezOne-Regular.ttf"
        return base64.b64encode(p.read_bytes()).decode("ascii")
    except Exception:
        return ""


# __FACE__ is substituted at patch time. A plain placeholder, not an f-string:
# this block is nearly all CSS braces and escaping them all would bury it.
_HEAD_TEMPLATE = """
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
      /* The OS launch image shows the chevron and the wordmark and NOTHING
         else. Whatever this shell paints on top of them at t=0 pops into
         existence during iOS's crossfade from that image. A delayed slide-up
         entrance was tried first — the pilot read THAT as another screen
         switch (2026-07-27 video #2). So: pure opacity, starting at once —
         the subtitle emerges inside the OS's own ~300ms crossfade window and
         nothing on the splash ever MOVES. */
      #cai-boot-splash .s { font: 600 11px ui-monospace, Menlo, monospace; letter-spacing: 3px;
        color: rgba(23,26,18,.6);
        animation: caiBootFade .8s ease both; }
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
      #cai-boot-splash .w { width: 22px; height: 22px; margin: auto auto 14vh;
        border: 2px solid rgba(23,26,18,.20); border-top-color: rgba(23,26,18,.55);
        border-radius: 50%;
        animation: caiBootSpin .9s linear infinite, caiBootFade .5s ease both;
        animation-delay: 0s, 2.5s; }
      [data-testid="stSkeleton"], [data-testid="stAppSkeleton"],
      [data-testid="stStatusWidget"], [data-testid="stDecoration"] { display: none !important; }
    </style>
"""

_BODY_ADD = """
    <div id="cai-boot-splash" dir="rtl">
      <div class="chev"><span></span><span></span></div>
      <div class="t">CommandAI</div>
      <div class="s">מערכת פקודות · בלמ"ס</div>
      <div class="w"></div>
    </div>
    <script id="cai-boot-js">
      (function () {
        var el = document.getElementById('cai-boot-splash');
        if (!el) return;
        var gone = false;
        // Curtain, not a fade. This shell is now the ONE loading screen — the
        // app no longer draws a second, near-identical splash underneath it
        // (see splash_active in app.py), so there is nothing to cross-fade to
        // and the reveal can be the real thing: the screen slides up off the
        // glass and the app is simply there behind it.
        var lift = function () {
          if (gone) return; gone = true;
          el.style.transition = 'transform .6s cubic-bezier(.7,0,.3,1), opacity .6s ease';
          el.style.transform = 'translateY(-101%)';
          el.style.opacity = '0';
          setTimeout(function () { el.remove(); }, 680);
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


def _strip(src: str) -> str:
    """Remove any previously injected boot shell, of any version.

    Anchored on ids that only ever appear in our own block, so this cannot
    touch Streamlit's markup. Covers the v1 shape too — its render-blocking
    <link id="cai-boot-font"> is exactly what v2 exists to delete.
    """
    src = re.sub(r'\s*<link id="cai-boot-font"[^>]*>', "", src)
    src = re.sub(r'\s*<style id="cai-boot".*?</style>', "", src, flags=re.S)
    src = re.sub(r'\s*<div id="cai-boot-splash".*?</script>', "", src, flags=re.S)
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
        if f'data-cai-ver="{_VERSION}"' in src:
            return True
        src = _strip(src)
        if "</head>" not in src or '<div id="root"></div>' not in src:
            return False
        face = ""
        b64 = _font_data_uri()
        if b64:
            face = ("@font-face { font-family: 'Suez One'; font-style: normal; "
                    "font-weight: 400; src: url(data:font/ttf;base64," + b64 +
                    ") format('truetype'); }")
        head = _HEAD_TEMPLATE.replace("__VER__", _VERSION).replace("__FACE__", face)
        patched = src.replace("</head>", head + "  </head>", 1)
        patched = patched.replace('<div id="root"></div>',
                                  '<div id="root"></div>' + _BODY_ADD, 1)
        index.write_text(patched, encoding="utf-8")
        return True
    except Exception:
        return False


if __name__ == "__main__":
    print("boot-shell branded:", patch_index_html())
