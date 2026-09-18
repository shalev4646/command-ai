# -*- coding: utf-8 -*-
"""RETRIEVE_ROUTER_BLOCKS — the router's picks served as BLOCKS (18.09).

Why: the free ceiling analysis of 18.09 showed a reranker over the embedding
ranking's top-25 cannot help the real-style questions (ceiling +1 of 20): for
them the answering order sits beyond the 25th distinct order, or is absent.
The document router reads every order TITLE, so rank is no obstacle to it —
and it is already paid for on every question. Probed on the 82 targets
($0.68): it names the answering order for 34, and for 18 of the 55 targets
the deployed window does not serve, the order it names carries the rule in
its curated block (3 of them real-style). Today a routed order gets a +0.05
bonus and one seat — one clause, usually the wrong one. This flag serves its
block instead, by the same cut as every block.

Ships OFF; off is byte-identical.

    venv\\Scripts\\python.exe tests\\test_router_blocks.py
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

WINDOW = [
    {"doc_id": "A.1", "title": "מסדר בוקר", "section": "key-facts",
     "clause": "מתי מתקיים מסדר בוקר", "text": "מסדר הבוקר יתקיים בכל יום.", "score": 0.55},
]


@contextmanager
def _with(on, docs, **knobs):
    names = ("RETRIEVE_ROUTER_BLOCKS", "RETRIEVE_V2_BLOCK_WORDS", "RETRIEVE_V2_TOP_K")
    old = {k: getattr(backend, k) for k in names}
    old_load, old_idx = backend.load_documents, backend._v2_index
    backend.RETRIEVE_ROUTER_BLOCKS = on
    for k, v in knobs.items():
        setattr(backend, k, v)
    backend.load_documents = lambda: docs
    backend._v2_index = None
    try:
        yield
    finally:
        for k, v in old.items():
            setattr(backend, k, v)
        backend.load_documents, backend._v2_index = old_load, old_idx


def test_off_changes_nothing():
    with _with(0, [DOC_A, DOC_B]):
        out = backend.extend_with_router_blocks(list(WINDOW), Q, "soldier", {"B.2"})
    assert out == WINDOW


def test_a_routed_order_is_served_as_its_whole_block_after_the_window():
    with _with(1, [DOC_A, DOC_B]):
        out = backend.extend_with_router_blocks(list(WINDOW), Q, "soldier", {"B.2"})
    assert out[:len(WINDOW)] == WINDOW, "the window comes first, untouched"
    served = [c["clause"] for c in out if c["doc_id"] == "B.2" and c["section"] == "key-facts"]
    assert served == ["מה חובת הגילוח", "אילו תכשיטים מותר לענוד עם מדים", "מה דין שיער ארוך"], served


def test_no_route_serves_nothing():
    with _with(1, [DOC_A, DOC_B]):
        assert backend.extend_with_router_blocks(list(WINDOW), Q, "soldier", set()) == WINDOW
        assert backend.extend_with_router_blocks(list(WINDOW), Q, "soldier", None) == WINDOW


def test_nothing_is_served_twice():
    with _with(1, [DOC_A, DOC_B]):
        out = backend.extend_with_router_blocks(list(WINDOW), Q, "soldier", {"A.1", "B.2"})
    keys = [(c["doc_id"], c["section"], c["clause"]) for c in out]
    assert len(keys) == len(set(keys)), keys


def test_a_routed_order_outside_the_role_or_without_a_block_is_skipped():
    with _with(1, [DOC_A, DOC_B]):
        out = backend.extend_with_router_blocks(list(WINDOW), Q, "soldier", {"Z.9"})
    assert out == WINDOW


def test_a_huge_routed_block_is_cut_like_every_block():
    with _with(1, [BIG], RETRIEVE_V2_BLOCK_WORDS=600, RETRIEVE_V2_TOP_K=2):
        out = backend.extend_with_router_blocks([], "כמה ימי מחבוש מפקד יכול לתת לי, מה העונש המרבי?",
                                                "soldier", {"E.5"})
    kf = [c["clause"] for c in out if c["section"] == "key-facts"]
    assert "מה עונש המחבוש המרבי לחייל" in kf and len(kf) <= 3, kf


def test_it_is_wired_into_widen_context():
    with _with(1, [DOC_A, DOC_B]):
        out = backend.widen_context(list(WINDOW), Q, "soldier", {"B.2"})
    assert any(c["clause"] == "אילו תכשיטים מותר לענוד עם מדים" for c in out), out


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
    print("all router-blocks tests passed")
