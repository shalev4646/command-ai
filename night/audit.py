"""Stage 0 — the structural audit. No API key, no questions, no cost.

Two things can be measured about the corpus before a single question is asked,
and both feed the morning report directly:

  structure     Which orders have a `key-facts` section, real `sections`, and
                anchors. This matters mechanically, not cosmetically: when an
                anchor wins, vector_store hands the model that document's
                MERGED key-facts block (vector_store.py:569). An order with
                anchors but no key-facts is therefore liftable but unanswerable
                — the lift arrives and the block behind it is noise. That
                combination is the single highest-value fix in the corpus and
                this is how we find every instance of it.

  reachability  Whether each order retrieves ITSELF from its own curated
                anchors. Purely local, so it costs nothing, and a failure here
                is unambiguous — no corpus-coverage excuse applies when the
                question was written against that very document.

Run this first: it is free, it is fast, and if the paid stages never happen it
still produces a real work list.
"""
from __future__ import annotations

import backend
from night import config as C

SELF_RETRIEVAL_TOP_N = 5   # "reachable" = the order appears this high for its own anchor


def _anchors(doc: dict) -> list[str]:
    """Every string vector_store would index as an anchor for this doc.

    Mirrors vector_store.py:335-347 — both storage shapes for
    suggested_questions (flat list, or {role: [...]}) plus anchor_questions,
    de-duplicated, minimum length 12.
    """
    sq = doc.get("suggested_questions")
    role_lists = list(sq.values()) if isinstance(sq, dict) else [sq or []]
    out, seen = [], set()
    for qs in role_lists + [doc.get("anchor_questions") or []]:
        for q in qs or []:
            if isinstance(q, str) and len(q.strip()) >= 12 and q not in seen:
                seen.add(q)
                out.append(q)
    return out


def _section_ids(doc: dict) -> list[str]:
    """`sections` is a LIST of {id, title, clauses}, not a dict — the indexer
    reads `section.get("id")` (vector_store.py:319) and that id is what the
    retrieval-time `section.startswith("key-facts")` test matches against.
    Iterating it as a mapping silently reports every document as key-facts-less.
    """
    secs = doc.get("sections") or []
    if isinstance(secs, dict):                      # tolerate a legacy shape
        return [str(k) for k in secs]
    return [str(s.get("id", "")) for s in secs if isinstance(s, dict)]


def structure() -> list[dict]:
    rows = []
    for d in backend.load_documents():
        doc_id = d.get("document_id")
        if not doc_id:
            continue
        secs = _section_ids(d)
        kf = [s for s in secs if s.startswith("key-facts")]
        anchors = _anchors(d)
        rows.append({
            "doc_id": doc_id,
            "title": d.get("title", ""),
            "roles": d.get("roles") or [],
            "n_sections": len(secs),
            "section_ids": secs,
            "has_key_facts": bool(kf),
            "n_anchors": len(anchors),
            "raw_words": len(str(d.get("raw_text", "")).split()),
            # the mechanically broken combination: liftable, but the lift
            # delivers no curated block behind it
            "liftable_but_empty": bool(anchors) and not kf,
            "anchor_only": not secs,
        })
    return rows


def reachability(rows: list[dict]) -> list[dict]:
    """For each order, does its own best anchor retrieve it into the top N?

    route=set() deliberately: the router is a paid Haiku call, and this probe
    asks a narrower question — can the EMBEDDING find this document from a
    question written against it. Router help would mask exactly the weakness
    we are looking for.
    """
    docs = {d["document_id"]: d for d in backend.load_documents() if d.get("document_id")}
    out = []
    for i, row in enumerate(rows, 1):
        doc = docs[row["doc_id"]]
        anchors = _anchors(doc)
        role = (row["roles"] or ["soldier"])[0]
        role = role if role in C.ROLES else "soldier"
        best_rank, best_anchor, probed = None, None, 0
        for a in anchors[:6]:                       # 6 is plenty to establish reachability
            chunks = backend.retrieve_for_role(a, role, route=set())
            probed += 1
            order = []
            for c in chunks:
                if c["doc_id"] not in order:
                    order.append(c["doc_id"])
            rank = order.index(row["doc_id"]) + 1 if row["doc_id"] in order else None
            if rank is not None and (best_rank is None or rank < best_rank):
                best_rank, best_anchor = rank, a
        out.append({**row, "self_rank": best_rank, "best_anchor": best_anchor,
                    "anchors_probed": probed,
                    "reachable": best_rank is not None and best_rank <= SELF_RETRIEVAL_TOP_N})
        if i % 20 == 0:
            C.log(f"[audit] reachability {i}/{len(rows)}")
    return out


def run() -> list[dict]:
    rows = structure()
    C.log(f"[audit] {len(rows)} orders")
    C.log(f"[audit]   no sections at all      : {sum(r['anchor_only'] for r in rows)}")
    C.log(f"[audit]   no key-facts section    : {sum(not r['has_key_facts'] for r in rows)}")
    C.log(f"[audit]   LIFTABLE BUT EMPTY      : {sum(r['liftable_but_empty'] for r in rows)}"
          "  <- anchors exist, no curated block behind them")
    C.log(f"[audit]   no anchors at all       : {sum(not r['n_anchors'] for r in rows)}")

    rows = reachability(rows)
    unreachable = [r for r in rows if not r["reachable"]]
    C.log(f"[audit] cannot retrieve themselves from their own anchors: "
          f"{len(unreachable)}/{len(rows)}")
    for r in sorted(unreachable, key=lambda r: (r["self_rank"] is not None, r["self_rank"] or 999)):
        C.log(f"[audit]   {r['doc_id']:<12} rank={r['self_rank'] or 'MISS':<5} "
              f"anchors={r['n_anchors']:<3} kf={'Y' if r['has_key_facts'] else 'N'} "
              f"{r['title'][:48]}")

    C.write_jsonl(C.OUT / "audit.jsonl", rows)
    return rows


if __name__ == "__main__":
    run()
