# -*- coding: utf-8 -*-
"""The compliance layer: privacy policy, accessibility statement, contact.

Run: venv\\Scripts\\python.exe tests\\test_compliance_screens.py
Prints only ASCII (cp1252 console pitfall) -- Hebrew appears in the assertions
but never in printed output.

These are not style checks. Every assertion here guards a statement the app
makes TO A USER about what happens to their data, and the whole reason this
file exists is that one such statement was false in production for months:
the privacy banner read "the information is stored encrypted on the device and
is not sent to an external server" while every question was being sent to the
Anthropic API and written verbatim into a Google Sheet.

A promise the code does not keep is the one defect class that cannot be caught
by reading the code alone -- you have to read the code AND the sentence next
to it. That pairing is what these tests automate.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = (ROOT / "app.py").read_text(encoding="utf-8")
METRICS = (ROOT / "metrics.py").read_text(encoding="utf-8")


# ── P1: the banner that was false ────────────────────────────────────────────

def test_privacy_banner_does_not_claim_data_stays_on_device():
    """The exact sentence that shipped, and what made it dangerous.

    It was not merely inaccurate. Clause 3 of the ToS forbids the user from
    entering classified material, and this banner told them the box was safe --
    the app argued both sides of the same question, and the reassuring side is
    the one rendered in a green card at the top of the privacy screen.
    """
    assert "אינו נשלח לשרת חיצוני" not in APP, (
        "the false no-external-server claim is back"
    )
    assert "המידע נשמר מוצפן במכשיר" not in APP, (
        "the false on-device-encryption claim is back"
    )


def test_privacy_screen_names_the_external_processors():
    """Naming them is the substance: 'we take reasonable measures' is what the
    old ToS clause 4 said, and it is compatible with any behaviour at all."""
    body = APP.split("_PRIVACY_SECTIONS = [")[1].split("\n]")[0]
    for token in ("Anthropic", "Google"):
        assert token in body, f"the privacy policy must name {token} as a processor"


def test_privacy_screen_warns_before_the_input_not_after():
    """The warning has to reach the person while they are deciding what to
    type. A truthful policy three taps into settings does not."""
    assert "אין להזין מידע מסווג" in APP


# ── P2: the privacy policy itself ────────────────────────────────────────────

def test_privacy_policy_section_list_exists():
    assert "_PRIVACY_SECTIONS = [" in APP, (
        "the policy must be a data list like _TOS_SECTIONS, not inline markup"
    )


def test_privacy_policy_covers_the_mandatory_topics():
    """What a privacy policy is REQUIRED to answer. Missing any one of these
    is what turns 'we have a policy' into 'we have a paragraph'."""
    body = APP.split("_PRIVACY_SECTIONS = [")[1].split("\n]")[0]
    required = {
        "collection": "איזה מידע נאסף",
        "purpose": "למה נאסף",
        "third_party": "למי המידע מועבר",
        "retention": "כמה זמן",
        "rights": "זכויות",
        "controller": "בעל המאגר",
    }
    for key, heading in required.items():
        assert heading in body, f"privacy policy is missing the {key} section"


def test_privacy_policy_is_reachable_from_settings():
    assert '"policy"' in APP, "no settings_screen value for the policy"
    assert "_settings_privacy_policy" in APP
    assert 'nav_policy' in APP, "no nav row opens the policy"


# ── P3: accessibility statement ──────────────────────────────────────────────

def test_accessibility_statement_exists():
    assert "_A11Y_SECTIONS = [" in APP
    assert "_settings_a11y" in APP
    assert 'nav_a11y' in APP, "no nav row opens the accessibility statement"


def test_accessibility_statement_declares_known_limitations():
    """A statement that claims full conformance is worth less than one that
    names what is not there yet -- and these two are KNOWN (2026-08-10 iOS
    audit): the drawer search field is 13px and the dialog fields 14/14.5px,
    all three under the 16px iOS focus-zoom floor."""
    body = APP.split("_A11Y_SECTIONS = [")[1].split("\n]")[0]
    assert "מגבלות ידועות" in body, "the statement must declare its gaps"
    assert "חיפוש" in body, "the 13px drawer search field is a known gap"


def test_accessibility_statement_names_a_contact_route():
    body = APP.split("_A11Y_SECTIONS = [")[1].split("\n]")[0]
    assert "_CONTACT_EMAIL" in body or "פנייה" in body


# ── P4: one contact address, defined once ────────────────────────────────────

def test_contact_address_is_a_single_constant():
    assert "_CONTACT_EMAIL = " in APP, "the address must be one constant"
    # every other occurrence has to be the constant, never the literal: three
    # screens quote this address and a stale one in any of them is a dead
    # support channel that still looks alive
    literals = re.findall(r"commandai\.support@gmail\.com", APP)
    assert len(literals) == 1, (
        f"the address is written literally {len(literals)} times; use _CONTACT_EMAIL"
    )


def test_contact_screen_exists_and_is_reachable():
    assert "_settings_contact" in APP
    assert 'nav_contact' in APP


def test_report_form_reuses_the_feedback_tab():
    """A new Sheets tab/column is a schema risk: _append_to_sheet writes rows
    POSITIONALLY against a header created on the tab's first use. log_feedback
    already carries a free-text `comment` and a `verdict`, so the report rides
    an existing, already-migrated shape."""
    assert "verdict=\"report\"" in APP or "verdict='report'" in APP, (
        "the report must log through log_feedback, not a new tab"
    )
    assert "_FEEDBACK_COLUMNS = [" in METRICS
    cols = METRICS.split("_FEEDBACK_COLUMNS = [")[1].split("]")[0]
    assert "comment" in cols, "log_feedback must keep carrying the report text"


# ── P5: the wipe button must not promise what it cannot do ───────────────────

def test_wipe_button_does_not_promise_server_deletion():
    """_wipe_all clears session_state and rotates the analytics id. The rows
    already in the Sheet -- full question text, role, timestamp -- stay. The
    old label said 'delete all data' full stop."""
    assert "מחיקת כל הנתונים מהמכשיר" not in APP, (
        "the unqualified wipe label is back"
    )
    assert "_WIPE_NOTE" in APP, (
        "the wipe needs a note saying what it does NOT reach"
    )


# ── P6: corpus coverage is disclosed before a question is spent ──────────────

def test_coverage_is_disclosed_on_the_greeting_screen():
    """60% of questions come back 'not in the supplied orders' and the user
    only learns the corpus is partial AFTER burning one of five daily
    questions. The greeting screen is the last surface before that spend."""
    assert "_CORPUS_NOTE" in APP
    # [-1], not [1]: the class is defined in CSS twice before the render site,
    # and slicing from the first hit inspects a stylesheet instead of a screen
    greet = APP.split("cai-greet-sub")[-1][:1400]
    assert "_CORPUS_NOTE" in greet, "the note must render on the greeting screen"


def test_coverage_note_is_derived_not_hardcoded():
    """A hardcoded '289 orders' rots on the next ingest wave -- and this app
    ingests in waves by design."""
    note = APP.split("_CORPUS_NOTE")[1][:600]
    assert not re.search(r"\b(289|447|124|98)\b", note), (
        "the corpus size must be counted at runtime, not written in"
    )


# ── P7: the app may not claim to be an internal military system ──────────────

def test_app_does_not_claim_internal_use_only():
    """'לשימוש פנימי בלבד' is the phrase of an official IDF document, and it sat
    on the entry screen, the drawer foot and the settings foot of a PUBLIC app
    whose own ToS clause 1 says 'not an official IDF tool, built by an
    independent developer'. It is a factual claim the app cannot back -- the
    same defect class as the privacy banner, one screen earlier.

    'בלמ״ס' on its own is deliberately NOT tested here: it describes the orders
    (which really are unclassified and carry the mark) and is the app's design
    language. This pins the claim about the app, not the mark on the documents.
    """
    assert "לשימוש פנימי בלבד" not in APP, (
        "the internal-use-only claim is back"
    )


def test_footers_carry_the_honest_line():
    """One phrase, everywhere a footer states what the app is -- the About
    screen already said it; the entry/drawer/settings footers contradicted it."""
    assert APP.count("כלי עזר פרטי · אינו כלי רשמי של צה\\\"ל") >= 3, (
        "entry, drawer and settings footers must all carry the honest line"
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
                # ASCII-only: an assertion message may quote Hebrew source
                print("FAIL", name, "-", str(exc).encode("ascii", "replace").decode())
            except Exception as exc:  # a missing split target reads as a fail
                failures += 1
                print("FAIL", name, "-", f"{type(exc).__name__}: {exc}"[:120])
    sys.exit(1 if failures else 0)
