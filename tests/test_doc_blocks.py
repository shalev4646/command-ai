# -*- coding: utf-8 -*-
"""RETRIEVE_DOC_BLOCKS — the window rebuilt as ORDERS (12.09).

Why: measured free on the 82 adjudicated targets, the answering order sits in
the global ranking's top-25 for 54 of them, and today's window delivers 29.
The loss is the window's shape — four of eight seats go to the leading order
so that "right doc, wrong chunk" cannot happen. Serving the order's curated
block removes that failure by construction, so the seats are free again.

Ships OFF; these tests pin what it does when on, and that OFF is
byte-identical to the historical path.

    venv\\Scripts\\python.exe tests\\test_doc_blocks.py
"""
import sys
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import backend
from tests.test_clause_index import DOC_A, DOC_B, BIG, Q

# a raw-text chunk of an order whose block does not carry the answer — the
# case the "V3 only" run lost 15 orders on
RAW = {"doc_id": "B.2", "title": "הופעה ולבוש", "section": "chunk3", "clause": "3",
       "text": "הופעה ולבוש\nטקסט גולמי על שרוולים שאין לו סעיף מאוצר.", "score": 0.51}


def _ranked():
    """What the deep ranking returns: B.2 leads, then A.1, then B.2's raw."""
    return [
        {"doc_id": "B.2", "title": "הופעה ולבוש", "section": "key-facts",
         "clause": "מה חובת הגילוח", "text": "חייל יתגלח מדי יום.", "score": 0.62},
        {"doc_id": "A.1", "title": "מסדר בוקר", "section": "key-facts",
         "clause": "מתי מתקיים מסדר בוקר", "text": "מסדר הבוקר יתקיים בכל יום.", "score": 0.55},
        RAW,
    ]


@contextmanager
def _with(n, docs, pool=None, ranked=None, **knobs):
    old = {k: getattr(backend, k) for k in
           ("RETRIEVE_DOC_BLOCKS", "RETRIEVE_DOC_BLOCKS_POOL", "RETRIEVE_DOC_BLOCKS_RAW",
            "RETRIEVE_V2_BLOCK_WORDS", "RETRIEVE_V2_TOP_K", "RETRIEVE_V3", "RETRIEVE_V3_ONLY")}
    old_load, old_idx, old_ret = backend.load_documents, backend._v2_index, backend.retrieve
    backend.RETRIEVE_DOC_BLOCKS = n
    if pool is not None:
        backend.RETRIEVE_DOC_BLOCKS_POOL = pool
    for k, v in knobs.items():
        setattr(backend, k, v)
    backend.load_documents = lambda: docs
    backend._v2_index = None
    calls = []
    if ranked is not None:
        backend.retrieve = lambda *a, **kw: (calls.append(kw), list(ranked))[1]
    try:
        yield calls
    finally:
        for k, v in old.items():
            setattr(backend, k, v)
        backend.load_documents, backend._v2_index, backend.retrieve = old_load, old_idx, old_ret


def test_off_returns_nothing_and_leaves_the_historical_path():
    with _with(0, [DOC_A, DOC_B], ranked=_ranked()) as calls:
        assert backend.doc_blocks_window(Q, "soldier", set(), ["A.1", "B.2"]) == []
        assert not calls, "off must not even rank"
        out = backend.retrieve_for_role(Q, "soldier", route=set(), widen=False)
    assert [c["clause"] for c in out] == [c["clause"] for c in _ranked()], out


def test_the_first_n_orders_are_served_as_whole_blocks():
    with _with(1, [DOC_A, DOC_B], ranked=_ranked()):
        out = backend.doc_blocks_window(Q, "soldier", set(), ["A.1", "B.2"])
    blocks = [c for c in out if c["doc_id"] == "B.2" and c["section"] == "key-facts"]
    assert [c["clause"] for c in blocks] == ["מה חובת הגילוח", "אילו תכשיטים מותר לענוד עם מדים",
                                             "מה דין שיער ארוך"], blocks
    assert all(c["score"] == 0.62 for c in blocks), "the block carries the order's ranking score"
    assert not any(c["doc_id"] == "A.1" and c["section"] == "key-facts" and
                   c["clause"] == "מתי מתקיים מסדר בוקר" and c["score"] == 0.62 for c in out)


