# -*- coding: utf-8 -*-
"""Design invariants from the 2026-08-10 iOS/UI audit -- plain-assert script
(no pytest in venv).

Run: venv\\Scripts\\python.exe tests\\test_design_invariants.py
Prints only ASCII (cp1252 console pitfall) -- Hebrew appears in the assertions
but never in printed output.


Every assertion here corresponds to a defect that was found in PRODUCTION and
measured in a browser, not to a style preference. They are cheap string checks
over the CSS sources on purpose: the values they guard are spread across three
files and two non-f-string CSS blocks, which is exactly how they drifted apart
in the first place (the retired accent survived five months in the entry screen
because nothing failed when it did).

Contrast numbers in the docstrings were computed against --bg #14170E with the
WCAG relative-luminance formula and confirmed on the live page.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = (ROOT / "app.py").read_text(encoding="utf-8")
CONFIG = (ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8")
PWA = (ROOT / "pwa_assets.py").read_text(encoding="utf-8")


# ── C1: connection state ─────────────────────────────────────────────────────
# The original audit called stStatusWidget a wrongly-hidden connection
# indicator. That was WRONG: boot_shell.py already owns connection loss with
# #cai-net-bar, and hides the widget on purpose because it reports
# "Connecting" from an invisible corner while every control is dead. The
# assertions below lock in the real design, not the audit's first reading.

SHELL = (ROOT / "boot_shell.py").read_text(encoding="utf-8")


def test_connection_bar_owns_connection_loss():
    """It must wrap WebSocket (to see the real socket), live outside #root (so
    a rerun cannot remove it), and offer a way out."""
    assert "cai-net-bar" in SHELL
    assert "window.WebSocket = CW" in SHELL
    assert "_stcore/stream" in SHELL
    assert "רענון" in SHELL


def test_offline_fires_the_bar_without_the_socket_debounce():
    """The 4s debounce exists to ride out rerun blips; a radio that is off is
    not a blip and should not wait for it."""
    assert "window.addEventListener('offline'" in SHELL


def test_app_does_not_add_a_second_connection_banner():
    """A duplicate banner in app.py's engine was added and removed on
    2026-08-10 — it repeated the bar's message and could stack with it."""
    assert "cai-net-toast" not in APP


# ── C2: orientation ──────────────────────────────────────────────────────────

def test_manifest_locks_portrait():
    """No rule anywhere reads safe-area-inset-left/right, so landscape would
    put the header and drawer under the Dynamic Island."""
    assert '"orientation": "portrait"' in PWA


def test_lateral_insets_present_for_browser_rotation():
    """The manifest lock only governs standalone; an in-browser tab can still
    rotate, so the two edge containers carry the lateral insets."""
    assert "env(safe-area-inset-left" in APP
    assert "env(safe-area-inset-right" in APP


# ── C3: accessible names ─────────────────────────────────────────────────────

def test_icon_controls_have_accessible_names():
    """The gear and « carried a GLYPH as their accessible name; the hamburger
    and both backdrops had theirs deleted by display:none."""
    for sel, name in [
        (".st-key-open_settings button", "הגדרות"),
        (".st-key-drawer_close button", "סגירת התפריט"),
        (".st-key-drawer_open_btn button", "תפריט"),
        (".st-key-drawer_backdrop button", "סגירת התפריט"),
    ]:
        assert f'"{sel}": "{name}"' in APP, f"missing aria-label for {sel}"


def test_backdrop_labels_are_sr_only_not_display_none():
    """display:none removes the text from the accessibility tree; these
    buttons have no other name. A full-viewport unlabelled button is the
    worst offender a screen-reader user can meet."""
    assert ".st-key-drawer_backdrop button p {{ display: none; }}" not in APP
    assert ".st-key-settings_backdrop button p { display: none; }" not in APP
    assert "clip-path: inset(50%)" in APP


# ── C4: contrast ─────────────────────────────────────────────────────────────

def test_text_faint_meets_aa():
    """--text-faint carries the legal disclaimer. .4 measured 3.49:1 against
    --bg, under the 4.5:1 floor; .55 measures 5.49:1."""
    m = re.search(r"--text-faint:\s*rgba\(239,240,232,\.(\d+)\)", APP)
    assert m, "--text-faint token not found"
    assert int(m.group(1)) >= 50, (
        f"--text-faint alpha .{m.group(1)} is below the 4.5:1 threshold "
        "(.50 = 4.75:1)"
    )


