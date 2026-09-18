# -*- coding: utf-8 -*-
"""The terms-approval gate: what opens it, and what survives what.

Run: venv\\Scripts\\python.exe tests\\test_terms_gate.py
Prints only ASCII (cp1252 console pitfall).

History. 2026-09-12 put a consent tick on the welcome screen (consent_given),
a real act -- but on the Anthropic disclosure only. The terms themselves were
on no screen, while אודות showed every visitor a hardcoded green "you approved
the terms, at first install, version 2.4". A cloud session then built a
versioned terms gate on an older base (claude/cool-cori-s94h5t); this file
ports its behaviour tests onto the welcome screen that actually shipped.

These are behaviour tests, not text checks. app.py cannot be imported without
Streamlit and a corpus, so the identity functions, the gate expression, the
widget seeds and the small terms helpers are LIFTED OUT OF THE FILE -- by AST,
never by retyping -- and run against a fake session_state. A test that
re-types the condition it guards passes forever after the real one drifts.

Pinned, and why:
  a new device starts empty    a pre-ticked consent box is not consent
  logout keeps the approval    the terms belong to the device, so the welcome
                               reopens with the box ticked and the name empty.
                               (Before this port, production landed on the
                               role picker with no name field at all -- the
                               report that started the whole round.)
  the wipe clears it           מחק הכל promises a device at defaults; before
                               this port the device kept "ok": true
  a withdrawal reopens         the revoke button only zeroes tos_ok
  a version bump reopens       otherwise TOS_VERSION moves a banner and nothing
  the approval has a day       a version with no date is half a record
"""
import ast
import functools
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = (ROOT / "app.py").read_text(encoding="utf-8")
TREE = ast.parse(APP)


def _src(node) -> str:
    return ast.get_source_segment(APP, node)


@functools.lru_cache(maxsize=None)
def _assigned(target: str) -> str:
    """Source of the value assigned to `target`, which must be assigned once.
    (Targets are compared with ast.unparse: get_source_segment re-splits the
    whole file on every call, and over every assignment in app.py that made
    this file run for minutes.)"""
    hits = [n.value for n in ast.walk(TREE) if isinstance(n, ast.Assign)
            and any(ast.unparse(t) == target for t in n.targets)]
    assert len(hits) == 1, "%s is assigned %d times, expected once" % (target, len(hits))
    return _src(hits[0])


def _func(name: str) -> str:
    for n in TREE.body:
        if isinstance(n, ast.FunctionDef) and n.name == name:
            return _src(n)
    raise AssertionError("app.py has no top-level def " + name)


def _code_only(text: str) -> str:
    """Drop whole-line comments -- a comment naming a call satisfies a plain
    substring test (how the cloud version of this check once passed a
    mutation that emptied the branch it guarded)."""
    return "\n".join(l for l in text.split("\n") if not l.lstrip().startswith("#"))


class _SS(dict):
    """session_state: attribute access over a dict, like Streamlit's."""

    def __getattr__(self, k):
        try:
            return self[k]
        except KeyError:
            raise AttributeError(k)

    def __setattr__(self, k, v):
        self[k] = v


def _world(tos_version=1):
    st = types.SimpleNamespace(session_state=_SS())
    metrics = types.SimpleNamespace(new_session_id=lambda: "ROTATED-ID")
    ns = {"st": st, "metrics": metrics, "TOS_VERSION": tos_version,
          "_today_il": lambda: "2026-09-17"}
    for fn in ("_clear_history", "_reset_identity", "_wipe_all",
               "_record_terms_approval"):
        # a missing helper fails the test that calls it, not every test
        if ("def %s(" % fn) in APP:
            exec(_func(fn), ns)
    env = {"st": st, "int": int, "TOS_VERSION": tos_version}
    return st, ns, env


def _expr(target: str, env):
    # the AST segment of a parenthesised value drops the parentheses, and a
    # multi-line expression needs them back to be evaluated on its own
    return eval("(" + _assigned(target) + ")", dict(env))


def _gate(env) -> bool:
    return bool(_expr("_welcome_gate", env))


def _box(st, env) -> bool:
    """What the consent tick shows: the live widget value, else its seed."""
    if "gate_consent" in st.session_state:
        return bool(st.session_state["gate_consent"])
    return bool(_expr("st.session_state.gate_consent", env))


def _name_field(st, env) -> str:
    if "gate_name_w" in st.session_state:
        return st.session_state["gate_name_w"]
    return _expr("st.session_state.gate_name_w", env)


def _calls(name: str) -> int:
    return sum(1 for n in ast.walk(TREE) if isinstance(n, ast.Call)
               and isinstance(n.func, ast.Name) and n.func.id == name)


