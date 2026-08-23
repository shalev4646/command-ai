# -*- coding: utf-8 -*-
"""night.report_goal's credit rule. The number it produces is the one the user
will read as "how close are we to the goal", so every way of inflating it is
pinned here: crediting a silence nobody adjudicated, reading a per-question
verdict as per-part, crediting a fabrication, or counting an answer that names
a body without sending the soldier to it."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from night import report_goal as G


def _row(qid, parts, answered, level="led_known", answer=""):
    return {"id": qid, "answer": answer,
            "grade": {"level": level, "parts": parts, "answered_parts": answered}}


def test_zero_without_a_verdict_earns_nothing():
    rows = [_row("q1", ["a", "b"], 0)]
    d = G.tally(rows, {})
    assert d["strict"] == 0 and d["goal"] == 0, d
    assert d["uncredited"] == ["q1"], d


def test_adjudicated_zero_is_credited():
    rows = [_row("q1", ["a", "b"], 0)]
    for verdict in G.UNANSWERABLE:
        d = G.tally(rows, {"q1": verdict})
        assert d["goal"] == 2 and d["credited"] == ["q1"], (verdict, d)


def test_a_verdict_the_pass_did_not_write_earns_nothing():
    rows = [_row("q1", ["a", "b"], 0)]
    d = G.tally(rows, {"q1": "ANSWERED_IN_CORPUS_IN_BLOCK"})
    assert d["goal"] == 0, d


def test_partial_answers_keep_their_unanswered_half_uncredited():
    """The verdict was written about a question that scored zero. Applying it to
    the unanswered half of a partial answer reads it as if it were per-part."""
    rows = [_row("q1", ["a", "b"], 1)]
    d = G.tally(rows, {"q1": "MISSING"})
    assert d["strict"] == 1 and d["goal"] == 1, d
    assert d["credited"] == [] and d["uncredited"] == [], d


def test_fabrication_is_not_an_honest_negative():
    rows = [_row("q1", ["a"], 0, level="invented")]
    d = G.tally(rows, {"q1": "MISSING"})
    assert d["goal"] == 0, d


def test_goal_never_exceeds_the_denominator():
    rows = [_row("q1", ["a", "b"], 0), _row("q2", ["c"], 1)]
    d = G.tally(rows, {"q1": "MISSING", "q2": "MISSING"})
    assert d["strict"] <= d["goal"] <= d["total"], d


def test_naming_a_body_is_not_a_referral():
    named = _row("q1", ["a"], 0, answer='הסמכות היא מפקד היחידה לפי הפקודה.')
    sent = _row("q2", ["a"], 0, answer='המידע לא קיים. יש לפנות למדור ת"ש ביחידה.')
    d = G.tally([named, sent], {"q1": "MISSING", "q2": "MISSING"})
    assert d["credited"] == ["q1", "q2"], d
    assert d["referred"] == 1, d


def test_verdicts_read_only_files_that_carry_ids():
    """adjudication.json (the first pass) is keyed by question prose. If it ever
    starts contributing, ids collided with prose and the credit is wrong."""
    v = G.verdicts()
    assert v, "no adjudication verdicts on disk — the credit rule cannot run"
    assert all(isinstance(k, str) and k.startswith("q") for k in v), sorted(v)[:5]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
    print("all report_goal tests passed")
