"""A free, end-to-end rehearsal of one fix, on one real order.

The night's whole premise is that adding a curated `key-facts` section to an
order that lacks one changes what the model actually receives. That is a claim
about plumbing, and plumbing can be tested without an API key or a single
generated question — so it should be, before any money is spent on the claim.

The rehearsal:
  1. show what an anchor-win currently hands the model for a key-facts-less order
  2. add a key-facts section, re-index
  3. show what it hands the model now
  4. run the 415 gate cases before and after to prove nothing else moved

Step 4 runs with route=set() throughout. That is not the production ranking
(the router adds +0.05 to real chunks), but it is the SAME setting on both
sides, so the delta is valid — and it keeps the rehearsal free.
"""
from __future__ import annotations

import json
from pathlib import Path

import backend
from night import config as C

STORE = C.ROOT / "storage" / "json_store"


def doc_path(doc_id: str) -> Path:
    """Locate an order's JSON by its document_id (filenames are Hebrew titles)."""
    for p in STORE.glob("*.json"):
        try:
            if json.loads(p.read_text(encoding="utf-8")).get("document_id") == doc_id:
                return p
        except (json.JSONDecodeError, OSError):
            continue
    raise FileNotFoundError(doc_id)


def anchors_of(doc: dict) -> list[str]:
    sq = doc.get("suggested_questions")
    lists = list(sq.values()) if isinstance(sq, dict) else [sq or []]
    out = []
    for qs in lists + [doc.get("anchor_questions") or []]:
        out += [q for q in (qs or []) if isinstance(q, str) and len(q.strip()) >= 12]
    return out


def context_for(question: str, role: str = "soldier") -> tuple[list[str], str]:
    """What retrieval hands the model, with the router deliberately bypassed."""
    chunks = backend.retrieve_for_role(question, role, route=set())
    labels = [f'{c["doc_id"]}|{c["section"]}' for c in chunks]
    return labels, backend._context_from_chunks(chunks)


def gate_snapshot() -> dict[str, list[str]]:
    """Top-3 doc ids for every one of the 415 gate cases, router bypassed.

    Returned as data rather than pass/fail so the comparison can distinguish
    "a case broke" from "a case that already failed still fails" — only the
    former is a regression this fix is responsible for.
    """
    import sys
    sys.argv = ["rehearse"]
    import eval as E
    cases = [(r, q, e) for r, q, e in E.GOLDEN] + [(r, q, e) for r, q, e in E.DIRTY]
    adv = json.loads((C.ROOT / "eval_adversarial.json").read_text(encoding="utf-8"))
    cases += [(p["role"], p["question"], p["expected"]) for p in adv]

    snap = {}
    for i, (role, q, _expected) in enumerate(cases):
        chunks = backend.retrieve_for_role(q, role, route=set())
        tops = []
        for c in chunks:
            if c["doc_id"] not in tops:
                tops.append(c["doc_id"])
        snap[f"{role}|{q}"] = tops[:3]
        if (i + 1) % 100 == 0:
            C.log(f"[rehearse] gate snapshot {i + 1}/{len(cases)}")
    return snap


def compare(before: dict, after: dict) -> dict:
    """Rank inversions between two gate snapshots, split by direction."""
    changed = {k: (before[k], after[k]) for k in before if before.get(k) != after.get(k)}
    return {"n_cases": len(before), "n_changed": len(changed), "changed": changed}
