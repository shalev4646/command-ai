# -*- coding: utf-8 -*-
"""The terms-approval gate: what it opens for, and what survives what.

Run: venv\\Scripts\\python.exe tests\\test_terms_gate.py
Prints only ASCII (cp1252 console pitfall).

These are behaviour tests, not text checks. app.py cannot be imported without
Streamlit and a corpus, so the three identity functions and the two gate
expressions are LIFTED OUT OF THE FILE and executed against a fake
session_state. Lifting rather than restating them is the point: a test that
re-types the condition it is guarding passes forever after the real one drifts.

What is being pinned, and why each one is here rather than assumed:

  logout keeps the approval   -- the terms belong to the device, not to the
                                 person signing out, so the card reopens with
                                 the box already ticked (the user's ask).
  the wipe clears it          -- מחק הכל promises a device at defaults, and a
                                 device at defaults has approved nothing.
                                 Leaving it would put "אישרת את התנאים" on the
                                 אודות screen of a just-reset device -- the
                                 exact false claim that banner was rewritten
                                 to stop making.
  a withdrawal reopens        -- "ביטול אישור התנאים" only zeroes tos_ok. If
                                 the gate did not read tos_ok the button would
                                 be decorative and the person would carry on
                                 using an app they had just un-agreed to.
  a version bump reopens      -- same clause. Without it TOS_VERSION would
                                 move the banner to "אישרת גרסה קודמת" and
                                 never once re-ask.
  a new device starts empty   -- a pre-ticked consent box is not consent. It
                                 may only ever DISPLAY an approval already on
                                 record for this device.
"""
import re
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = (ROOT / "app.py").read_text(encoding="utf-8")


def _balanced(after: str) -> str:
    """The parenthesised expression that follows `after` in app.py."""
    i = APP.index(after) + len(after)
    depth = 0
    for j in range(i, len(APP)):
        if APP[j] == "(":
            depth += 1
        elif APP[j] == ")":
            depth -= 1
            if depth == 0:
                return APP[i:j + 1]
    raise AssertionError("unbalanced expression after " + after)


def _func(name: str) -> str:
    a = APP.index("def %s(" % name)
    m = re.search(r"\n\ndef ", APP[a:])
    return APP[a:a + m.start()]


GATE_SRC = _balanced("_name_gate = ")
SEED_SRC = _balanced("st.session_state.gate_tos_w = ")


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
    """A fresh fake app: session_state plus the real identity functions."""
    st = types.SimpleNamespace(session_state=_SS())
    metrics = types.SimpleNamespace(new_session_id=lambda: "ROTATED-ID")
    ns = {"st": st, "metrics": metrics}
    for fn in ("_clear_history", "_reset_identity", "_wipe_all"):
        exec(_func(fn), ns)
    env = {"st": st, "int": int, "TOS_VERSION": tos_version}
    return st, ns, env


def _gate(env):
    return bool(eval(GATE_SRC, dict(env)))


def _box(st, env):
    """What the consent checkbox shows: a live widget value, else the seed."""
    if "gate_tos_w" in st.session_state:
        return bool(st.session_state["gate_tos_w"])
    return bool(eval(SEED_SRC, dict(env)))


def _signed_in(st, version=1):
    st.session_state.clear()
    st.session_state.update(dict(
        role="soldier", name_asked=True, tos_ok=version, profile_name="Dana",
        gate_name_w="Dana", gate_tos_w=True, mil_saved=True, mil_salary=12000,
        device_id="DEVICE-1", conversation_history=[1], messages=[2],
        profile_saved=["x"], profile_customized=True))


# -- the gate opens for the terms, not only for the name ---------------------

def test_a_new_device_meets_the_gate_with_an_empty_box():
    st, _, env = _world()
    assert _gate(env), "a device that approved nothing must meet the gate"
    assert not _box(st, env), (
        "the consent box is pre-ticked on a fresh device -- a pre-ticked "
        "terms box is not consent"
    )


def test_withdrawing_the_approval_reopens_the_gate():
    """ביטול אישור התנאים only zeroes tos_ok; the gate is what enforces it."""
    st, _, env = _world()
    _signed_in(st)
    assert not _gate(env), "an approved device should be past the gate"
    st.session_state.tos_ok = 0            # exactly what the revoke button does
    st.session_state.pop("gate_tos_w", None)
    assert _gate(env), (
        "withdrawing the approval left the app usable -- the revoke button is "
        "decorative unless the gate reads tos_ok"
    )
    assert not _box(st, env), "the box must reseed empty after a withdrawal"


def test_a_version_bump_asks_again():
    """Otherwise TOS_VERSION is a number that moves a banner and nothing else."""
    st, _, env_old = _world(tos_version=1)
    _signed_in(st, version=1)
    assert not _gate(env_old)
    _, _, env_new = _world(tos_version=2)
    env_new["st"] = types.SimpleNamespace(session_state=st.session_state)
    assert _gate(env_new), (
        "a device holding an older approved version was not asked again"
    )


