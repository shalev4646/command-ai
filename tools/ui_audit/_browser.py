"""The one browser launcher the ui_audit scripts share.

CAI_AUDIT_BROWSER picks the engine: a path to a Chromium-family executable,
or a Playwright channel name (msedge, chrome). The default is Edge on Windows —
the dev machine has it and no downloaded Playwright browsers — and Playwright's
bundled Chromium elsewhere.
"""
import os
import sys

VIEWPORT = {"width": 390, "height": 844}


def launch(p):
    choice = os.environ.get("CAI_AUDIT_BROWSER", "")
    if choice and os.path.exists(choice):
        return p.chromium.launch(executable_path=choice, args=["--no-sandbox"])
    channel = choice or ("msedge" if sys.platform == "win32" else "")
    if channel:
        return p.chromium.launch(channel=channel)
    return p.chromium.launch(args=["--no-sandbox"])

# The installed app is a different CSS world from a browser tab: html.cai-standalone
# pins the pane to a measured height, floats the composer strip, and reserves its
# room on the scroller itself. The app turns that layer on from window.__caiSA
# (navigator.standalone, or the display-mode media query) -- which CDP cannot
# emulate -- so the flag is set before the app's own script runs, and matchMedia is
# taught to agree for the one query the app asks.
#
# An audit in a plain tab measures an app the users are not in. 2026-09-19: the
# welcome screen carried 194px of drag in the installed app against 64px in a tab,
# because html.cai-standalone .stMain reserves --cai-sbh (the composer, fallback
# 134px) and the variable survives on <html> from the chat screen. The tab-world
# sweep called that screen clean.
STANDALONE_INIT = """
    window.__caiSA = true;
    var mm = window.matchMedia.bind(window);
    window.matchMedia = function (q) {
        if (String(q).indexOf('display-mode: standalone') !== -1)
            return {matches: true, media: q, onchange: null,
                    addListener: function () {}, removeListener: function () {},
                    addEventListener: function () {}, removeEventListener: function () {},
                    dispatchEvent: function () { return false; }};
        return mm(q);
    };
"""


def phone_context(br, standalone=False, **kw):
    """A context shaped like the phone: touch, mobile UA, and -- with
    standalone=True -- the installed app's CSS layer."""
    ctx = br.new_context(viewport=VIEWPORT, locale="he-IL",
                         is_mobile=True, has_touch=True, **kw)
    if standalone:
        ctx.add_init_script(STANDALONE_INIT)
    return ctx
