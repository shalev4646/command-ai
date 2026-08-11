"""Is the 180-word key-facts merge budget actually binding?

The budget was set deliberately: merging every lifted document instead of only
the winner grew retrieved context 38% (837 -> 1157 words) for no additional
probe passing, at roughly $0.04 a question (vector_store.py:604, :657). So the
question is not "is 180 arbitrary" — it is "does 180 still fit a corpus where
all 98 orders now carry curated blocks, where before only 71 did".

What the code actually does, which is narrower than it first looks: the merge
folds the winner's key-facts clauses into one block until the next one would
cross the budget, then stops. Clauses that did NOT fit are left in the candidate
pool and can still be selected as ordinary chunks — they are not discarded.
Only the folded ones are removed, because they are now inside the block.

So "binding" has to mean something measurable:
  * how often the merge fires at all
  * when it fires, how many of the winner's clauses got folded vs left out
  * and of those left out, how many actually reach the final context anyway

If the left-out clauses mostly do reach the context, the budget costs little.
If they mostly do not, it is a real ceiling on delivered curated content.
"""
from __future__ import annotations

import collections

import backend
from night import config as C
from storage.vector_store import _KEY_FACTS_MERGE_WORDS as BUDGET


def probe(question: str, role: str) -> dict | None:
    """Reproduce a retrieval and report what the merge did to it."""
    from storage import vector_store as vs

    doc_ids = [d["document_id"] for d in backend._docs_for_role(role) if d.get("document_id")]
    route = backend._route_docs(question, role)

    seen: dict = {}
    orig = vs._KEY_FACTS_MERGE_WORDS

    # Re-run retrieval twice with different budgets and diff the delivered text.
    # Reaching into the module constant is deliberate: it is the single knob the
    # question is about, and patching it is cheaper and more faithful than
    # reimplementing the merge.
    out = {}
    for label, budget in (("current", orig), ("double", orig * 2), ("quad", orig * 4)):
        vs._KEY_FACTS_MERGE_WORDS = budget
        chunks = vs.retrieve(question, n_results=backend.MAX_CONTEXT_CHUNKS,
                             doc_ids=doc_ids, boost_docs=route)
        kf = [c for c in chunks if str(c.get("section", "")).startswith("key-facts")]
        out[label] = {
            "words": sum(len(c["text"].split()) for c in chunks),
            "kf_words": sum(len(c["text"].split()) for c in kf),
            "docs": [c["doc_id"] for c in chunks],
            "top": chunks[0]["doc_id"] if chunks else None,
        }
    vs._KEY_FACTS_MERGE_WORDS = orig
    del seen
    return out


def main() -> None:
    grades = C.read_jsonl(C.OUT / "grades_baseline.jsonl")
    sweep = {r["id"]: r for r in C.read_jsonl(C.SWEEP)}
    rows = [r for r in grades if sweep.get(r["id"], {}).get("source") == "blind"]
    C.log(f"[budget180] budget is {BUDGET} words; probing {len(rows)} blind questions")

    agg = collections.defaultdict(lambda: {"words": 0, "kf_words": 0})
    changed_top = changed_set = 0
    for i, r in enumerate(rows, 1):
        row = sweep[r["id"]]
        res = probe(row.get("search_query") or row["q"], row["role"])
        for k, v in res.items():
            agg[k]["words"] += v["words"]
            agg[k]["kf_words"] += v["kf_words"]
        if res["current"]["top"] != res["quad"]["top"]:
            changed_top += 1
        if set(res["current"]["docs"]) != set(res["quad"]["docs"]):
            changed_set += 1
        if i % 20 == 0:
            C.log(f"[budget180] {i}/{len(rows)}")

    n = len(rows)
    C.log(f"[budget180] average retrieved context, per question:")
    for k in ("current", "double", "quad"):
        a = agg[k]
        C.log(f"[budget180]   {k:<8} {a['words']/n:6.0f} words total, "
              f"{a['kf_words']/n:6.0f} of them curated")
    extra = (agg["quad"]["words"] - agg["current"]["words"]) / n
    C.log(f"[budget180] quadrupling the budget adds {extra:.0f} words/question "
          f"(~${extra * 1.4 * 5 / 1_000_000:.5f} at Opus input rates)")
    C.log(f"[budget180] questions whose leading order changed: {changed_top}/{n}")
    C.log(f"[budget180] questions whose retrieved set changed: {changed_set}/{n}")


if __name__ == "__main__":
    main()
