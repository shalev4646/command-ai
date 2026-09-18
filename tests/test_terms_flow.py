# -*- coding: utf-8 -*-
"""The terms gate and the destructive settings actions, run for real.

    venv\\Scripts\\python.exe tests\\test_terms_flow.py

test_terms_gate.py lifts the pieces out of app.py; this file runs app.py
(AppTest, backend stubbed as in test_entry_gate.py) and taps the buttons a
person taps. It pins the two things that only show up when the screens run in
order:

  * where each action LANDS -- logout on the welcome screen with the tick set
    and the name empty (before the 2026-09 port: the role picker, no name
    field), a wipe on the welcome with the tick empty, a withdrawal on the
    welcome with the name still there;
  * that nothing destructive happens on the first tap. Every one of these was
    a single tap in production: logout (which since conversation restore also
    erases the chats kept on the device), both "נקה היסטוריית שיחות" rows, and
    "מחיקת הנתונים מהמכשיר הזה".
"""
import ast
import contextlib
import datetime as _dt
import json
import sys
import urllib.parse
import zoneinfo
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# app.py's _startup_ingest ingests un-ingested PDFs THROUGH THE PAID API and
# binds the names at import -- patch first (the 2026-08-16 rule).
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
_TREE = ast.parse(APP)


def _const(name, default):
    for n in _TREE.body:
        if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == name:
            return ast.literal_eval(n.value)
    return default


# a missing constant must fail the tests that need it, not the import
TOS = _const("TOS_VERSION", 1)
SECTIONS = _const("_TOS_SECTIONS", [])

OLD_CHAT = {"title": "שאלה ישנה", "role": "soldier",
            "messages": [{"role": "user", "content": "שאלה ישנה"}]}
WIPE_NOTE_PART = "בקשת מחיקה"


@contextlib.contextmanager
def _probe_answers(payload):
    """The device-profile probe as it really behaves: nothing on the first
    render, the stored payload on the next (a component round trip). AppTest
    hands custom components their default, so the timing has to be faked."""
    import streamlit.components.v1 as components
    real = components.declare_component
    calls = {"n": 0}                       # shared across runs, like the client

    def fake(name, *a, **kw):
        if name != "cai_profile_probe":
            return real(name, *a, **kw)

        def probe(*args, default=None, **kwargs):
            calls["n"] += 1
            return default if calls["n"] == 1 else payload
        return probe

    components.declare_component = fake
    try:
        yield
    finally:
        components.declare_component = real


def _signed_in(screen=None, **extra):
    # consent_given is the pre-port flag every production device carries; the
    # port ignores it, and keeping it here is what lets the same state reach
    # the settings screens on the old code too (a red run that fails for the
    # right reason instead of stopping at the welcome screen)
    state = dict(role="soldier", name_asked=True, tos_ok=TOS, tos_date="2026-09-01",
                 consent_given=True,
                 profile_name="Dana", conversation_history=[dict(OLD_CHAT)])
    if screen:
        state.update(show_settings=True, settings_screen=screen)
    state.update(extra)
    return state


def _fresh(**state):
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=120)
    for k, v in state.items():
        at.session_state[k] = v
    at.run()
    assert not at.exception, "the app raised: %s" % (at.exception,)
    return at


def _ss(at, key, default=None):
    """AppTest.session_state has no .get -- index it (2026-08-16 lesson)."""
    return at.session_state[key] if key in at.session_state else default


def _key(at, key):
    for b in at.button:
        if b.key == key:
            return b
    return None


def _label(at, label):
    for b in at.button:
        if (b.label or "").strip() == label:
            return b
    return None


def _tap(at, key):
    b = _key(at, key)
    assert b is not None, "no button %r on this screen" % key
    b.click().run()
    assert not at.exception, "the app raised: %s" % (at.exception,)
    return at


def _text(at):
    return " ".join(m.value for m in at.markdown)


def _widget(at, kind, key):
    for w in getattr(at, kind):
        if w.key == key:
            return w
    return None


def _roles(at):
    return any(_key(at, k) for k in ("role_soldier", "role_commander", "role_reserve"))


def _on_welcome(at):
    return _widget(at, "checkbox", "gate_consent") is not None


# -- a new device --------------------------------------------------------------

def test_the_full_terms_are_on_the_screen_where_they_are_approved():
    at = _fresh()
    body = _text(at)
    assert SECTIONS, "app.py has no _TOS_SECTIONS to show"
    for heading, _ in SECTIONS:
        assert heading in body, "the welcome screen does not carry %r" % heading
    tick = _widget(at, "checkbox", "gate_consent")
    assert "תנאי השימוש" in (tick.label or ""), "the tick does not say what it approves"
    assert tick.value is False, "the tick starts set on a new device"


