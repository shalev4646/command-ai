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
import inspect
from pathlib import Path

import streamlit as st

# The one splash rule that changes between versions. When an OLD on-disk patch
# is found we swap just this rule (the anchor is unique to our injected block,
# so the replace can't touch anything else) — that lets a layout tweak land
# without a full reinstall. No-op once the file already carries the fresh rule.
_STALE_RULE = ("align-items: center; justify-content: center;\n"
               "        gap: 18px; transition: opacity .4s ease")
_FRESH_RULE = ("align-items: center; justify-content: flex-start;\n"
               "        padding-top: calc(env(safe-area-inset-top, 0px) + 14vh);\n"
               "        gap: 18px; transition: opacity .4s ease")

_HEAD_ADD = """
    <link id="cai-boot-font" rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Suez+One&display=swap">
    <style id="cai-boot">
      html, body { background: #99A26B; }
      #cai-boot-splash { position: fixed; inset: 0; z-index: 2147483000; background: #99A26B;
        display: flex; flex-direction: column; align-items: center; justify-content: flex-start;
        padding-top: calc(env(safe-area-inset-top, 0px) + 14vh);
        gap: 18px; transition: opacity .4s ease; pointer-events: none; }
      #cai-boot-splash .chev span { display: block; width: 26px; height: 26px;
        border-top: 6px solid #171A12; border-left: 6px solid #171A12; transform: rotate(45deg); }
      #cai-boot-splash .chev span + span { border-color: rgba(23,26,18,.45); margin-top: -9px; }
      #cai-boot-splash .t { font: 400 34px 'Suez One', serif; color: #171A12; }
      #cai-boot-splash .s { font: 600 11px ui-monospace, Menlo, monospace; letter-spacing: 3px;
        color: rgba(23,26,18,.6); }
      [data-testid="stSkeleton"], [data-testid="stAppSkeleton"],
      [data-testid="stStatusWidget"], [data-testid="stDecoration"] { display: none !important; }
    </style>
"""

_BODY_ADD = """
    <div id="cai-boot-splash" dir="rtl">
      <div class="chev"><span></span><span></span></div>
      <div class="t">CommandAI</div>
      <div class="s">מערכת פקודות · בלמ"ס</div>
    </div>
    <script id="cai-boot-js">
      (function () {
        var el = document.getElementById('cai-boot-splash');
        if (!el) return;
        var gone = false;
        var lift = function () {
          if (gone) return; gone = true;
          el.style.opacity = '0';
          setTimeout(function () { el.remove(); }, 450);
        };
        // first real content: the app's own boot splash (identical olive, so
        // the hand-off is invisible) or any rendered markdown (admin view)
        var ready = function () {
          return document.querySelector('.cai-splash, [data-testid="stAppViewContainer"] .stMarkdown');
        };
        var tick = setInterval(function () { if (ready()) { clearInterval(tick); lift(); } }, 120);
        setTimeout(function () { clearInterval(tick); lift(); }, 90000);
      })();
    </script>
"""


def _index_path() -> Path:
    return Path(inspect.getfile(st)).parent / "static" / "index.html"


def patch_index_html() -> bool:
    """Inject the olive boot splash into Streamlit's static index.html.

    Idempotent by the `id="cai-boot"` marker. Returns True when the file
    carries (or already carried) the patch, False if it could not be written
    (read-only install) or lacks the expected anchors.
    """
    try:
        index = _index_path()
        src = index.read_text(encoding="utf-8")
        if 'id="cai-boot"' in src:
            # Already patched — refresh only the version-specific splash rule.
            if _STALE_RULE in src:
                try:
                    index.write_text(src.replace(_STALE_RULE, _FRESH_RULE, 1), encoding="utf-8")
                except Exception:
                    pass
            return True
        if "</head>" not in src:
            return False
        patched = src.replace("</head>", _HEAD_ADD + "  </head>", 1)
        patched = patched.replace('<div id="root"></div>', '<div id="root"></div>' + _BODY_ADD, 1)
        index.write_text(patched, encoding="utf-8")
        return True
    except Exception:
        return False


if __name__ == "__main__":
    print("boot-shell branded:", patch_index_html())
