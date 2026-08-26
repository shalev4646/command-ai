# -*- coding: utf-8 -*-
"""storage.vector_store._ROUTE_BOOST: the router's pick must be able to win.

2026-08-25, mini-pilot 150. The adjudication of all 107 zeros found that the
answering order reached the served window in only 14 of 62 recoverable cases —
retrieval, not coverage, is the bottleneck. Probing the two biggest clusters
(25 questions answered by PM-35.0402 or 61.0104) split the failure in two:

  * the router picked the answering order in 11/25 (44%) — its own accuracy
    problem, not addressed here;
  * and in 6 of those 11 it picked correctly and the order STILL missed the
    window, because +0.05 is barely twice the median doc-to-doc score margin
    (0.025) while `top_doc_depth=4` hands the leader half the eight seats.
    A hint that cannot outrank a near-tie is not a hint, it is decoration.

Measured sweep over the same 25 (router decisions frozen in
night/out/route_probe_pilot150.json, so this test costs nothing):

    boost   router right (11)   router wrong (14)   total in window
    0.05            5                   5                 10
    0.10            7                   5                 12
    0.15            9                   5                 14
    0.25           10                   3                 13
    0.40           11                   0                 11

0.15 is the peak. Past it the bonus stops being a hint and becomes a scope:
the five orders that retrieval finds WITHOUT the router lose their seats, which
is exactly the filtering variant `_ROUTE_BOOST`'s own comment says was rejected.
Both halves are pinned below — the gain, and the ceiling that stops it from
turning into a filter.

The retrieval gate cannot catch this: night/gate.py runs with the router
bypassed, so every boost value scores identically there. This file is the
control that gate run cannot be.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import backend
from storage import vector_store as vs

PROBE = ROOT / "night" / "out" / "route_probe_pilot150.json"


def _cases():
    assert PROBE.exists(), f"no frozen router decisions at {PROBE}"
    return json.loads(PROBE.read_text(encoding="utf-8"))


def _in_window(case) -> bool:
    chunks = backend.retrieve_for_role(case["q"], case["role"],
                                       route=set(case["route"]), widen=False)
    return case["target"] in {c["doc_id"] for c in chunks}


def _split():
    right, wrong = [], []
    for case in _cases().values():
        (right if case["target"] in case["route"] else wrong).append(case)
    return right, wrong


def test_router_pick_reaches_the_window_in_most_cases():
    """When the router names the answering order, retrieval must serve it."""
    right, _ = _split()
    hits = sum(1 for c in right if _in_window(c))
    assert hits >= 8, (
        f"router named the answering order in {len(right)} cases but only "
        f"{hits} reached the window — the bonus is too weak to break a near-tie"
    )


def test_boost_stays_a_hint_and_not_a_scope():
    """Orders retrieval finds on its own must keep their seats."""
    _, wrong = _split()
    survivors = sum(1 for c in wrong if _in_window(c))
    assert survivors >= 5, (
        f"only {survivors} orders survived without the router's help — the "
        f"bonus has become a filter, the variant _ROUTE_BOOST rejects by design"
    )


def test_boost_is_within_the_measured_band():
    assert 0.10 <= vs._ROUTE_BOOST <= 0.20, (
        f"_ROUTE_BOOST={vs._ROUTE_BOOST} is outside the band measured on the "
        f"mini-pilot: below 0.10 the hint cannot break a tie, above 0.20 it "
        f"starts evicting orders retrieval found by itself"
    )


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
    print("all route-boost tests passed")