def test_disclaimer_is_not_micro_type():
    """The composer disclaimer is read outdoors, in daylight."""
    tail = APP.split('content: "כלי עזר מבוסס בינה מלאכותית')[1][:400]
    m = re.search(r"font:\s*400\s+([\d.]+)px", tail)
    assert m and float(m.group(1)) >= 11, "disclaimer font dropped below 11px"


# ── C5: text scaling ─────────────────────────────────────────────────────────

def test_reading_text_scales():
    """maximum-scale=1 + gesture preventDefault + text-size-adjust:100% leave
    no other path to larger text: not pinch, not Dynamic Type, not zoom."""
    assert "--cai-fs:" in APP
    assert "calc(15px * var(--cai-fs, 1))" in APP
    assert "calc(16px * var(--cai-fs, 1))" in APP


def test_text_scale_persists_without_bloating_other_users_cookie():
    """Same contract as the mil/sol slots: absent unless changed, so an
    untouched user's cookie payload stays byte-identical."""
    assert '_ck_dict["fs"] = _fs_key' in APP
    assert "if _fs_key:" in APP


# ── M1: one accent ───────────────────────────────────────────────────────────

def test_primary_color_matches_the_app_accent():
    """#99A26B was retired on 2026-08-03; config.toml kept it, so every
    control Streamlit painted itself used the old olive."""
    m = re.search(r'primaryColor\s*=\s*"(#[0-9A-Fa-f]{6})"', CONFIG)
    assert m, "primaryColor not found"
    accent = re.search(r'"accent":\s*"(#[0-9A-Fa-f]{6})"', APP).group(1)
    assert m.group(1).upper() == accent.upper(), (
        f"config primaryColor {m.group(1)} != app accent {accent}"
    )


def test_retired_accent_is_gone_from_css():
    """Only the historical comments may still name it."""
    for line in APP.splitlines():
        stripped = line.strip()
        if "#99A26B" in line and not (
            stripped.startswith("#") or stripped.startswith("/*")
            or stripped.startswith("*") or "retired" in line or "was the" in line
        ):
            raise AssertionError(f"retired accent still used: {line.strip()[:90]}")


def test_text_color_has_one_value():
    m = re.search(r'textColor\s*=\s*"(#[0-9A-Fa-f]{6})"', CONFIG)
    token = re.search(r"--text:\s*(#[0-9A-Fa-f]{6})", APP)
    assert m and token
    assert m.group(1).upper() == token.group(1).upper(), (
        "config textColor and the --text token disagree — that split is what "
        "let app CSS drift to a third near-white"
    )


# ── M4/M5/M6: motion, states, focus ──────────────────────────────────────────

def test_reduced_motion_covers_transitions():
    """The block used to reset animations only, leaving every transition
    running at full speed for a user who asked the OS for less motion."""
    block = APP.split("@media (prefers-reduced-motion: reduce) {{")[1][:400]
    assert "transition-duration" in block


def test_focus_visible_ring_exists():
    assert ":focus-visible" in APP
    assert "outline: 2px solid var(--accent) !important" in APP


def test_disabled_state_exists():
    assert "button:disabled, button[disabled]" in APP


# ── M7/M8: touch targets ─────────────────────────────────────────────────────

def test_primary_controls_meet_the_thumb_floor():
    """44x44 is the floor. The hamburger was 42 and the name input 38."""
    hb = APP.split(".st-key-drawer_open_btn button {{")[1][:220]
    assert "width: 44px" in hb and "height: 44px" in hb

    name = APP.split('.st-key-cai_name_card [data-testid="stTextInput"] input {{')[1][:320]
    assert "min-height: 44px" in name


def test_header_padding_tracks_the_hamburger():
    """The wordmark optically centres between the button-side padding edge and
    the identity pill, so padding = inset + button width. Measured live after
    the 42->44 change: 75px of gap on each side."""
    assert "max(62px, env(safe-area-inset-right" in APP, (
        "header padding must be 18 (inset) + 44 (button) = 62"
    )


def test_no_duplicate_owner_for_the_drawer_close_button():
    """Two !important rules used to set its size, radius, background, border
    and colour differently; only source order decided the winner."""
    assert "width: 36px !important; height: 36px !important" not in APP