def test_the_name_survives_a_terms_re_approval():
    """The card reopens for people who already told us their name."""
    st, _, _ = _world()
    _signed_in(st)
    st.session_state.tos_ok = 0
    st.session_state.pop("gate_tos_w", None)
    assert st.session_state.get("profile_name") == "Dana", (
        "a terms re-approval wiped the name; withdrawing consent is not a "
        "sign-out"
    )
    assert "st.session_state.get(\"profile_name\") or \"\"" in APP, (
        "the gate no longer seeds gate_name_w from profile_name"
    )


# -- what each reset does and does not take with it --------------------------

def test_logout_keeps_the_terms_approval_and_the_device_id():
    st, ns, env = _world()
    _signed_in(st)
    ns["_clear_history"]()
    ns["_reset_identity"]()
    assert st.session_state.get("tos_ok") == 1, (
        "logout cleared the terms approval -- the device did approve them and "
        "still has"
    )
    assert _gate(env), "logout must land on the name + terms card"
    assert _box(st, env), (
        "the box is empty after a logout; it should display the approval this "
        "device already holds"
    )
    assert st.session_state.get("device_id") == "DEVICE-1", (
        "logout rotated the analytics id -- logging out is the same device"
    )


def test_logout_still_forgets_the_person():
    """The approval surviving must not quietly keep anything else."""
    st, ns, _ = _world()
    _signed_in(st)
    ns["_reset_identity"]()
    assert not st.session_state.get("profile_name"), "the name survived logout"
    assert st.session_state.get("role") is None, "the role survived logout"
    assert "mil_salary" not in st.session_state, (
        "a sign-out that leaves a salary behind is not a sign-out"
    )


def test_the_wipe_clears_the_terms_approval_and_rotates_the_id():
    st, ns, env = _world()
    _signed_in(st)
    ns["_wipe_all"]()
    assert st.session_state.get("tos_ok") == 0, (
        "מחק הכל left the terms approved -- the אודות banner would then claim "
        "an approval on a device the person just reset"
    )
    assert not _box(st, env), "the box must be empty on a wiped device"
    assert st.session_state.get("device_id") == "ROTATED-ID", (
        "the wipe stopped rotating the analytics id"
    )


# -- the approval has to be reachable and reversible -------------------------

def test_the_consent_widget_reseeds_after_a_reset():
    """Dropping the widget key is what makes the box follow tos_ok at all."""
    assert '"gate_tos_w",' in _func("_reset_identity"), (
        "gate_tos_w left the widget-key reset list; the box can now carry a "
        "stale tick across a logout or a wipe"
    )


def test_the_gate_cannot_be_passed_without_ticking():
    gate = APP[APP.index("if _name_gate:"):]
    gate = gate[:gate.index("_emit_boot_settled()")]
    assert 'if not st.session_state.get("gate_tos_w"):' in gate, (
        "the gate no longer checks the consent box before letting anyone past"
    )
    assert "st.session_state.tos_ok = TOS_VERSION" in gate, (
        "passing the gate no longer records the approval"
    )


def test_withdrawing_is_confirmed_before_it_happens():
    """Pins WHERE the withdrawal happens, not merely that a confirm exists.

    Asserting that the string "tos_revoke_ask" appears is not enough: leaving
    that branch in place while the opening button zeroes tos_ok itself is a
    one-tap withdrawal with a dead confirm attached, and it reads identically.
    So this walks the source order -- the zeroing must sit after the
    confirming button and before the opener that only raises the question.
    (Verified by mutation: wiring the opener straight to tos_ok = 0 fails
    here, and passed the presence check it replaced.)
    """
    about = _func("_settings_about")
    assert about.count("st.session_state.tos_ok = 0") == 1, (
        "tos_ok is zeroed in more than one place in the אודות screen; at "
        "least one of them is not behind the confirm"
    )
    zeroed = about.index("st.session_state.tos_ok = 0")
    confirmed = about.index('key="danger_tos_revoke_yes"')
    opener = about.index('key="danger_tos_revoke"')
    assert confirmed < zeroed, (
        "the approval is withdrawn before the confirming button -- the "
        "are-you-sure step is decorative"
    )
    assert zeroed < opener, (
        "the opening button withdraws the approval itself; it may only raise "
        "the question"
    )
    assert "לבטל את אישור התנאים?" in about, "the confirm asks nothing"
    assert "נשמרים" in about, (
        "the confirm does not say what is KEPT, which makes it read as a wipe"
    )


if __name__ == "__main__":
    failures = 0
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            try:
                _fn()
                print("PASS", _name)
            except AssertionError as exc:
                failures += 1
                # ASCII-only: an assertion message may quote Hebrew source
                print("FAIL", _name, "-",
                      str(exc).encode("ascii", "replace").decode())
            except Exception as exc:
                failures += 1
                print("FAIL", _name, "-", f"{type(exc).__name__}: {exc}"[:140])
    sys.exit(1 if failures else 0)
