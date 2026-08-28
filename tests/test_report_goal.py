# -*- coding: utf-8 -*-
"""night.report_goal's credit rule. The number it produces is the one the user
will read as "how close are we to the goal", so every way of inflating it is
pinned here: crediting a silence nobody adjudicated, reading a per-question
verdict as per-part, crediting a fabrication, or counting an answer that names
a body without sending the soldier to it."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import scope_routes
from night import report_goal as G


MARK = scope_routes.MARK_MISSING


def _row(qid, parts, answered, level="led_known", answer="", question=""):
    return {"id": qid, "answer": answer, "clean_q": question, "q": question,
            "question": question,
            "grade": {"level": level, "parts": parts, "answered_parts": answered}}


def test_zero_without_a_verdict_earns_nothing():
    rows = [_row("q1", ["a", "b"], 0)]
    d = G.tally(rows, {})
    assert d["strict"] == 0 and d["goal"] == 0, d
    assert d["uncredited"] == ["q1"], d


def test_adjudicated_zero_is_credited_but_credit_alone_is_not_the_goal():
    """Being right to say nothing earns `credited`. Reaching the soldier's next
    step is a second, separate hurdle -- so a credited answer that rendered no
    strip at all scores zero on both `served` and `goal`."""
    for verdict in G.UNANSWERABLE:
        d = G.tally([_row("q1", ["a", "b"], 0)], {"q1": verdict})
        assert d["credited"] == ["q1"] and d["credited_parts"] == 2, (verdict, d)
        assert d["goal"] == 0 and d["stranded"] == ["q1"], (verdict, d)

        marked = _row("q1", ["a", "b"], 0, answer=MARK + " כלל.", question="שאלה?")
        d = G.tally([marked], {"q1": verdict})
        assert d["goal"] == 2, (verdict, d)


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
    rows = [_row("q1", ["a", "b"], 0, answer=MARK + " כלל.", question="שאלה?"),
            _row("q2", ["c"], 1)]
    d = G.tally(rows, {"q1": "MISSING", "q2": "MISSING"})
    assert d["strict"] <= d["goal"] <= d["total"], d


def test_the_two_passes_spell_the_same_verdict_differently():
    """adjudication2/3 wrote MISSING; the pilot-150 pass wrote NOT_IN_CORPUS for
    the same thing -- the answering rule exists and the document is not ours.
    Reading only one name charged the app for 12 questions an arbitration had
    already cleared it of."""
    rows = [_row("q1", ["a"], 0, answer=MARK + " כלל.", question="שאלה?")]
    assert G.tally(rows, {"q1": "NOT_IN_CORPUS"})["goal"] == 1
    assert G.tally(rows, {"q1": "MISSING"})["goal"] == 1


def test_an_answer_without_a_routing_marker_reaches_no_door():
    """Gate one is the app's: no marker in the answer, no strip is rendered,
    so nothing is served however helpful the prose sounds."""
    rows = [_row("q1", ["a", "b"], 0, answer="פנה למפקד הישיר שלך.")]
    d = G.tally(rows, {"q1": "NO_SUCH_RULE"})
    assert d["served"] == 0 and d["goal"] == 0, d
    assert d["stranded"] == ["q1"], d


def test_the_catch_all_door_is_counted_apart_from_a_verified_one():
    """It names no address -- it states procedure. Crediting it as a real
    referral would hide the number `out_of_scope` asked to be watched."""
    rows = [_row("q1", ["a", "b"], 0, answer=MARK + " כלל כלשהו.",
                 question="מי קובע מתי משדרים את ההתרעה?")]
    d = G.tally(rows, {"q1": "NO_SUCH_RULE"})
    assert d["served"] == 0 and d["goal"] == 2, d
    assert d["defaulted"] == ["q1"] and d["stranded"] == [], d


def test_a_verified_family_door_is_served():
    rows = [_row("q1", ["a", "b"], 0, answer=MARK + " כלל כלשהו.",
                 question="אפשר לצבור חופשה?")]
    d = G.tally(rows, {"q1": "NO_SUCH_RULE"})
    assert d["served"] == 2 and d["goal"] == 2, d
    assert d["defaulted"] == [] and d["referred"] == 1, d


def test_served_never_exceeds_goal_and_never_undercuts_strict():
    rows = [_row("q1", ["a", "b"], 0, answer="אין כלל."),
            _row("q2", ["c"], 1),
            _row("q3", ["d"], 0, answer=MARK + " כלל.", question="שאלה כלשהי?"),
            _row("q4", ["e"], 0, answer=MARK + " כלל.",
                 question="אפשר לצבור חופשה?")]
    d = G.tally(rows, {"q1": "NO_SUCH_RULE", "q3": "NOT_IN_CORPUS",
                       "q4": "NO_SUCH_RULE"})
    assert d["strict"] <= d["served"] <= d["goal"] <= d["total"], d
    assert d["stranded"] == ["q1"] and d["defaulted"] == ["q3"], d


def test_the_strip_is_not_in_the_answer_text():
    """The referral is rendered by app.py from `out_of_scope`, and probe.py
    measures `stream_ai_answer`, which never sees it. Reading the answer's
    prose for it reported 6 of 45 on pilot-150 where the gate fires on all 45.
    """
    prose = _row("q1", ["a"], 0, answer='המידע לא קיים. יש לפנות למדור ת"ש.')
    assert G.door(prose["answer"], prose["question"]) is None
    gated = _row("q2", ["a"], 0, answer=MARK + " כלל.", question="שאלה כלשהי?")
    assert G.door(gated["answer"], gated["question"]) == G.DEFAULT_FAMILY


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
