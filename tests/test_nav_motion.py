# -*- coding: utf-8 -*-
"""Navigation motion: what a finger sees between one screen and the next.

Run: venv\\Scripts\\python.exe tests\\test_nav_motion.py

Every assertion here comes from a device video the user recorded on
2026-09-18 and from a measurement that reproduced what it showed -- not from
taste. The four defects, in the order they appear in that video:

  0:24  the welcome screen could be dragged into 112px of empty padding it
        kept for a composer that is not on that screen ("scroll to the
        bottom and it smears")
  0:43  moving the drawer read as a page refresh: the wordmark took up to
        320ms to come back (.18s transition behind a .14s delay)
  1:40  a tool opened in stages -- drawer gone, chat exposed, an empty
        dialog card, content a beat later ("takes time and comes up
        strangely")
  2:25  a back-swipe dragged the settings sheet 77% off the screen, over the
        near-black scrim, then sprang it BACK to rest and swapped the screen
        underneath ("it exits badly" / "it still does not come back right")

These are string checks over app.py, like the design invariants: the values
live in three places (a Python expression, a CSS block and a JS engine) and
nothing else fails when one of them drifts. The browser measurements that
justify each number are in the comments beside them in app.py.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = (ROOT / "app.py").read_text(encoding="utf-8")


# -- 0:24 the entry screens have nothing to drag into -------------------------

def test_entry_screens_do_not_carry_the_composers_padding():
    """7rem of bottom padding is the chat composer's room. On the welcome
    screen there is no composer, so it was 112px of empty page under the
    footer -- 156px of scrollable screen, measured, of which 112 was that
    padding."""
    assert 'MAIN_BOTTOM_PADDING = "16px" if _entry_like else "7rem"' in APP
    assert "{MAIN_BOTTOM_PADDING}" in APP, "the token is computed but not used"


def test_the_installed_app_does_not_reserve_the_composer_on_entry():
    """The same hole one layer down, and the one the phone falls into. The
    installed app (html.cai-standalone) reserves --cai-sbh -- the measured
    composer strip, fallback 134px -- on the SCROLLER itself. The entry screens
    have no composer, and the variable lives on <html>, so it survives from the
    chat screen.

    This is why the first fix measured clean and the phone still smeared: a
    browser tab never has that rule. Measured 2026-09-19 with the standalone
    layer forced on: stMain scrollHeight 1038 in an 844 pane (194px to drag
    into) against 908 (64px) in a tab; 64px in both after."""
    assert 'MAIN_SB_PADDING = "0px" if _entry_like else "var(--cai-sbh, 134px)"' in APP
    assert "padding-bottom: {MAIN_SB_PADDING}" in APP, (
        "the standalone scroller still hard-codes the composer's room"
    )


def test_entry_screens_do_not_bounce():
    """iOS rubber-band drags the column away from the page's fixed gradient
    underlay and snaps it back -- the smear. The chat keeps its bounce: there
    the give at the end of a long answer is the scroll telling you it ended."""
    assert 'MAIN_OVERSCROLL = "none" if _entry_like else "auto"' in APP
    assert "overscroll-behavior-y: {MAIN_OVERSCROLL}" in APP


# -- 0:43 the header hands over without a gap ---------------------------------

def test_the_wordmark_comes_back_with_the_drawer_not_after_it():
    """It is hidden while the drawer is over it and restored when the drawer
    lets go. A delay on that transition is dead air on the header -- .14s of
    delay plus .18s of fade measured 146ms before the wordmark reappeared,
    and read as a page refresh. No delay, and it is back in 10ms."""
    m = re.search(r"\.cai-header > \* \{ transition: opacity ([\d.]+)s ease ([\d.]+)s;", APP)
    assert m, ".cai-header transition not found"
    assert float(m.group(2)) == 0, f"the wordmark waits {m.group(2)}s before returning"
    assert float(m.group(1)) <= 0.14, f"and then fades for {m.group(1)}s"


# -- 1:40 a tool is not assembled in public -----------------------------------

def test_a_tool_tap_is_covered_until_the_dialog_has_content():
    """The veil is the app's language for a server round trip (the role tap
    uses it). A dialog that exists is not a dialog that is ready: Streamlit
    mounts the card first and fills it a beat later, which is the "comes up
    strangely". Measured: cover up at 291ms, content at 729ms, cover gone at
    1192ms."""
    i = APP.find('.st-key-cai_tools button")) {')
    assert i > 0, "no veil on the tool tap"
    block = APP[i:i + 400]
    assert "veil(function" in block, "the tool tap must raise the cover"
    assert 'data-testid="stDialog"' in block
    assert re.search(r"innerText[^;]*length > \d+", block), (
        "lifting on the dialog NODE brings back the empty-card flash"
    )


# -- 2:25 the back-swipe ------------------------------------------------------

def test_a_back_drag_is_damped_and_never_hands_over_the_screen():
    """min(-dx, w) was a clamp, not a band: a committed swipe dragged the
    sheet 300 of 390px and what it uncovered was the scrim over the drawer.
    BACK_FREE must stay at or above the commit threshold (min(90, .3w)) at
    every phone width, or the damping would move the commit point."""
    m = re.search(r"var BACK_FREE = ([\d.]+), BACK_DAMP = ([\d.]+);", APP)
    assert m, "the damping constants are gone"
    free, damp = float(m.group(1)), float(m.group(2))
    assert free >= 0.30, f"BACK_FREE {free} falls under the .3w commit threshold"
    assert 0 < damp <= 0.2, f"BACK_DAMP {damp} is not resistance"
    assert free + (1 - free) * damp <= 0.45, "a full drag still hands over half the screen"
    assert "Math.min(-dx, g.w)" not in APP, "the old clamp is back"


def test_what_a_back_drag_uncovers_is_painted_like_the_sheet():
    """The strip behind the sheet is the settings scrim, rgba(9,11,7,.85) over
    the drawer -- a hole. Painted like the sheet, the same drag reads as the
    sheet giving way. The paint is also what makes it safe for the screen to
    leave at all (see below)."""
    assert 'root.classList.add("cai-back-drag");' in APP
    i = APP.find("html.cai-back-drag .st-key-settings_backdrop button {")
    assert i > 0, "the paint has no rule"
    rule = APP[i:i + 400]
    assert "linear-gradient(180deg,#141710 0%,#0E1007 100%)" in rule, (
        "the strip must wear the sheet's own background"
    )
    assert "pointer-events: none" in rule, (
        "the scrim is a button that CLOSES settings and it is bare to a thumb "
        "while the sheet is away"
    )


def test_the_paint_cannot_outlive_the_gesture():
    """Left on, it is worse than the bug: a scrim that cannot be tapped and a
    sheet that may be invisible -- a dead app. backReset() is on every path
    out (commit, spring-back, touchcancel, the 4s guard, the sweep)."""
    i = APP.find("var backReset = function (panel) {")
    assert i > 0
    assert 'classList.remove("cai-back-drag")' in APP[i:i + 200], (
        "backReset must un-paint before anything else -- it runs even when "
        "there is no panel left"
    )


def test_a_committed_back_swipe_carries_the_screen_out():
    """Easing it BACK to rest and dimming it there reads as a gesture that
    failed and a screen that changed anyway. It leaves the way the finger
    sent it (2026-08-10 forbade that while the strip behind was black; the
    paint is what changed)."""
    i = APP.find('if (cur.mode === "back") {')
    assert i > 0
    block = APP[i:i + 2800]
    assert 'panel.style.transform = "translateX(-" + Math.round(cur.w) + "px)";' in block
    assert 'panel.style.opacity = "0";' in block
    assert 'panel.style.transform = "translateX(0)"' not in block, "the bounce is back"


def test_the_new_screen_is_waited_for_by_its_title():
    """A rerun arrives over several deltas. Clearing on the first mutation put
    the panel back at full opacity mid-swap -- half the old screen, half the
    new. The title is the one node that changes exactly when the screen does.
    A dialog has no title: there the first mutation is still the signal."""
    i = APP.find("var mo = new MutationObserver(done);")
    assert i > 0
    block = APP[max(0, i - 2200):i]   # the whole commit branch above it
    assert 'var ttl = panel.querySelector(".cai-set-title");' in block
    assert "if (was !== null && recs) {" in block, (
        "the backstop must still clear unconditionally"
    )
    assert "doc.contains(panel)" in block, "a panel that left the document counts as changed"
    assert "setTimeout(function () { done(); }, 1400)" in APP, "no backstop"


def test_the_screen_comes_back_even_when_raf_is_starved():
    """The panel is invisible between the exit and the new screen. A web view
    that is backgrounded mid-gesture starves requestAnimationFrame, and on rAF
    alone the sheet would stay at opacity 0 with its scrim untappable. Same
    whichever-runs-first pair the canvas sync uses."""
    i = APP.find("var up = false;")
    assert i > 0, "the raise has no guard"
    block = APP[i:i + 600]
    assert "requestAnimationFrame(raise);" in block
    assert re.search(r"setTimeout\(raise, \d+\);", block), "no timeout backstop for the raise"


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS", name)
            except AssertionError as exc:
                failures += 1
                print("FAIL", name, "-", str(exc).encode("ascii", "replace").decode())
    sys.exit(1 if failures else 0)