def _signed_in(st, version=1):
    """A device that has been through the gate -- carrying, like every
    production device today, the legacy consent flag as well."""
    st.session_state.clear()
    st.session_state.update(dict(
        role="soldier", name_asked=True, tos_ok=version, tos_date="2026-09-01",
        consent_given=True, profile_name="Dana", gate_name_w="Dana",
        gate_consent=True, mil_saved=True, mil_salary=12000,
        device_id="DEVICE-1", conversation_history=[1], messages=[2],
        profile_saved=["x"], profile_customized=True))


# -- the gate opens for the terms ---------------------------------------------

def test_a_new_device_meets_the_gate_with_an_empty_box():
    st, _, env = _world()
    assert _gate(env), "a device that approved nothing must meet the gate"
    assert not _box(st, env), "the tick is pre-set on a fresh device -- not consent"
    assert _name_field(st, env) == "", "a fresh device has no name to show"


def test_an_approved_device_is_past_the_gate():
    st, _, env = _world()
    _signed_in(st)
    assert not _gate(env), "a device that approved the current terms is gated again"


def test_the_legacy_consent_flag_is_not_an_approval_of_the_terms():
    """consent_given ticked a sentence about Anthropic; the terms were never on
    that screen. Treating it as approval would carry the banner's old lie."""
    st, _, env = _world()
    _signed_in(st)
    st.session_state.tos_ok = 0
    st.session_state.pop("gate_consent", None)
    assert _gate(env), "consent_given alone let a device past the terms gate"


def test_withdrawing_the_approval_reopens_the_gate_and_keeps_the_name():
    st, _, env = _world()
    _signed_in(st)
    st.session_state.tos_ok = 0            # exactly what the revoke confirm does
    for k in ("gate_consent", "gate_name_w"):
        st.session_state.pop(k, None)
    assert _gate(env), "withdrawing left the app usable -- the revoke is decorative"
    assert not _box(st, env), "the tick must reseed empty after a withdrawal"
    assert _name_field(st, env) == "Dana", (
        "a terms re-approval asks for the name again -- withdrawing consent is "
        "not a sign-out")


def test_a_version_bump_asks_again():
    st, _, _ = _world(tos_version=1)
    _signed_in(st, version=1)
    env2 = {"st": st, "int": int, "TOS_VERSION": 2}
    assert _gate(env2), "a device holding an older approval was not asked again"


# -- what each reset does and does not take -----------------------------------

def test_logout_lands_on_the_welcome_with_the_box_ticked_and_no_name():
    st, ns, env = _world()
    _signed_in(st)
    ns["_clear_history"]()
    ns["_reset_identity"]()
    assert _gate(env), (
        "logout does not reach the welcome screen -- the role picker has no "
        "name field (the original report)")
    assert st.session_state.get("tos_ok") == 1, "logout cleared the approval"
    assert st.session_state.get("tos_date") == "2026-09-01", "logout cleared its date"
    assert _box(st, env), "the tick is empty after logout; this device did approve"
    assert _name_field(st, env) == "", "the previous name is offered to the next person"
    assert st.session_state.get("device_id") == "DEVICE-1", (
        "logout rotated the analytics id -- it is the same device")


def test_logout_still_forgets_the_person():
    st, ns, _ = _world()
    _signed_in(st)
    ns["_reset_identity"]()
    assert not st.session_state.get("profile_name"), "the name survived logout"
    assert st.session_state.get("role") is None, "the role survived logout"
    assert "mil_salary" not in st.session_state, "a salary survived logout"


def test_the_wipe_clears_the_approval_and_rotates_the_id():
    st, ns, env = _world()
    _signed_in(st)
    ns["_wipe_all"]()
    assert st.session_state.get("tos_ok") == 0, (
        "מחק הכל left the terms approved on a device reset to defaults")
    assert st.session_state.get("tos_date") == "", "the wipe kept the approval date"
    assert _gate(env), "a wiped device is not asked again"
    assert not _box(st, env), "the tick must be empty on a wiped device"
    assert st.session_state.get("device_id") == "ROTATED-ID", "the id was not rotated"


def test_the_widgets_reseed_after_a_reset():
    """Dropping the widget keys is what makes them follow tos_ok and the name."""
    reset = _func("_reset_identity")
    for key in ('"gate_consent"', '"gate_name_w"'):
        assert key in reset, "%s left the widget-key reset list" % key


# -- the record: version and day ----------------------------------------------

