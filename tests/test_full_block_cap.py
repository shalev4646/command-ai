# -*- coding: utf-8 -*-
"""RETRIEVE_FULL_BLOCK_MAX_WORDS — a ceiling on the lead order's full block.

Why (18.09): RETRIEVE_FULL_BLOCKS serves the WHOLE curated block of the window's
first order, and four blocks are huge (PM-33.0302 is 9.8K words). Measured free
on the 82 adjudicated targets with the production flags, the window's mean is
3,397 words and its max 12,040, and Hebrew costs 3.9-4.5 tokens a word on the
Opus tokenizer — a question PM-33.0302 leads pays ~$0.22 a pass for that one
block. Over the ceiling the block is cut to the clauses the clause-title index
scores for the question, best first, then unscored clauses in block order while
budget remains — always emitted in block order.

Ships OFF; these tests pin that OFF is byte-identical and what ON does.

    venv\\Scripts\\python.exe tests\\test_full_block_cap.py
"""
import sys
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import backend
from tests.test_clause_index import BIG, DOC_B

ARREST = "מה עונש המחבוש המרבי לחייל"      # BIG's last clause, the one a punishment question needs
Q_ARREST = "כמה ימי מחבוש מפקד יכול לתת לחייל? מה עונש המחבוש המרבי?"


@contextmanager
def _with(cap: int, docs: list[dict], full_blocks: int = 1):
    old = {k: getattr(backend, k) for k in ("RETRIEVE_FULL_BLOCKS", "RETRIEVE_FULL_BLOCK_MAX_WORDS")}
    old_load, old_index = backend.load_documents, backend._v2_index
    backend.RETRIEVE_FULL_BLOCKS = full_blocks
    backend.RETRIEVE_FULL_BLOCK_MAX_WORDS = cap
    backend.load_documents = lambda: docs
    backend._v2_index = None
    try:
        yield
    finally:
        for k, v in old.items():
            setattr(backend, k, v)
        backend.load_documents, backend._v2_index = old_load, old_index


def _words(chunks) -> int:
    return sum(len(c["text"].split()) for c in chunks)


def _lead_window():
    """A window BIG leads, as the ranking returns it: one of its own clauses."""
    return [{"doc_id": "E.5", "title": "פקודה ענקית", "section": "key-facts",
             "clause": "סעיף מספר 0 על נושא 0", "text": "פקודה ענקית — עיקרי הפקודה\nסעיף ...", "score": 0.6}]


def test_off_is_byte_identical():
    with _with(0, [BIG]):
        assert backend._capped_block(BIG, Q_ARREST, 0) == backend._full_block(BIG)
        out = backend.extend_with_full_blocks(_lead_window(), "soldier", Q_ARREST)
    whole = backend._append_new(_lead_window(), backend._full_block(BIG), None)
    assert out == whole, "cap off must serve exactly what RETRIEVE_FULL_BLOCKS always served"


def test_a_block_under_the_ceiling_is_never_cut():
    with _with(10_000, [BIG, DOC_B]):
        assert backend._capped_block(DOC_B, Q_ARREST, 10_000) == backend._full_block(DOC_B)
        assert backend._capped_block(BIG, Q_ARREST, 10_000) == backend._full_block(BIG)


def test_the_cut_keeps_the_scored_clause_and_the_budget_and_block_order():
    with _with(350, [BIG]):
        cut = backend._capped_block(BIG, Q_ARREST, 350)
    full = backend._full_block(BIG)
    assert any(c["clause"] == ARREST for c in cut), "the clause the question asks about must survive the cut"
    assert _words(cut) <= 350, _words(cut)
    assert len(cut) < len(full)
    order = [full.index(c) for c in cut]
    assert order == sorted(order), "clauses are emitted in the curator's block order, not score order"


def test_budget_left_after_the_scored_clauses_fills_in_block_order():
    with _with(350, [BIG]):
        cut = backend._capped_block(BIG, Q_ARREST, 350)
    unscored = [c["clause"] for c in cut if c["clause"] != ARREST]
    assert unscored == [f"סעיף מספר {i} על נושא {i}" for i in range(len(unscored))], unscored
    assert len(unscored) >= 2, "roughly three hundred words of budget hold more than one filler clause"


def test_no_question_cuts_by_block_order_alone():
    with _with(250, [BIG]):
        cut = backend._capped_block(BIG, "", 250)
    assert [c["clause"] for c in cut][:2] == ["סעיף מספר 0 על נושא 0", "סעיף מספר 1 על נושא 1"]
    assert _words(cut) <= 250


def test_an_over_long_first_clause_never_empties_the_block():
    with _with(10, [BIG]):
        cut = backend._capped_block(BIG, "", 10)
    assert len(cut) >= 1


def test_widen_context_hands_the_question_to_the_cap():
    with _with(350, [BIG]):
        out = backend.extend_with_full_blocks(_lead_window(), "soldier", Q_ARREST)
    served = [c for c in out if c["doc_id"] == "E.5"]
    assert any(c["clause"] == ARREST for c in served)
    assert len(served) < len(backend._full_block(BIG)) + 1, "the whole 1,000-word block must not ride along"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok {name}")
