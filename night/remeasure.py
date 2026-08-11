"""Re-run the saved baseline questions and report a paired before/after.

The 54 blind questions from the night's probe are on disk with their graded
outcomes. Re-running the same questions after a corpus change gives a paired
comparison — the difference is attributable to the corpus, not to question
variance — which is why this is worth more than a fresh random sample of the
same size and cost.

Only questions whose retrieval actually changed are re-run, plus a small
control group whose retrieval did not. The control measures how much of any
observed movement is just the model sampling differently on identical input;
without it, a few flipped verdicts look like progress.
"""
from __future__ import annotations

import json

import backend
from night import config as C
from night.ledger import Ledger
from night.probe import build_requests, run_batch
from night.redband import _outcome, wilson

N_CONTROL = 12
OUT = C.OUT / "probe_remeasure.jsonl"


def _context_fingerprint(row: dict) -> str:
    """What retrieval hands the model, reduced to a comparable key."""
    sq = row.get("search_query") or row["q"]
    chunks = backend.retrieve_for_role(sq, row["role"],
                                       route=backend._route_docs(sq, row["role"]))
    return "|".join(f'{c["doc_id"]}:{c["section"]}' for c in chunks)


def run() -> None:
    ledger = Ledger(C.LEDGER)
    base = C.read_jsonl(C.OUT / "grades_baseline.jsonl")
    sweep = {r["id"]: r for r in C.read_jsonl(C.SWEEP)}
    blind = [r for r in base if sweep.get(r["id"], {}).get("source") == "blind"]
    if not blind:
        raise SystemExit("no baseline to compare against")

    changed, same = [], []
    for r in blind:
        row = sweep[r["id"]]
        (changed if _context_fingerprint(row) != r.get("context_fp", "") else same).append(r)
    # first run has no stored fingerprint, so everything reads as changed;
    # fall back to re-running the lot when that happens
    if not same and all("context_fp" not in r for r in blind):
        C.log("[remeasure] no stored fingerprints — treating every question as changed")

    sample = changed + same[:N_CONTROL]
    C.log(f"[remeasure] {len(changed)} questions whose context moved, "
          f"+{min(len(same), N_CONTROL)} controls -> {len(sample)} to re-run")

    reqs, meta = build_requests([{**sweep[r["id"]]} for r in sample])
    for m, r in zip(meta, sample):
        m["baseline_outcome"] = _outcome(r)
    run_batch(reqs, meta, ledger, OUT, "probe-remeasure")

    from night.grade import grade_file
    grade_file(OUT, Ledger(C.LEDGER), "remeasure")

    after = C.read_jsonl(C.OUT / "grades_remeasure.jsonl")
    b_full = sum(1 for r in blind if _outcome(r) == "full")
    a_full = sum(1 for r in after if _outcome(r) == "full")
    lo_b, hi_b = wilson(b_full, len(blind))
    lo_a, hi_a = wilson(a_full, len(after))
    C.log(f"[remeasure] full answers: {b_full}/{len(blind)} "
          f"({100*b_full/len(blind):.0f}%, {100*lo_b:.0f}-{100*hi_b:.0f}) -> "
          f"{a_full}/{len(after)} ({100*a_full/max(1,len(after)):.0f}%, "
          f"{100*lo_a:.0f}-{100*hi_a:.0f})")

    flips = [r for r in after if r.get("baseline_outcome") and
             _outcome(r) != r["baseline_outcome"]]
    C.log(f"[remeasure] verdicts that moved: {len(flips)}/{len(after)}")
    (C.OUT / "remeasure_summary.json").write_text(json.dumps(
        {"before_full": b_full, "before_n": len(blind),
         "after_full": a_full, "after_n": len(after),
         "flips": len(flips)}, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    run()
