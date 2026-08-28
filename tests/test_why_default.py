# -*- coding: utf-8 -*-
"""night.why_default separates a masked retrieval failure from a real silence.

The generic door fires on 95% of fresh zeros, so it hides both. Everything that
could turn this triage back into a confident-sounding guess is pinned here: the
module claiming "no such rule", the threshold drifting off the labelled data it
was calibrated on, and the score being read off text the app never produced.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import scope_routes
from night import why_default as W

MARK = scope_routes.MARK_MISSING


def _clause(number, text):
    return {"number": number, "text": text}


def _doc(did, clauses):
    return {"document_id": did, "sections": [{"id": "k", "title": "t", "clauses": clauses}]}


_REAL = None


def real_index():
    """The production index. The threshold is calibrated against ITS idf scale,
    so every assertion about COVERED/UNCLEAR has to run here -- see
    `test_the_score_carries_the_index_scale` for what a toy index does to it."""
    global _REAL
    if _REAL is None:
        _REAL = W.build_index(W._load_corpus())
    return _REAL


def _index():
    return W.build_index([
        _doc("36.0401", [_clause(
            "מכסת חופשה שנתית",
            "חייל בשירות קבע זכאי למכסת ימי חופשה שנתית הנקבעת לפי ותק; "
            "מניין ימי החופשה השנתית וחישובה ייעשו לפי טבלת הוותק שבנספח.")]),
        _doc("61.0104", [_clause(
            "טיפול רפואי בחייל",
            "חייל הפונה לקבלת טיפול רפואי יופנה למרפאת היחידה; הרופא הצבאי "
            "יקבע את המשך הטיפול ואת ההפניה למומחה לפי הצורך.")]),
    ])


def test_it_never_says_there_is_no_rule():
    """A low score is a lean, not a finding: on the arbitrated 107 the bottom of
    the ranking was 53% NO_SUCH_RULE against a 31% base rate. A module that
    turned that into a verdict would launder a coin flip into a decision."""
    for s in (0.0, 0.01, 0.05, 0.09, 0.1299):
        assert W.verdict(s) == "UNCLEAR", s
    for s in (0.130, 0.2, 1.0):
        assert W.verdict(s) == "COVERED", s
    assert "NO_SUCH_RULE" not in {W.verdict(s / 100) for s in range(0, 101)}


def test_the_threshold_is_the_calibrated_one():
    """0.130 bought 94.7% precision at 48.6% coverage on pilot-150. Moving it
    silently would move every number this module reports, so it is pinned to
    the value the calibration produced."""
    assert W.COVERED_AT == 0.130
    assert W.NGRAM == 4, "3-grams scored AUC 0.717 and 5-grams 0.700"


def test_a_declaration_finds_the_clause_that_covers_it():
    """The real question the fresh set produced, against the real corpus: the
    app said it lacked the annual leave quota, and 36.0401 carries it."""
    s, doc = W.score("מכסת ימי החופשה השנתית שמגיעה למשרת מילואים ואופן חישובה.",
                     real_index())
    assert doc == "36.0401", (s, doc)
    assert W.verdict(s) == "COVERED", s


def test_a_declaration_about_something_absent_stays_unclear():
    """Tattoos occur zero times in 3.24M characters of orders, verified on
    2026-08-28. Nothing can cover it, so nothing may be flagged as covered."""
    s, _ = W.score("הכללים בדבר קעקועים גלויים בידיים ובצוואר של חייל.", real_index())
    assert W.verdict(s) == "UNCLEAR", s


def test_the_score_carries_the_index_scale():
    """Same declaration, two indexes. The toy one collapses every weight to
    MIN_IDF, so the number drops far below a threshold calibrated on 2,389
    clauses. Pinned because reading a toy score against COVERED_AT would call
    every finding UNCLEAR and look like the corpus covers nothing."""
    decl = "מכסת ימי החופשה השנתית שמגיעה לחייל ואופן חישובה."
    big, big_doc = W.score(decl, real_index())
    small, small_doc = W.score(decl, _index())
    assert big_doc == small_doc == "36.0401", (big_doc, small_doc)
    assert small < big / 10, (small, big)


def test_hebrew_prefixes_and_plurals_do_not_break_the_match():
    """Why 4-grams and not words: a word-level probe run on 2026-08-28 drowned
    in exactly this. 'החופשה' and 'חופשות' share no token and plenty of grams."""
    idx = real_index()
    plain, _ = W.score("מכסת ימי חופשה שנתית לחייל.", idx)
    inflected, _ = W.score("המכסה של ימי החופשות השנתיות לחיילים.", idx)
    assert inflected > 0.5 * plain, (plain, inflected)


def test_an_answer_that_claimed_no_lack_yields_no_declaration():
    assert W.declaration_of("הפקודה קובעת שלושים יום. אין חוסר.") == ""
    assert W.declaration_of("") == ""


def test_the_declaration_is_the_text_after_the_marker():
    d = W.declaration_of("גוף התשובה כאן. " + MARK + " מכסת ימי החופשה השנתית.")
    assert d == "מכסת ימי החופשה השנתית."
    assert "גוף התשובה" not in d


def test_triage_skips_answered_questions_and_ranks_by_score():
    rows = [
        {"id": "a", "clean_q": "?", "sources": [],
         "answer": MARK + " מכסת ימי החופשה השנתית וחישובה.",
         "grade": {"parts": ["x"], "answered_parts": 0}},
        {"id": "b", "clean_q": "?", "sources": [],
         "answer": MARK + " קעקועים גלויים.",
         "grade": {"parts": ["x"], "answered_parts": 0}},
        {"id": "c", "clean_q": "?", "sources": [],
         "answer": MARK + " מכסת ימי החופשה השנתית וחישובה.",
         "grade": {"parts": ["x"], "answered_parts": 1}},
    ]
    out = W.triage(rows, real_index())
    assert [d["id"] for d in out] == ["a", "b"], out
    assert out[0]["score"] >= out[1]["score"]
    assert out[0]["verdict"] == "COVERED" and out[1]["verdict"] == "UNCLEAR"


def test_in_window_separates_block_depth_from_a_retrieval_miss():
    """Same declaration, same best document -- the only difference is whether
    the window already held it. That flag is what tells the two levers apart."""
    row = {"id": "a", "clean_q": "?", "answer": MARK + " מכסת ימי החופשה השנתית וחישובה.",
           "grade": {"parts": ["x"], "answered_parts": 0}}
    idx = real_index()
    missed = W.triage([{**row, "sources": ["31.0117"]}], idx)[0]
    shown = W.triage([{**row, "sources": ["36.0401"]}], idx)[0]
    assert missed["in_window"] is False and shown["in_window"] is True


def test_the_module_is_pure():
    """No Streamlit, no anthropic, no network: this has to run in the free
    measurement pass, and a paid import would put it behind the API budget."""
    src = (Path(__file__).resolve().parents[1] / "night" / "why_default.py").read_text(encoding="utf-8")
    for banned in ("import streamlit", "import anthropic", "from anthropic", "requests."):
        assert banned not in src, banned


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
    print("all why_default tests passed")
