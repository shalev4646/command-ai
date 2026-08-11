"""Measure the red band, then weight the bands into one corpus-wide number.

The night's probe sampled only the yellow band. That was the right call for
information per dollar — yellow is where the outcome is genuinely unpredictable
— but it means the result cannot be read as a population rate: 393 of the 449
blind questions sat in red and were never sent to Opus at all.

Stratified sampling fixes that without paying for a full random sample. Each
band is measured separately and then weighted by how many questions actually
fall in it, which yields a valid estimate for the whole blind set:

    rate = Σ  (share of questions in band)  ×  (measured rate within band)

Only the blind questions are counted. The inside-out half was generated from
the curated blocks themselves, so it measures embedding round-tripping rather
than coverage, and folding it in would inflate the answer.
"""
from __future__ import annotations

import json
import math
import random

from night import config as C
from night.ledger import Ledger
from night.probe import build_requests, run_batch

N_RED = 60
rng = random.Random(20260811)

OUT_RED = C.OUT / "probe_red.jsonl"
GRADES_RED = C.OUT / "grades_red.jsonl"


def _outcome(row: dict) -> str:
    """full / partial / nothing, from the part counts.

    The four-way LABEL is not used: the night's grader returned zero `partial`
    and zero `refused` because the prompt told it `led_known` was "a success,
    not a failure". The counts were never given a preferred answer, so they are
    the field that survived.
    """
    g = row.get("grade") or {}
    if g.get("answered_parts") == 0:
        return "nothing"
    if g.get("unanswered_parts") == 0:
        return "full"
    return "partial"


def wilson(k: int, n: int) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    z, p = 1.96, k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    m = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (max(0.0, (c - m) / d), min(1.0, (c + m) / d))


def probe_red() -> None:
    ledger = Ledger(C.LEDGER)
    sweep = C.read_jsonl(C.SWEEP)
    red = [r for r in sweep if r["source"] == "blind" and r["band"] == C.BAND_RED]
    sample = rng.sample(red, min(N_RED, len(red)))
    C.log(f"[redband] {len(red)} blind questions in the red band; probing {len(sample)}")

    reqs, meta = build_requests(sample)
    run_batch(reqs, meta, ledger, OUT_RED, "probe-red")

    from night.grade import grade_file
    grade_file(OUT_RED, Ledger(C.LEDGER), "red")


def combine() -> dict:
    """Weight the measured bands by their true share of the blind set."""
    sweep = C.read_jsonl(C.SWEEP)
    blind = [r for r in sweep if r["source"] == "blind"]
    n_blind = len(blind)
    sizes = {b: sum(1 for r in blind if r["band"] == b)
             for b in (C.BAND_GREEN, C.BAND_YELLOW, C.BAND_RED)}

    blind_ids = {r["id"] for r in blind}
    measured: dict[str, list[str]] = {}
    for path in (C.OUT / "grades_baseline.jsonl", GRADES_RED):
        for row in C.read_jsonl(path):
            if row["id"] in blind_ids:
                band = next(r["band"] for r in blind if r["id"] == row["id"])
                measured.setdefault(band, []).append(_outcome(row))

    est = {"full": 0.0, "partial": 0.0, "nothing": 0.0}
    detail = {}
    for band, size in sizes.items():
        obs = measured.get(band, [])
        share = size / n_blind
        if not obs:
            # Never measured. Green is 2 questions out of 449 and cannot move
            # the estimate; if a larger band were ever unmeasured this would
            # need saying out loud rather than silently skipping.
            detail[band] = {"size": size, "measured": 0}
            continue
        rates = {k: obs.count(k) / len(obs) for k in est}
        detail[band] = {"size": size, "measured": len(obs), "share": round(share, 3),
                        **{k: round(v, 3) for k, v in rates.items()}}
        for k in est:
            est[k] += share * rates[k]

    covered = sum(d.get("share", 0) for d in detail.values() if d.get("measured"))
    return {"n_blind": n_blind, "bands": detail,
            "weighted": {k: round(v, 4) for k, v in est.items()},
            "share_of_blind_measured": round(covered, 3)}


def report() -> None:
    res = combine()
    C.log("[redband] " + json.dumps(res, ensure_ascii=False))
    w = res["weighted"]
    C.log(f"[redband] WEIGHTED estimate over all {res['n_blind']} blind questions:")
    for k, label in (("full", "complete answer"), ("partial", "partial"), ("nothing", "no content")):
        C.log(f"[redband]   {label:<16} {100*w[k]:.0f}%")
    (C.OUT / "weighted.json").write_text(json.dumps(res, ensure_ascii=False, indent=1),
                                         encoding="utf-8")


if __name__ == "__main__":
    import sys
    if "--report-only" not in sys.argv:
        probe_red()
    report()
