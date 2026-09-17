# -*- coding: utf-8 -*-
"""The clause-title path (RETRIEVE_V2): storage/clause_index.py and
backend.extend_with_clause_index. Ships OFF; these tests pin what it does
when turned on, and that OFF is byte-identical to before.

Why it exists: the answering ORDER reaches the window three times more often
than the answering CLAUSE (18/59 vs 5/59). The curator wrote every clause's
title in soldier language; this path matches the question to those titles
and serves the order's block, so "right order, wrong clause" cannot happen to
an order served whole. Numbers: night/titleprobe.py.

    venv\\Scripts\\python.exe tests\\test_clause_index.py
"""
import sys
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import backend
from night import why_default as W
from storage import clause_index as ci

# three curated orders shaped like the store's: the answering clause of the
# jewellery question is the SECOND clause of the second order
DOC_A = {"document_id": "A.1", "title": "מסדר בוקר", "roles": ["soldier"],
         "sections": [{"id": "key-facts", "title": "עיקרי הפקודה", "clauses": [
             {"number": "מתי מתקיים מסדר בוקר", "text": "מסדר הבוקר יתקיים בכל יום בתחילת יום הפעילות."},
         ]}]}
DOC_B = {"document_id": "B.2", "title": "הופעה ולבוש", "roles": ["soldier"],
         "sections": [{"id": "key-facts", "title": "עיקרי הפקודה", "clauses": [
             {"number": "מה חובת הגילוח", "text": "חייל יתגלח מדי יום אלא אם קיבל פטור גילוח."},
             {"number": "אילו תכשיטים מותר לענוד עם מדים",
              "text": "עם מדים מותר לענוד טבעת נישואין בלבד; שרשרת תוסתר מתחת למדים."},
             {"number": "מה דין שיער ארוך", "text": "שיער החייל יהיה קצר ומסודר."},
         ]}]}
DOC_C = {"document_id": "C.3", "title": "טיפול רפואי", "roles": ["reserve"],
         "sections": [{"id": "key-facts", "title": "עיקרי הפקודה", "clauses": [
             {"number": "אילו תכשיטים מותר לענוד בטיפול רפואי", "text": "בטיפול רפואי יוסרו התכשיטים."},
         ]}]}
UNCURATED = {"document_id": "D.4", "title": "פקודה בלי בלוק", "roles": ["soldier"],
             "sections": [{"id": "chunk0", "title": "", "clauses": [
                 {"number": "1", "text": "טקסט גולמי בלבד עם תכשיטים ומדים."}]}]}
BIG = {"document_id": "E.5", "title": "פקודה ענקית", "roles": ["soldier"],
       "sections": [{"id": "key-facts", "title": "עיקרי הפקודה", "clauses": [
           {"number": f"סעיף מספר {i} על נושא {i}", "text": " ".join(["מילה"] * 100)} for i in range(10)
       ] + [{"number": "מה עונש המחבוש המרבי לחייל", "text": "עד שבעה ימי מחבוש בסמכות מפקד יחידה."}]}]}
Q = "יש לי טבעת, מותר לענוד תכשיטים עם מדים?"


@contextmanager
def _with(flag: int, docs: list[dict], route=None, **knobs):
    old = {k: getattr(backend, k) for k in
           ("RETRIEVE_V2", "RETRIEVE_V2_BLOCK_WORDS", "RETRIEVE_V2_TOP_K", "RETRIEVE_V2_ROUTE_BONUS")}
    old_load, old_index = backend.load_documents, backend._v2_index
    backend.RETRIEVE_V2 = flag
    for k, v in knobs.items():
        setattr(backend, k, v)
    backend.load_documents = lambda: docs
    backend._v2_index = None
    try:
        yield
    finally:
        for k, v in old.items():
            setattr(backend, k, v)
        backend.load_documents, backend._v2_index = old_load, old_index


def _window():
    return [{"doc_id": "A.1", "title": "מסדר בוקר", "section": "key-facts",
             "clause": "מתי מתקיים מסדר בוקר", "text": "מסדר בוקר — עיקרי הפקודה\nסעיף מתי מתקיים מסדר בוקר: ...",
             "score": 0.4}]