def test_continuing_without_the_tick_records_nothing():
    """The welcome stays up either way (the name is unanswered too), so only
    the record shows whether the approval was written behind the tick."""
    at = _fresh()
    _label(at, "המשך").click().run()
    assert _on_welcome(at)
    assert not _ss(at, "tos_ok") and not _ss(at, "tos_date"), (
        "an approval was recorded although the tick was never set")


def test_approving_records_the_version_and_todays_date():
    at = _fresh()
    _widget(at, "checkbox", "gate_consent").check().run()
    _label(at, "המשך").click().run()
    today = _dt.datetime.now(zoneinfo.ZoneInfo("Asia/Jerusalem")).date().isoformat()
    assert _ss(at, "tos_ok") == TOS, "passing the gate did not record the version"
    assert _ss(at, "tos_date") == today, "passing the gate did not record the day"
    assert _roles(at), "the approval did not reach the role screen"


# -- logout ---------------------------------------------------------------------

def test_logout_asks_first():
    at = _tap(_fresh(**_signed_in("hub")), "danger_logout")
    assert _ss(at, "role") == "soldier" and _ss(at, "profile_name") == "Dana", (
        "logout signed the person out on the first tap")
    assert _ss(at, "conversation_history"), "logout erased the chats on the first tap"
    assert "להתנתק?" in _text(at), "no question was asked"
    _tap(at, "logout_no")
    assert _ss(at, "role") == "soldier" and _ss(at, "conversation_history")
    assert "להתנתק?" not in _text(at), "the question stayed up after cancelling"


def test_logout_lands_on_the_welcome_with_the_tick_set_and_no_name():
    at = _tap(_tap(_fresh(**_signed_in("hub")), "danger_logout"), "danger_logout_yes")
    assert _ss(at, "role") is None and not _ss(at, "profile_name")
    assert not _ss(at, "conversation_history"), "logout kept the chats"
    assert _ss(at, "tos_ok") == TOS, "logout withdrew the terms approval"
    assert _on_welcome(at) and not _roles(at), (
        "logout did not land on the welcome screen -- the original report")
    assert _widget(at, "checkbox", "gate_consent").value is True, (
        "the tick is empty after logout; this device did approve")
    assert _widget(at, "text_input", "gate_name_w").value == "", (
        "the previous person's name is offered to the next")


def test_a_long_name_survives_the_screen_it_is_shown_on():
    """The field is max_chars=20; הגדרות ← פרטים אישיים has no limit and the
    cookie keeps 40. A device that saved a longer name meets this screen once,
    on this deploy — and the browser reports the field back on submit, so a
    tighter limit here silently rewrites the name that was already stored."""
    long_name = "אלכסנדר בן-ציון רוזנברג"          # 23 characters
    at = _fresh(**_signed_in(tos_ok=0, profile_name=long_name))
    assert _on_welcome(at), "the terms were not re-asked"
    assert _widget(at, "text_input", "gate_name_w").value == long_name
    _widget(at, "checkbox", "gate_consent").check().run()
    _label(at, "המשך").click().run()
    assert _ss(at, "profile_name") == long_name, "the screen truncated a saved name"


def test_passing_a_tick_nobody_touched_does_not_restamp_the_day():
    """After a logout the tick shows the approval this DEVICE already holds.
    Pressing past it is not a new approval, and the date must keep saying when
    the terms were actually approved."""
    at = _fresh(**_signed_in(name_asked=False, profile_name="", tos_date="2026-09-01"))
    assert _on_welcome(at)
    assert _widget(at, "checkbox", "gate_consent").value is True
    _widget(at, "text_input", "gate_name_w").set_value("דנה")
    _label(at, "המשך").click().run()
    assert _ss(at, "tos_ok") == TOS
    assert _ss(at, "tos_date") == "2026-09-01", (
        "an untouched tick re-stamped the approval with today's date")


def test_a_name_restored_from_the_browser_store_reaches_the_field():
    """No cookie (Community Cloud, or iOS dropping it): the probe answers on
    the run AFTER the first paint, so the field was seeded empty before the
    name arrived and kept its empty value."""
    payload = urllib.parse.quote(json.dumps(
        {"role": "soldier", "name": "דנה", "asked": True}))
    with _probe_answers(payload):
        at = _fresh()
        at.run()                      # the payload lands on the second run
        assert not at.exception, at.exception
        assert _ss(at, "profile_name") == "דנה", "the probe did not restore the name"
        assert _on_welcome(at), "the terms were not asked"
        assert _widget(at, "text_input", "gate_name_w").value == "דנה", (
            "the restored name never reached the field")


