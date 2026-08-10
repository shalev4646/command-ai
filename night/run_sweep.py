"""Stage 2 driver — route once, then sweep every question for free.

Ordering matters here. The router is memoized to disk BEFORE the sweep starts
(one Batch API pass, half price), so the sweep itself and every later re-sweep
in the night are pure local compute. Normalization is memoized the same way and
only fires for questions whose vocabulary the corpus does not recognise, which
is exactly the deliberately-degraded quarter of the set.

Scores come from `retrieve_for_role` rather than from `stream_ai_answer`,
because banding needs the numbers and the latter only hands back rendered
context. The exact production context is fetched later, and only for the ~100
questions that actually go to Opus — no reason to pay the extra work 800 times.
"""
from __future__ import annotations

import backend
from night import config as C
from night.ledger import Ledger
from night.sweep import NormalizeCache, RouteCache


def calibrate(rows: list[dict], routes: RouteCache) -> dict:
    """Set band thresholds from the golden set instead of from intuition.

    "Green" should mean "scores like a retrieval we already know answers
    correctly", so the cut is taken from the distribution of top scores on the
    102 golden cases — questions whose correct order is known and reached.
    """
    from night.gate import load_cases
    tops = []
    for role, q, expected, tag in load_cases():
        if tag != "golden":
            continue
        chunks = backend.retrieve_for_role(q, role, route=set())
        if chunks:
            tops.append(chunks[0]["score"])
    tops.sort()
    lo = tops[len(tops) // 10]            # 10th percentile of known-good
    med = tops[len(tops) // 2]
    C.log(f"[sweep] golden top-score distribution: p10={lo:.3f} p50={med:.3f} "
          f"min={tops[0]:.3f} max={tops[-1]:.3f}")
    return {"green": med, "red": lo}


def main() -> None:
    ledger = Ledger(C.LEDGER)
    rows = C.read_jsonl(C.QUESTIONS)
    if not rows:
        raise SystemExit("no questions.jsonl — run `python -m night.genq` first")
    C.log(f"[sweep] {len(rows)} questions, budget left ${ledger.remaining():.2f}")

    # Normalize BEFORE prewarming the router, not after. The sweep routes on the
    # normalized query, so prewarming on the raw one would miss the cache for
    # every deliberately-degraded question — a quarter of the set, at full
    # (unbatched) router price. Normalization itself is gated by a free
    # vocabulary check, so clean questions cost nothing here.
    norms = NormalizeCache()
    norms.install()
    for i, r in enumerate(rows, 1):
        r["_sq"] = backend._standalone_question(r["q"], None)
        if i % 100 == 0:
            norms.save()
            C.log(f"[sweep] normalized {i}/{len(rows)} (spent ${ledger.spent:.2f})")
    norms.save()
    changed = sum(1 for r in rows if r["_sq"] != r["q"])
    C.log(f"[sweep] normalization rewrote {changed}/{len(rows)} questions")

    routes = RouteCache()
    routes.prewarm([{"q": r["_sq"], "role": r["role"]} for r in rows], ledger)
    routes.install()

    thresholds = calibrate(rows, routes)

    out = []
    for i, r in enumerate(rows, 1):
        q, role = r["q"], r["role"]
        sq = r.pop("_sq", q)
        try:
            chunks = backend.retrieve_for_role(sq, role, route=backend._route_docs(sq, role))
        except Exception as e:
            C.log(f"[sweep] {r['id']} error: {type(e).__name__}: {e}")
            continue
        docs_ranked: list[str] = []
        for c in chunks:
            if c["doc_id"] not in docs_ranked:
                docs_ranked.append(c["doc_id"])
        top = chunks[0]["score"] if chunks else 0.0
        target = r.get("target_doc")
        target_rank = docs_ranked.index(target) + 1 if target in docs_ranked else None
        hit = None if target is None else (target_rank is not None and target_rank <= 3)

        if top >= thresholds["green"] and hit is not False:
            band = C.BAND_GREEN
        elif top < thresholds["red"]:
            band = C.BAND_RED
        else:
            band = C.BAND_YELLOW

        out.append({**r, "search_query": sq if sq != q else None,
                    "top_score": round(top, 4), "docs": docs_ranked[:5],
                    "sections": [c["section"] for c in chunks[:5]],
                    "target_rank": target_rank, "target_hit": hit, "band": band})
        if i % 100 == 0:
            norms.save(); routes.save()
            C.log(f"[sweep] {i}/{len(rows)}  spent ${ledger.spent:.2f}")

    norms.save(); routes.save()
    C.write_jsonl(C.SWEEP, out)

    from collections import Counter
    bands = Counter(r["band"] for r in out)
    C.log(f"[sweep] bands: {dict(bands)}")
    io_rows = [r for r in out if r["source"] == "inside_out"]
    if io_rows:
        hits = sum(1 for r in io_rows if r["target_hit"])
        C.log(f"[sweep] inside-out: {hits}/{len(io_rows)} reached their own order in top-3 "
              f"({100*hits/len(io_rows):.0f}%)")
    ugly = [r for r in out if r["ugly"]]
    if ugly:
        red_u = sum(1 for r in ugly if r["band"] == C.BAND_RED)
        clean = [r for r in out if not r["ugly"]]
        red_c = sum(1 for r in clean if r["band"] == C.BAND_RED)
        C.log(f"[sweep] red band — degraded {red_u}/{len(ugly)} ({100*red_u/len(ugly):.0f}%) "
              f"vs clean {red_c}/{len(clean)} ({100*red_c/len(clean):.0f}%)")
    C.log(f"[sweep] done. spent ${ledger.spent:.2f} of ${10:.2f}")


if __name__ == "__main__":
    main()