# ── answer language (2026-08-10) ─────────────────────────────────────────────
# The chat answer was the app's most-used surface and its least designed one:
# a verdict chip over bare markdown. answer_format.py now dresses the label
# grammar the prompt mandates. The values below were measured in a browser
# against a reproduction of Streamlit's chat DOM, not chosen.

ANS_CSS = APP.split(".cai-ans {{")[1].split("/* ── Escalation strip")[0]


def test_answer_blocks_space_with_padding_not_margin():
    """The blocks sit in a FLEX parent (stVerticalBlock), where margins never
    collapse, and each carries stMarkdownContainer's margin-bottom:-1rem
    inside it. Measured in the running app: gap = (previous padding-bottom
    - 16) + next padding-top + 3. 27/11 puts every seam at 14px; an earlier
    16/11 left block-to-paragraph at 3px and label-to-list at -8 (overlap)."""
    decl = ANS_CSS.split("}}")[0]
    assert "padding: 11px 0 27px" in decl, decl.strip()[:120]
    assert "margin" not in decl, "margins do not collapse in a flex parent"


def test_a_block_after_a_block_drops_its_top_padding():
    """Without this the two paddings stack and consecutive blocks sit 25px
    apart while everything else sits at 14."""
    assert ':has(.cai-ans, .verdict-solo, .verdict-stack)' in APP
    assert '+ [data-testid="stElementContainer"] .cai-ans {{ padding-top: 0; }}' in APP


def test_every_chip_form_cancels_the_negative_margin_the_same_way():
    """.verdict-stack had 22px (a 6px gap) and the single chip had nothing at
    all — the answer language's first block overlapped a single chip by 5px
    (measured). One number for both now."""
    assert ".verdict-solo {{ padding-bottom: 27px; }}" in APP
    assert "padding-bottom: 22px;" not in APP, ".verdict-stack must match"
    assert APP.count('<div class="verdict-solo">') == 2, (
        "both the verdict path and the neutral-refusal path must wrap"
    )


def test_answer_language_type_scales_with_the_reading_size():
    """Answer text is reading text: every font in these blocks must ride
    --cai-fs, or the text-size setting silently stops covering the surface it
    matters most on."""
    bare = re.findall(r"font:\s*\d+\s+(\d+(?:\.\d+)?px)", ANS_CSS)
    assert not bare, f"fixed font sizes in the answer language: {bare}"


def test_the_answer_body_renders_through_the_formatter():
    """A bare st.markdown(body) in the conversation loop would silently
    restore the old look. (_render_body keeps one as its stale-build
    fallback — that one is deliberate and lives above the loop.)"""
    loop = APP.split("# ── Conversation ──")[1]
    assert "_render_body(body, chip)" in loop, (
        "the chip must reach the formatter — it is how the routing block "
        "knows the neutral pill already said its label"
    )
    assert "st.markdown(body)" not in loop


# ── the source row must survive any clause the model writes ─────────────────

def test_source_row_cannot_be_overflowed_by_a_long_clause():
    """.c is sized by CONTENT the model wrote, and nothing caps its length.

    Measured on production at 320px AND 430px: a clause of "סעיף חופשה
    שנתית — מכסה שנתית: ..." rendered .c at 496px inside a 312px row, which
    squeezed .t — the ORDER NUMBER — to width 0 and pushed the text to
    x=-298, outside a viewport that cannot scroll sideways. A ruling whose
    provenance is invisible is the one failure this app cannot ship.
    """
    row = APP.split(".cai-ans-src .r {{")[1].split("}}")[0]
    assert "flex-wrap: wrap" in row, "the source row must wrap"

    clause = APP.split(".cai-ans-src .c {{")[1].split("}}")[0]
    assert "white-space: nowrap" not in clause, (
        "a nowrap badge cannot hold model-written text"
    )
    assert "flex: 0 1 auto" in clause and "min-width: 0" in clause, (
        "the clause must be allowed to shrink"
    )

    title = APP.split(".cai-ans-src .t {{")[1].split("}}")[0]
    assert "min-width: 10ch" in title, (
        "the order number needs a FLOOR, not min-width:0 — with 0 it still "
        "collapsed to nothing whenever the clause won the space fight"
    )


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS", name)
            except AssertionError as exc:
                failures += 1
                # ASCII-only: an assertion message may quote Hebrew CSS
                print("FAIL", name, "-", str(exc).encode("ascii", "replace").decode())
    sys.exit(1 if failures else 0)