def test_the_raw_chunks_that_ranked_are_never_dropped():
    """9 of 82 adjudicated targets are answered by raw text no curated clause
    carries, and the V3-only run lost 15 orders by dropping them."""
    with _with(1, [DOC_A, DOC_B], ranked=_ranked(), RETRIEVE_DOC_BLOCKS_RAW=4):
        out = backend.doc_blocks_window(Q, "soldier", set(), ["A.1", "B.2"])
    assert any(c["section"] == "chunk3" for c in out), "the raw chunk must survive"
    assert any(c["doc_id"] == "A.1" for c in out), "an order below the block cap still rides as a chunk"


def test_the_raw_tail_is_capped():
    """Measured 12.09: uncapped, the tail bought 29 orders and 3 sections for
    3,500 words a question. It is here for the raw-only answers, not for
    coverage."""
    many = [dict(RAW, section=f"chunk{i}", clause=str(i), score=0.5 - i / 100) for i in range(3, 20)]
    with _with(1, [DOC_A, DOC_B], ranked=many, RETRIEVE_DOC_BLOCKS_RAW=2):
        out = backend.doc_blocks_window(Q, "soldier", set(), ["A.1", "B.2"])
    raw = [c for c in out if c["section"] != "key-facts"]
    assert [c["section"] for c in raw] == ["chunk3", "chunk4"], [c["section"] for c in raw]
    with _with(1, [DOC_A, DOC_B], ranked=many, RETRIEVE_DOC_BLOCKS_RAW=0):
        # the pool holds only B.2 raw chunks here, so blocks-only is B.2's block
        out = backend.doc_blocks_window(Q, "soldier", set(), ["A.1", "B.2"])
    assert all(c["section"] == "key-facts" for c in out), [c["section"] for c in out]
    assert backend._append_new([], [{"doc_id": "x", "section": "s", "clause": "c"}], 0) == [], \
        "limit 0 appends nothing (the loop caps after appending)"


def test_nothing_is_served_twice():
    with _with(2, [DOC_A, DOC_B], ranked=_ranked()):
        out = backend.doc_blocks_window(Q, "soldier", set(), ["A.1", "B.2"])
    keys = [(c["doc_id"], c["section"], c["clause"]) for c in out]
    assert len(keys) == len(set(keys)), keys


def test_the_value_is_how_many_orders_and_the_pool_is_deeper_than_the_window():
    with _with(2, [DOC_A, DOC_B], pool=40, ranked=_ranked()) as calls:
        out = backend.doc_blocks_window(Q, "soldier", set(), ["A.1", "B.2"])
    assert calls and calls[0]["n_results"] == 40 and calls[0]["top_doc_depth"] == 1, calls
    served = {c["doc_id"] for c in out if c["section"] == "key-facts"}
    assert served == {"A.1", "B.2"}, served


def test_a_huge_block_is_cut_to_its_evidenced_clauses():
    ranked = [{"doc_id": "E.5", "title": "פקודה ענקית", "section": "key-facts",
               "clause": "סעיף מספר 0 על נושא 0", "text": "...", "score": 0.4}]
    with _with(1, [BIG], ranked=ranked, RETRIEVE_V2_BLOCK_WORDS=600, RETRIEVE_V2_TOP_K=2):
        out = backend.doc_blocks_window("כמה ימי מחבוש מפקד יכול לתת לי, מה העונש המרבי?",
                                        "soldier", set(), ["E.5"])
    kf = [c["clause"] for c in out if c["section"] == "key-facts"]
    assert "מה עונש המחבוש המרבי לחייל" in kf and len(kf) <= 3, kf


def test_retrieve_for_role_uses_the_rule_and_the_extensions_still_stack():
    with _with(1, [DOC_A, DOC_B], ranked=_ranked()):
        out = backend.retrieve_for_role(Q, "soldier", route=set(), widen=True)
    assert any(c["clause"] == "אילו תכשיטים מותר לענוד עם מדים" for c in out), out
    assert any(c["section"] == "chunk3" for c in out)


def test_an_empty_ranking_serves_nothing_rather_than_guessing():
    with _with(2, [DOC_A, DOC_B], ranked=[]):
        assert backend.doc_blocks_window(Q, "soldier", set(), ["A.1", "B.2"]) == []


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
    print("all doc-blocks tests passed")
