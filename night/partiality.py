"""Why are 78% of answers partial — missing content, or starved context?

The two explanations imply opposite plans. If the second half of a two-part
question goes unanswered because the corpus lacks the order, more orders fixes
it. If it goes unanswered because the answering order WAS retrieved but got one
chunk out of eight, more orders makes it worse — 447 documents compete for the
same eight slots, and the project's own history already records rank inversions
from corpus growth.

The distinction is measurable for free. For each blind question that came back
partial, look at what retrieval actually delivered:

  starved    two or more distinct orders in the context, and the runner-up got
             substantially fewer chunks than the leader. The content was there
             and the allocation buried it.
  absent     nothing in the context plausibly covers the unanswered part.

Only `absent` is fixed by acquisition.
"""
from __future__ import annotations

import collections

import backend
from night import config as C


def analyse() -> dict:
    grades = C.read_jsonl(C.OUT / "grades_baseline.jsonl")
    sweep = {r["id"]: r for r in C.read_jsonl(C.SWEEP)}
    rows = [r for r in grades
            if sweep.get(r["id"], {}).get("source") == "blind"
            and (r.get("grade") or {}).get("unanswered_parts", 0) > 0]

    out = []
    for r in rows:
        q, role = r["q"], r["role"]
        chunks = backend.retrieve_for_role(
            sweep[r["id"]].get("search_query") or q, role,
            route=backend._route_docs(q, role))
        per_doc = collections.Counter(c["doc_id"] for c in chunks)
        ranked = per_doc.most_common()
        leader = ranked[0][1] if ranked else 0
        runner = ranked[1][1] if len(ranked) > 1 else 0
        out.append({
            "id": r["id"], "q": q,
            "n_orders": len(per_doc), "leader_chunks": leader,
            "runner_chunks": runner,
            "answered": r["grade"]["answered_parts"],
            "unanswered": r["grade"]["unanswered_parts"],
            # a runner-up holding a single slot against a leader holding half
            # the context is the documented starvation shape
            "starved": len(per_doc) > 1 and runner <= 1 and leader >= 3,
        })
    return {"rows": out}


def main() -> None:
    res = analyse()
    rows = res["rows"]
    n = len(rows)
    starved = [r for r in rows if r["starved"]]
    C.log(f"[partiality] {n} blind questions with an unanswered part")
    C.log(f"[partiality]   context held 2+ orders : "
          f"{sum(1 for r in rows if r['n_orders'] > 1)}/{n}")
    C.log(f"[partiality]   STARVED runner-up      : {len(starved)}/{n} "
          f"({100*len(starved)/max(1,n):.0f}%)")
    dist = collections.Counter(r["n_orders"] for r in rows)
    C.log(f"[partiality]   distinct orders in context: {dict(sorted(dist.items()))}")
    lead = collections.Counter(r["leader_chunks"] for r in rows)
    C.log(f"[partiality]   chunks held by the leading order: {dict(sorted(lead.items()))}")
    C.write_jsonl(C.OUT / "partiality.jsonl", rows)
    for r in starved[:5]:
        C.log(f"[partiality]   {r['leader_chunks']}v{r['runner_chunks']} "
              f"{r['q'][:66]}")


if __name__ == "__main__":
    main()