def test_the_grams_match_the_calibrated_instrument():
    for text in (Q, "סעיף 12: המפקד (או מי שהוסמך לכך) — ״טופס 102״", "  רווחים   כפולים \n ושורות"):
        assert ci.grams(text) == W.grams(text) == backend._lack_grams(text), text


def test_the_question_finds_the_clause_by_its_title():
    idx = ci.ClauseIndex([DOC_A, DOC_B, DOC_C])
    hits = idx.rank(Q, doc_ids={"A.1", "B.2"})
    assert hits.clauses[0][1:] == ("B.2", "key-facts", "אילו תכשיטים מותר לענוד עם מדים"), hits.clauses[:2]
    assert hits.docs[0][1] == "B.2"
    assert all(d != "C.3" for _, d in hits.docs), "doc_ids scopes the ranking"


def test_only_the_titles_are_indexed_by_default():
    """Measured 2026-09-11: every unit kind added to the titles lost ground
    (order in top-2: 20 → 16 → 15 → 14 of 83). The default is the titles."""
    idx = ci.ClauseIndex([DOC_A, DOC_B, DOC_C, UNCURATED])
    st = idx.stats()
    assert set(st) - {"units", "docs"} == {"title"}, st
    assert st["docs"] == 3, "an uncurated order has no block to serve and nothing to index"


def test_anchors_and_order_titles_are_available_for_the_ablation():
    anchors = {"B.2": {"הופעה ולבוש מותר לי טבעת בבסיס": ["key-facts", "אילו תכשיטים מותר לענוד עם מדים"],
                       "הופעה ולבוש שאלה על סעיף שאינו קיים": ["key-facts", "אין כזה"]}}
    idx = ci.ClauseIndex([DOC_B], anchors=anchors, kinds=ci.ClauseIndex.ALL_KINDS)
    st = idx.stats()
    assert st["anchor"] == 1 and st["doc_title"] == 1, st
    hits = idx.rank("מותר לי טבעת בבסיס?")
    assert hits.clauses[0][3] == "אילו תכשיטים מותר לענוד עם מדים", "the anchor votes for ITS clause"


def test_off_is_a_no_op():
    win = _window()
    with _with(0, [DOC_A, DOC_B]):
        out = backend.extend_with_clause_index(win, Q, "soldier")
    assert out is win


def test_the_best_order_is_served_whole_and_appended():
    win = _window()
    with _with(1, [DOC_A, DOC_B]):
        out = backend.extend_with_clause_index(win, Q, "soldier")
    assert out[:1] == win, "appends only — the ranking is untouched"
    served = [(c["doc_id"], c["clause"]) for c in out[1:]]
    assert served == [("B.2", "מה חובת הגילוח"), ("B.2", "אילו תכשיטים מותר לענוד עם מדים"),
                      ("B.2", "מה דין שיער ארוך")], served
    assert out[1]["text"].startswith("הופעה ולבוש — עיקרי הפקודה\nסעיף "), "shaped like the index's own chunks"
    jewel = next(c for c in out if c["clause"] == "אילו תכשיטים מותר לענוד עם מדים")
    assert jewel["score"] > out[1]["score"] == 0.0, "a clause carries its own evidence, the rest ride the block"


def test_the_value_is_how_many_orders_and_an_order_without_evidence_gets_no_seat():
    both = "יש לי טבעת עם מדים, ומתי מתקיים מסדר הבוקר?"
    with _with(2, [DOC_A, DOC_B]):
        out = backend.extend_with_clause_index([], both, "soldier")
    docs = {c["doc_id"] for c in out}
    assert docs == {"B.2", "A.1"}, docs
    with _with(1, [DOC_A, DOC_B]):
        out = backend.extend_with_clause_index([], both, "soldier")
    assert len({c["doc_id"] for c in out}) == 1, "the value caps the orders"
    with _with(2, [DOC_A, DOC_B]):
        out = backend.extend_with_clause_index([], Q, "soldier")
    assert {c["doc_id"] for c in out} == {"B.2"}, "A.1 shares no 4-gram with the question: no evidence, no seat"