def test_passing_the_gate_records_the_version_and_the_day():
    st, ns, _ = _world()
    ns["_record_terms_approval"]()
    assert st.session_state.get("tos_ok") == 1
    assert st.session_state.get("tos_date") == "2026-09-17"


def test_the_gate_records_only_behind_the_tick():
    gate = APP[APP.index("if _welcome_gate:"):]
    gate = _code_only(gate[:gate.index("_emit_boot_settled()")])
    assert gate.count("_record_terms_approval()") == 1, (
        "the approval is recorded more than once, or not at all, at the gate")
    assert gate.index('if st.session_state.get("gate_consent"):') < gate.index(
        "_record_terms_approval()"), "the approval is recorded before the tick is read"
    assert _calls("_record_terms_approval") == 1, (
        "something outside the gate records an approval")


def test_cookie_values_are_validated_before_use():
    ns = {"TOS_VERSION": 1}
    exec(_func("_tos_version_from"), ns)
    exec("import datetime as _dt\n" + _func("_tos_date_from"), ns)  # app.py's own alias
    v, d = ns["_tos_version_from"], ns["_tos_date_from"]
    assert v(1) == 1 and v(3) == 3
    for bad in (None, "1", True, -2, 1.5, [1]):
        assert v(bad) == 0, "accepted %r as a terms version" % (bad,)
    assert d("2026-09-17") == "2026-09-17"
    for bad in (None, "", "<b>x</b>", "2026-13-40", "17.09.2026", 20260917):
        assert d(bad) == "", "accepted %r as an approval date" % (bad,)


def test_the_banner_says_what_was_approved_and_when():
    ns = {"TOS_VERSION": 1}
    exec(_func("_tos_banner"), ns)
    ok = ns["_tos_banner"](1, "2026-09-01")
    assert ok["approved"] and ok["title"] == "אישרת את התנאים"
    assert "גרסה 1" in ok["sub"] and "01.09.2026" in ok["sub"], ok["sub"]
    undated = ns["_tos_banner"](1, "")
    assert undated["approved"] and "גרסה 1" in undated["sub"]
    assert "אושרו" not in undated["sub"], "an approval with no day claims one"
    ns["TOS_VERSION"] = 2
    older = ns["_tos_banner"](1, "2026-09-01")
    assert not older["approved"] and "קודמת" in older["title"]
    none = ns["_tos_banner"](0, "")
    assert not none["approved"] and none["title"] == "טרם אישרת את התנאים"


def test_the_approval_rides_the_device_cookie():
    code = _code_only(APP)
    assert '_ck_dict["tos"] = ' in code and '_ck_dict["tosd"] = ' in code, (
        "the approval is not written to the cookie")
    assert '"ok"' not in APP.split("_ck_dict = {")[1].split("}")[0], (
        "the legacy consent flag is still written")
    assert 'st.session_state.setdefault("tos_ok", _tos_version_from(_ck.get("tos")))' in code
    assert 'st.session_state.setdefault("tos_date", _tos_date_from(_ck.get("tosd")))' in code
    # located in the raw text (the end marker is a comment), judged without comments
    probe = _code_only(APP[APP.index("_pd = json.loads"):APP.index("# ── Conversation restore")])
    assert "_tos_version_from(" in probe and "_tos_date_from(" in probe, (
        "the localStorage restore path brings the name back but not the approval")


# -- reversible, but only on purpose ------------------------------------------

def test_the_withdrawal_question_says_what_is_kept():
    """A withdrawal that does not say the name and the history stay reads as a
    wipe. (That it is asked BEFORE anything happens is pinned by AST in
    test_compliance_screens.py: this file's text-order version of that check
    missed an `st.button(...) or _confirm_action(...)` bypass — mutation M16.)"""
    calls = [n for n in ast.walk(TREE) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Name) and n.func.id == "_confirm_action"
             and n.args and isinstance(n.args[0], ast.Constant)
             and n.args[0].value == "tos_revoke"]
    assert len(calls) == 1, "the withdrawal is not behind exactly one question"
    kw = {k.arg: ast.literal_eval(k.value) for k in calls[0].keywords}
    assert kw.get("title") == "לבטל את אישור התנאים?", "the question asks nothing"
    assert "נשמרים" in kw.get("body", ""), "the question does not say what is kept"


if __name__ == "__main__":
    failures = 0
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            try:
                _fn()
                print("PASS", _name)
            except Exception as exc:  # a missing def is a failure, not a crash
                failures += 1
                print("FAIL", _name, "-",
                      ("%s: %s" % (type(exc).__name__, exc))[:160]
                      .encode("ascii", "replace").decode())
    sys.exit(1 if failures else 0)
