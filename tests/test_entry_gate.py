# -*- coding: utf-8 -*-
"""The entry flow: consent first, then the role. (2026-09-12)

WHAT CHANGED AND WHY

Until now the first screen was the role picker, and a tap on a role raised a
floating card over it asking for a name. Two defects in one:

  * the card covered the three buttons it was launched from -- the user called
    it unprofessional, and he was right;
  * nowhere in the flow did the user ever agree to anything. Apple's 5.1.2(i)
    wants explicit, informed consent BEFORE user content reaches a third
    party, and every question in this app is sent to Anthropic. A truthful
    privacy policy three taps into settings is not that consent.

The flow is now two real screens:

  1. WELCOME -- what the app is, the (optional) name, and the consent tick.
     The consent is a separate, visible act: consent implied by pressing
     "continue" is the pattern reviewers reject, and a tick is also what lets
     us record that it was given and never ask again.
  2. ROLE -- greeted by the name that was just typed, then the same three
     buttons as before, with nothing floating over them.

Skipping the name is allowed (it exists for a greeting, not for identity), but
skipping the CONSENT is not: the skip gives up the greeting, not the approval.

    venv\\Scripts\\python.exe tests\\test_entry_gate.py
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# app.py's _startup_ingest ingests every un-ingested PDF THROUGH THE PAID API,
# and app.py binds the names at import -- patch before importing anything that
# pulls it in (the 2026-08-16 rule).
import backend  # noqa: E402
backend.ensure_pdfs_ingested = lambda *a, **k: None
backend.warm_index = lambda *a, **k: 0
import storage.vector_store as vs  # noqa: E402
vs._save_emb_cache = lambda: None
import metrics  # noqa: E402
metrics.reserve = lambda *a, **k: "ok"
metrics.refund = lambda *a, **k: None
metrics.log_question = lambda **kw: None

from streamlit.testing.v1 import AppTest  # noqa: E402

APP = (ROOT / "app.py").read_text(encoding="utf-8")
# 2026-09-17: the tick approves the terms, recorded as a version (tos_ok);
# a device past the welcome also has name_asked
TOS_VERSION = int(re.search(r"^TOS_VERSION = (\d+)", APP, re.M).group(1))
APPROVED = dict(tos_ok=TOS_VERSION, name_asked=True)

CONSENT_KEY = "gate_consent"
GO = "המשך"
SKIP = "אפשר גם בלי שם"


def _fresh(**state):
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=120)
    for k, v in state.items():
        at.session_state[k] = v
    at.run()
    return at


def _btn(at, label):
    """A submit/button by its visible label -- form_submit_button takes no key."""
    for b in at.button:
        if (b.label or "").strip() == label:
            return b
    return None


def _has_roles(at):
    return any(b.key in ("role_soldier", "role_commander", "role_reserve")
               for b in at.button if b.key)


def _consent(at):
    for c in at.checkbox:
        if c.key == CONSENT_KEY:
            return c
    return None


def _text(at):
    return " ".join(m.value for m in at.markdown)


def _name(at):
    """AppTest.session_state has no .get — index it (2026-08-16 lesson)."""
    if "profile_name" not in at.session_state:
        return ""
    return (at.session_state["profile_name"] or "").strip()


# ── screen 1: the welcome ────────────────────────────────────────────────
def test_the_first_screen_asks_for_consent_not_for_a_role():
    at = _fresh()
    assert _consent(at) is not None, "no consent tick on the first screen"
    assert not _has_roles(at), "the role buttons must wait for the second screen"


def test_the_consent_names_the_processor_and_the_classified_rule():
    at = _fresh()
    body = _text(at)
    assert "Anthropic" in body, "the consent must name who receives the questions"
    assert "מסווג" in body, "the consent must carry the no-classified-material rule"


def test_the_first_screen_offers_the_name_and_a_way_past_it():
    at = _fresh()
    assert any(t.key == "gate_name_w" for t in at.text_input), "no name field"
    assert _btn(at, SKIP) is not None, "no way past the name"


# ── the consent is the gate ──────────────────────────────────────────────
def test_continuing_without_the_tick_does_not_advance():
    at = _fresh()
    _btn(at, GO).click().run()
    assert not _has_roles(at), "advanced to the role screen without consent"
    assert _consent(at) is not None, "still on the welcome screen"


def test_skipping_the_name_still_requires_the_tick():
    at = _fresh()
    _btn(at, SKIP).click().run()
    assert not _has_roles(at), "the skip must give up the greeting, not the consent"


def test_the_tick_and_continue_reach_the_role_screen():
    at = _fresh()
    _consent(at).check().run()
    _btn(at, GO).click().run()
    assert _has_roles(at), "consent given, but the role screen never came"
    assert _consent(at) is None, "the consent screen is still up after consenting"


def test_the_skip_works_once_the_tick_is_there():
    at = _fresh()
    _consent(at).check().run()
    _btn(at, SKIP).click().run()
    assert _has_roles(at), "skipping with consent must reach the role screen"
    assert not _name(at), "the skip must not record a name"


# ── the name earns its greeting ──────────────────────────────────────────
def test_the_name_typed_here_greets_on_the_role_screen():
    at = _fresh()
    at.text_input(key="gate_name_w").set_value("שלו")
    _consent(at).check().run()
    _btn(at, GO).click().run()
    assert _name(at) == "שלו"
    assert "שלו" in _text(at), "the role screen does not greet by name"


# ── asked once, remembered ───────────────────────────────────────────────
def test_a_device_that_already_consented_starts_at_the_role_screen():
    at = _fresh(**APPROVED)
    assert _consent(at) is None, "consent was asked a second time"
    assert _has_roles(at), "a consenting device must land on the role picker"


def test_the_pre_terms_consent_flag_is_asked_once_more():
    """consent_given (2026-09-12) ticked a sentence about Anthropic; the terms
    were on no screen. Those devices meet the welcome again, terms included."""
    at = _fresh(consent_given=True, name_asked=True)
    assert _consent(at) is not None, "the old flag was taken as a terms approval"


def test_the_consent_rides_the_device_cookie():
    """Recorded where role and name already live, so it survives a restart."""
    assert '_ck_dict["tos"] = ' in APP, "the approval is not written to the profile cookie"
    assert 'st.session_state.setdefault("tos_ok"' in APP, (
        "the approval is never seeded back from the cookie"
    )


# ── the floating card is gone ────────────────────────────────────────────
def test_the_floating_name_card_is_gone():
    assert "cai_name_card" not in APP, "the overlay card is back"
    assert "איך קוראים לך?" not in APP, "the overlay card's title is back"


def test_the_role_screen_has_nothing_floating_over_it():
    at = _fresh(**APPROVED)
    assert _btn(at, GO) is None and _btn(at, SKIP) is None, (
        "the welcome controls are still rendered over the role screen"
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
                print("FAIL", name, "-", str(exc)[:160])
            except Exception as exc:  # a missing widget is a failure, not a crash
                failures += 1
                print("ERROR", name, "-", repr(exc)[:200])
    sys.exit(1 if failures else 0)
