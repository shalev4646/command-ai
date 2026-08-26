# -*- coding: utf-8 -*-
"""backend.RETRIEVE_ROUTER_SLOTS: a routed order must reach the context.

2026-08-25, mini-pilot 150. Adjudicating all 107 zeros found the answering
order reached the served window in only 14 of 62 recoverable cases — retrieval,
not coverage, is the bottleneck. Probing the two biggest clusters (25 questions
answered by PM-35.0402 or 61.0104, router decisions frozen in
night/out/route_probe_pilot150.json so this test costs nothing) split that
failure in two:

  * the router named the answering order in 11/25 (44%) — its own accuracy
    problem, not addressed here;
  * and in 6 of those 11 it named the order correctly and the order STILL
    missed the context, because +0.05 is barely twice the median doc-to-doc
    score margin (0.025) while `top_doc_depth=4` hands the leader half the
    eight seats. A hint that cannot outrank a near-tie is decoration.

The obvious repair — raise `_ROUTE_BOOST` — was measured and rejected. With the
router live on all 431 gate cases (decisions bought once into
night/out/route_cache_gate.json), 0.05 and 0.10 both score 393/431 and 0.15
scores 390: six regressions for three rescues. And they are not anchorable —
35.0205 already carries an anchor almost identical to the question it loses
("האם מותר לקזז חובות מכספי הפיקדון האישי של חייל משוחרר?"). A stronger bonus
lifts the competing order by exactly as much, so no anchor can outrun it.

`extend_with_router_slots` wins instead, and its own docstring predicted this:
the routed orders "get a bonus that cannot lift them into the window, and here
they get a seat". Because it APPENDS, it cannot evict — measured on the same 25:

    slots   router right (11)   router wrong (14)   in context   median chunks
      0             5                   5               10            15
      1            10                   5               15            16
      2            11                   5               16            16
      3            11                   5               16            16

Two seats take the router-correct half from 5/11 to 11/11, leave the orders
retrieval finds unaided untouched at 5, and cost one chunk of context. Pinned
below: the gain, and the no-eviction property that makes it safe.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import backend

PROBE = ROOT / "night" / "out" / "route_probe_pilot150.json"
FLY = ROOT / "fly.toml"


def _configured_slots() -> int:
    """The value production actually deploys, not the code default.

    Retrieval flags in this project default OFF in code — so a local boot buys
    nothing unasked — and are switched on in fly.toml with the measurement that
    justified them. A test that read the code default would measure a no-op and
    pass for the wrong reason (the lesson test_hyde_prefetch records). The
    deployed value is the condition under test.
    """
    for line in FLY.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("RETRIEVE_ROUTER_SLOTS"):
            return int(line.split("=", 1)[1].strip().strip('"'))
    return 0


def _cases():
    assert PROBE.exists(), f"no frozen router decisions at {PROBE}"
    return json.loads(PROBE.read_text(encoding="utf-8"))


def _served(case) -> bool:
    """Is the answering order anywhere in the context the model is handed?"""
    backend.RETRIEVE_ROUTER_SLOTS = _configured_slots()
    chunks = backend.retrieve_for_role(case["q"], case["role"],
                                       route=set(case["route"]), widen=True)
    return case["target"] in {c["doc_id"] for c in chunks}


def _split():
    right, wrong = [], []
    for case in _cases().values():
        (right if case["target"] in case["route"] else wrong).append(case)
    return right, wrong


def test_a_routed_order_reaches_the_context():
    right, _ = _split()
    hits = sum(1 for c in right if _served(c))
    assert hits == len(right), (
        f"the router named the answering order in {len(right)} cases but only "
        f"{hits} of them were served — a routed order needs a seat, not a bonus. "
        f"One seat reaches 10/11; the eleventh needs two."
    )


def test_seats_append_and_never_evict():
    """Orders retrieval finds without the router must keep their place."""
    _, wrong = _split()
    survivors = sum(1 for c in wrong if _served(c))
    assert survivors >= 5, (
        f"only {survivors} orders survived where the router did NOT name them — "
        f"seats are appended and must not displace what ranking already found"
    )


def test_production_deploys_the_seats():
    assert _configured_slots() >= 1, (
        "fly.toml does not set RETRIEVE_ROUTER_SLOTS, so production leaves the "
        "router paying for a shortlist it cannot deliver: 5/11 served, not 11/11"
    )




# ── added 2026-08-26, after the seats shipped ────────────────────────────────
# `extend_with_router_slots` calls `retrieve` with explicit `doc_ids`, and that
# path applies no curated-only filter — the filter lives one level up, in
# `retrieve_for_role`. So the seats quietly re-admitted the eleven orders that
# `RETRIEVE_CURATED_ONLY` deliberately keeps out of the search space
# (tests/test_corpus_reachable.py), and the same document became invisible to
# ranking but visible through a seat.
#
# The tempting reading is that this is a bonus: 36.0301 is genuinely the order
# that answers q00177, and a seat is the only way it ever reaches a soldier.
# But that is an argument for CURATING it, not for serving raw uncurated text
# through a side door — the curated block is the quality instrument the whole
# ingest pipeline exists to produce. Whether uncurated orders should be served
# is a real question and deserves a deliberate answer; it must not arrive as a
# side effect of a retrieval fix.
UNCURATED_PROBE = "תוספת פעילות ברמה 3 — מי זכאי וכמה זה?"


def test_a_seat_does_not_smuggle_in_an_uncurated_order():
    if not backend.RETRIEVE_CURATED_ONLY:
        return  # the policy is off; nothing to enforce
    backend.RETRIEVE_ROUTER_SLOTS = max(2, _configured_slots())
    uncurated = {d["document_id"] for d in backend.load_documents()
                 if d.get("document_id") and not backend._has_key_facts(d)}
    assert uncurated, "corpus has no uncurated order to test against"
    route = set(sorted(uncurated)[:2])
    served = {c["doc_id"] for c in backend.retrieve_for_role(
        UNCURATED_PROBE, "soldier", route=route, widen=True)}
    leaked = served & uncurated
    assert not leaked, (
        f"router seats served uncurated orders {sorted(leaked)} that "
        f"RETRIEVE_CURATED_ONLY keeps out of retrieval everywhere else"
    )


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
    print("all router-seat tests passed")