def test_a_clause_already_in_the_window_is_not_served_twice():
    win = _window() + [{"doc_id": "B.2", "title": "הופעה ולבוש", "section": "key-facts",
                        "clause": "מה חובת הגילוח", "text": "...", "score": 0.3}]
    with _with(1, [DOC_A, DOC_B]):
        out = backend.extend_with_clause_index(win, Q, "soldier")
    keys = [(c["doc_id"], c["section"], c["clause"]) for c in out]
    assert len(keys) == len(set(keys)), keys


def test_an_oversized_block_is_reduced_to_its_evidenced_clauses_in_block_order():
    with _with(1, [BIG], RETRIEVE_V2_BLOCK_WORDS=600, RETRIEVE_V2_TOP_K=2):
        out = backend.extend_with_clause_index([], "כמה ימי מחבוש מפקד יכול לתת לי, מה העונש המרבי?", "soldier")
    served = [c["clause"] for c in out]
    assert "מה עונש המחבוש המרבי לחייל" in served, served
    assert len(served) <= 2, served
    block_order = [c["clause"] for c in backend._full_block(BIG)]
    assert served == [c for c in block_order if c in served], "block order, not score order"


def test_a_block_that_fits_is_served_whole_even_when_only_one_clause_has_evidence():
    with _with(1, [DOC_B], RETRIEVE_V2_BLOCK_WORDS=600):
        out = backend.extend_with_clause_index([], "מה חובת הגילוח?", "soldier")
    assert len(out) == 3, [c["clause"] for c in out]


def test_the_scope_is_the_role_and_the_curated_orders():
    with _with(2, [DOC_A, DOC_B, DOC_C, UNCURATED]):
        out = backend.extend_with_clause_index([], Q, "soldier")
    assert {c["doc_id"] for c in out} <= {"A.1", "B.2"}, {c["doc_id"] for c in out}
    with _with(2, [DOC_A, DOC_B, DOC_C]):
        out = backend.extend_with_clause_index([], "תכשיטים בטיפול רפואי", "reserve")
    assert {c["doc_id"] for c in out} == {"C.3"}, {c["doc_id"] for c in out}


def test_the_router_shortlist_is_a_bonus_never_a_scope():
    """A routed order within the bonus of the leader takes the seat; an order
    outside the role's curated scope gets nothing from the router."""
    with _with(1, [DOC_A, DOC_B, DOC_C], RETRIEVE_V2_ROUTE_BONUS=1.0):
        out = backend.extend_with_clause_index([], Q, "soldier", route={"A.1"})
    assert out[0]["doc_id"] == "A.1", [c["doc_id"] for c in out]
    with _with(1, [DOC_A, DOC_B, DOC_C], RETRIEVE_V2_ROUTE_BONUS=1.0):
        out = backend.extend_with_clause_index([], Q, "soldier", route={"C.3"})
    assert out and out[0]["doc_id"] == "B.2", [c["doc_id"] for c in out]
    with _with(1, [DOC_A, DOC_B, DOC_C], RETRIEVE_V2_ROUTE_BONUS=0.0):
        out = backend.extend_with_clause_index([], Q, "soldier", route={"A.1"})
    assert out[0]["doc_id"] == "B.2", "bonus 0 = the index alone"


def test_widen_context_with_every_flag_off_is_unchanged():
    win = _window()
    with _with(0, [DOC_A, DOC_B]):
        assert not (backend.RETRIEVE_HYDE or backend.RETRIEVE_ROUTER_SLOTS or backend.RETRIEVE_FULL_BLOCKS
                    or backend.RETRIEVE_QUANTITY_CLAUSES or backend.RETRIEVE_V2), "code defaults are all off"
        out = backend.widen_context(win, Q, "soldier", route=set())
    assert out == win


def test_the_index_is_rebuilt_when_the_corpus_changes():
    with _with(1, [DOC_A]):
        first = backend._clause_index()
        assert backend._clause_index() is first, "cached while the corpus is the same list"
        backend.load_documents = lambda: [DOC_A, DOC_B]
        assert backend._clause_index() is not first


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
    print("all clause-index tests passed")
