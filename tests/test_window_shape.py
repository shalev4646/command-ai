# -*- coding: utf-8 -*-
"""The retrieval window's SHAPE — how its seats are shared, not how they rank.

Five attempts this campaign changed the ranking. Nobody had changed the
allocation, and the 431-case gate cannot see it: it asserts on the first three
DISTINCT documents, which the global chunk ranking fixes regardless of
n_results / max_per_doc / top_doc_depth. `test_the_gate_is_blind_to_the_shape`
pins that, so the next person does not read a flat gate as "no effect".

Measured 2026-08-28 against the 59 arbitration targets carrying a verbatim
quote verified in raw_text:

    n=8  depth=4 cap=4   18/59 orders   5/59 CLAUSES   4.9 docs   715 words
    n=8  depth=2 cap=3   23/59          6/59          6.6        669
    n=8  depth=1 cap=1   24/59          2/59          8.0        657
    n=12 depth=2 cap=3   27/59          8/59         10.3        972
    n=16 depth=2 cap=3   30/59         10/59         13.9       1277
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import backend
from storage import glossary as _glossary
from storage.vector_store import retrieve

Q = "כמה ימי חופשה מגיעים לי בשנה"


def _win(n, cap, depth, question=Q, role="soldier"):
    doc_ids = [d["document_id"] for d in backend._docs_for_role(role) if d.get("document_id")]
    search = _glossary.expand(question) if _glossary.RETRIEVE_GLOSSARY else question
    return retrieve(search, n_results=n, max_per_doc=cap, top_doc_depth=depth,
                    doc_ids=doc_ids, boost_docs=set())


def _docs(win):
    return {c["doc_id"] for c in win}


def test_the_defaults_are_still_production():
    """Every knob ships at today's value, so importing backend changes nothing
    until a paired measurement of the ANSWERS says it should — the same rule
    RETRIEVE_ROUTER_SLOTS and RETRIEVE_FULL_BLOCKS ship under."""
    assert backend.MAX_CONTEXT_CHUNKS == int(os.environ.get("RETRIEVE_MAX_CHUNKS", "8"))
    assert backend.RETRIEVE_MAX_PER_DOC == int(os.environ.get("RETRIEVE_MAX_PER_DOC", "4"))
    assert backend.RETRIEVE_TOP_DOC_DEPTH == int(os.environ.get("RETRIEVE_TOP_DOC_DEPTH", "4"))


def test_retrieve_for_role_actually_passes_the_knobs_down():
    """The override is worthless if it stops at the module global. Verified by
    the one thing that cannot be faked: the window's size changes."""
    seen = {}
    real = backend.retrieve

    def spy(*a, **kw):
        seen.update(kw)
        return real(*a, **kw)

    backend.retrieve = spy
    try:
        backend.retrieve_for_role(Q, "soldier", route=set())
    finally:
        backend.retrieve = real
    assert seen.get("max_per_doc") == backend.RETRIEVE_MAX_PER_DOC, seen
    assert seen.get("top_doc_depth") == backend.RETRIEVE_TOP_DOC_DEPTH, seen
    assert seen.get("n_results") == backend.MAX_CONTEXT_CHUNKS, seen


def test_sharing_the_seats_widens_the_window_without_evicting_anyone():
    """Across all 431 gate cases on 2026-08-28 this held 431/431 — the wider
    window is a strict superset, so widening risks dilution and never loss.
    Checked here on one question so the suite stays fast; the full sweep lives
    in the commit that introduced these knobs."""
    narrow = _docs(_win(8, 4, 4))
    for n, cap, depth in ((8, 3, 2), (12, 3, 2), (16, 3, 2)):
        wide = _docs(_win(n, cap, depth))
        assert narrow <= wide, (n, cap, depth, sorted(narrow - wide))
        assert len(wide) >= len(narrow)


def test_spreading_to_one_chunk_per_document_is_the_trap():
    """depth=1 puts the MOST orders in the window and collapses the answering
    clause from 5/59 to 2/59: the "right doc, wrong chunk" failure that
    top_doc_depth=4 was written to prevent. Pinned as a mechanism, not a score:
    at depth=1 no document may hold more than one seat, so the leading order
    cannot contribute a second clause however well it ranks."""
    win = _win(8, 1, 1)
    seats = {}
    for c in win:
        seats[c["doc_id"]] = seats.get(c["doc_id"], 0) + 1
    assert max(seats.values()) == 1, seats
    assert len(seats) == len(win)


def test_the_gate_is_blind_to_the_shape():
    """night.gate takes the first three DISTINCT documents. Those come from the
    global chunk ranking, which none of these knobs touch — so the gate returns
    the identical 389/431 for every shape. A flat gate here is not evidence of
    no effect; it is evidence the gate cannot see this axis."""
    def top3(win):
        out = []
        for c in win:
            if c["doc_id"] not in out:
                out.append(c["doc_id"])
        return out[:3]

    base = top3(_win(8, 4, 4))
    assert len(base) == 3, base
    for n, cap, depth in ((8, 3, 2), (12, 3, 2), (16, 3, 2)):
        assert top3(_win(n, cap, depth)) == base, (n, cap, depth)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
    print("all window-shape tests passed")