def test_an_unanswered_question_does_not_wait_for_the_next_visit():
    """A question belongs to the screen it was asked on. Left unanswered — the
    settings closed — it must not greet the next visit already open."""
    at = _tap(_fresh(**_signed_in("hub")), "danger_logout")
    assert "להתנתק?" in _text(at)
    _tap(at, "settings_back")                  # on the hub, back closes settings
    assert not _ss(at, "show_settings"), "back did not close settings"
    at.session_state["show_settings"] = True
    at.run()
    assert "להתנתק?" not in _text(at), "the logout question was still up on the next visit"
    assert _key(at, "danger_logout") is not None, "the logout button did not come back"


def test_a_question_does_not_follow_to_another_screen():
    at = _tap(_fresh(**_signed_in("hub")), "nav_clearhist")
    assert "למחוק את כל השיחות?" in _text(at)
    _tap(at, "nav_privacy")
    _tap(at, "settings_back")                  # privacy -> hub
    assert "למחוק את כל השיחות?" not in _text(at), (
        "the question asked before leaving the hub was still up on returning")
    assert _ss(at, "conversation_history"), "the chats went"


# -- נקה היסטוריית שיחות, from both places --------------------------------------

def test_clearing_history_from_the_hub_asks_first():
    at = _tap(_fresh(**_signed_in("hub")), "nav_clearhist")
    assert _ss(at, "conversation_history"), "the chats went on the first tap"
    assert "למחוק את כל השיחות?" in _text(at)
    _tap(at, "clearhist_no")
    assert _ss(at, "conversation_history"), "cancelling erased the chats"
    _tap(_tap(at, "nav_clearhist"), "danger_clearhist_yes")
    assert not _ss(at, "conversation_history"), "confirming did not erase them"
    assert _ss(at, "role") == "soldier", "clearing the chats signed the person out"


def test_clearing_history_from_privacy_asks_first():
    at = _tap(_fresh(**_signed_in("privacy")), "nav_clearhist2")
    assert _ss(at, "conversation_history"), "the chats went on the first tap"
    assert "למחוק את כל השיחות?" in _text(at)
    _tap(at, "danger_clearhist2_yes")
    assert not _ss(at, "conversation_history")


# -- מחיקת הנתונים מהמכשיר הזה ----------------------------------------------------

def test_the_wipe_asks_first_and_says_it_once():
    at = _tap(_fresh(**_signed_in("privacy")), "danger_wipe")
    assert _ss(at, "profile_name") == "Dana" and _ss(at, "tos_ok") == TOS, (
        "the device was wiped on the first tap")
    assert "למחוק את הנתונים מהמכשיר הזה?" in _text(at)
    assert _text(at).count(WIPE_NOTE_PART) == 1, (
        "the wipe note is on the screen twice while the question is up")


def test_the_wipe_clears_the_approval_and_lands_on_an_empty_tick():
    at = _tap(_tap(_fresh(**_signed_in("privacy")), "danger_wipe"), "danger_wipe_yes")
    assert _ss(at, "tos_ok") == 0 and _ss(at, "tos_date") == ""
    assert _on_welcome(at), "a wiped device was not asked again"
    assert _widget(at, "checkbox", "gate_consent").value is False, (
        "a wiped device shows the terms as already approved")


# -- the About screen -----------------------------------------------------------

def test_the_banner_reports_the_recorded_approval():
    body = _text(_fresh(**_signed_in("about")))
    assert "אישרת את התנאים" in body and "01.09.2026" in body and "גרסה %d" % TOS in body
    assert "בהתקנה הראשונית" not in body


def test_withdrawing_asks_first_then_reopens_the_gate_with_the_name():
    at = _tap(_fresh(**_signed_in("about")), "danger_tos_revoke")
    assert _ss(at, "tos_ok") == TOS, "the approval was withdrawn on the first tap"
    assert "לבטל את אישור התנאים?" in _text(at)
    _tap(at, "danger_tos_revoke_yes")
    assert _ss(at, "tos_ok") == 0 and _ss(at, "tos_date") == ""
    assert _on_welcome(at), "withdrawing left the app usable"
    assert _widget(at, "checkbox", "gate_consent").value is False
    assert _widget(at, "text_input", "gate_name_w").value == "Dana", (
        "withdrawing the terms asked for the name again -- it is not a sign-out")
    assert _ss(at, "role") == "soldier", "withdrawing dropped the role"


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
