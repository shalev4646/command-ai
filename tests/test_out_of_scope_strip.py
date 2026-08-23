# -*- coding: utf-8 -*-
"""The out-of-scope strip's wiring in app.py.

app.py cannot be imported outside Streamlit, so this reads its source — the
same technique tests/test_compliance_screens.py uses. Source assertions are
weaker than behavioural ones and are worth it only for invariants that a
future edit could silently break, which is exactly the case here: the strip
must REPLACE the generic chain, and the moment someone turns the `else` into
a second call the soldier gets two contradictory "where to turn" rows."""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import out_of_scope  # noqa: F401  — the strip is dead code without it

SRC = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")


def test_the_module_is_imported_fail_closed():
    """Same defensive shape as the sibling deterministic tools: a stale cached
    cloud build pairing a new app.py with an older tree hides the strip
    instead of crashing the answer."""
    assert "import out_of_scope as _oos" in SRC
    assert re.search(r"import out_of_scope as _oos\s*\nexcept Exception:\s*\n\s*_oos = None", SRC)


def test_the_strip_replaces_the_generic_chain_and_never_stacks_on_it():
    m = re.search(r"_oos_dest = _out_of_scope_destination\(.*\n"
                  r"\s*if _oos_dest:\s*\n"
                  r"\s*_out_of_scope_strip\(_oos_dest\)\s*\n"
                  r"\s*else:\s*\n"
                  r"\s*_escalation_strip\(", SRC)
    assert m, "the escalation chain must sit in the else branch, not beside it"
    assert SRC.count("_escalation_strip(msg.get(") == 1, \
        "more than one call site — the replace-not-stack invariant is unenforceable"


def test_both_gates_are_present():
    """Marker in the ANSWER and a verified family for the QUESTION. Dropping
    either one puts a referral under an answer that did resolve the question."""
    fn = SRC[SRC.index("def _out_of_scope_destination"):
             SRC.index("def _out_of_scope_strip")]
    assert "_MARK_MISS not in content and _MARK_OOS not in content" in fn
    assert "destination_for" in fn
    assert "_oos is None" in fn


def test_the_rendered_strip_escapes_everything_it_prints():
    """The iron rule app._answer_actions already lives by. The values are ours
    and not the model's today, but a future family could carry a quoted order
    title, and one unescaped '<' is an injected tag."""
    fn = SRC[SRC.index("def _out_of_scope_strip"):SRC.index("def _escalation_strip")]
    for field in ("dest['label']", "dest['where']", "dest['why']"):
        assert f"html.escape({field})" in fn, field
    assert "rel='noopener noreferrer'" in fn, "an external link needs the opener guard"


def test_the_link_style_exists():
    assert ".cai-escal-link" in SRC


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
    print("all out-of-scope strip tests passed")
