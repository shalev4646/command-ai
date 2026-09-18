# -*- coding: utf-8 -*-
"""head-100, option A: the paid arm — a fixed sample of the HELD half through the
real two-pass production path. Plan, price and criterion: PAID_RUN_PLAN.md.

Phases, because a batch outlives the session that submitted it:

    sample   write sample_A.json — deterministic (seeded), committed BEFORE any
             answer exists, so the sample cannot be picked after the fact
    dry      FREE price estimate from the free window of the sampled questions
    p1       PAID — first pass for every sampled question          (batch)
    p2       PAID — second pass only where the first answer declared a gap,
             seeded with that answer, exactly as production does    (batch)
    grade    PAID (cents) — night.grade on the final answers (p2 over p1)
    report   FREE — the criterion, plus the deterministic source check

    venv\\Scripts\\python.exe -m night.head100.arm dry

`p1`/`p2`/`grade` refuse to run without an API key; this worktree has no `.env`
on purpose. They are meant to run from the tree that production is built from,
after the free pre-checks in PAID_RUN_PLAN.md.
"""
from __future__ import annotations

import json
import os
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from common import safe_print  # noqa: E402
from night import config as C  # noqa: E402

HERE = Path(__file__).resolve().parent
TARGETS = HERE / "targets.json"
SAMPLE = HERE / "sample_A.json"
TAG = "head100A"
SEED = 20260918
N_ANSWERABLE = 40

# Calibrated on the ledger's own receipts (2026-09-18): Opus batch answers cost
# ≈ $0.010 fixed (system prompt + output) + $0.0115 per 1,000 context words —
# blocks6 $0.054 @3,844 words, clean3 $0.028 @1,806, win_prod $0.021 @920.
FIXED_USD, PER_KWORD_USD = 0.010, 0.0115
# share of first answers that declared a gap and bought a second pass: 63 of 72
# on the frozen ruler. Head questions should be lower; the estimate uses both.
P2_RATES = (0.6, 0.875)
COMPOSE_USD = 0.010            # HyDE + router per composed request (Haiku, not batched)


def _group(tid: str) -> str:
    for k in ("Res", "Cmd"):
        if tid.startswith(k):
            return k
    return tid[0]


def make_sample() -> list[dict]:
    """40 answerable HELD phrasings, proportional to the topic groups, plus all
    10 HELD no-order phrasings. One phrasing per topic at most."""
    rows = [r for r in json.loads(TARGETS.read_text(encoding="utf-8")) if r["split"] == "held"]
    ans = [r for r in rows if r["doc_id"]]
    noorder = [r for r in rows if not r["doc_id"]]
    rng = random.Random(SEED)
    by: dict[str, list[dict]] = {}
    for r in ans:
        by.setdefault(_group(r["topic_id"]), []).append(r)
    # largest-remainder allocation of N_ANSWERABLE over the groups
    quota = {g: N_ANSWERABLE * len(v) / len(ans) for g, v in by.items()}
    take = {g: int(q) for g, q in quota.items()}
    for g, _ in sorted(quota.items(), key=lambda kv: -(kv[1] - int(kv[1])))[:N_ANSWERABLE - sum(take.values())]:
        take[g] += 1
    picked: list[dict] = []
    for g in sorted(by):
        picked += rng.sample(sorted(by[g], key=lambda r: r["id"]), take[g])
    return sorted(picked, key=lambda r: r["id"]) + sorted(noorder, key=lambda r: r["id"])


def _as_probe_rows(sample: list[dict]) -> list[dict]:
    return [{"id": r["id"], "q": r["question"], "clean_q": r["question"], "role": r["role"],
             "source": "head100", "band": "head100", "target_doc": r["doc_id"]} for r in sample]


def _need_key() -> None:
    if not (os.environ.get("ANTHROPIC_API_KEY") or (ROOT / ".env").exists()):
        raise SystemExit("[arm] no API key in this tree — the paid phases run from the production "
                         "tree only, after the pre-checks in PAID_RUN_PLAN.md.")


def cmd_sample() -> int:
    if SAMPLE.exists():
        safe_print(f"[arm] {SAMPLE.name} already written — the sample is fixed; not rewriting it.")
        return 1
    s = make_sample()
    SAMPLE.write_text(json.dumps(s, ensure_ascii=False, indent=1), encoding="utf-8")
    groups: dict[str, int] = {}
    for r in s:
        groups[_group(r["topic_id"])] = groups.get(_group(r["topic_id"]), 0) + 1
    safe_print(f"[arm] sample: {len(s)} questions ({sum(1 for r in s if r['doc_id'])} with a target, "
               f"{sum(1 for r in s if not r['doc_id'])} no-order) by group {groups}")
    return 0


def cmd_dry() -> int:
    """Free: the free window of every sampled question -> words -> dollars."""
    import backend
    sample = json.loads(SAMPLE.read_text(encoding="utf-8"))
    hyde, backend.RETRIEVE_HYDE = backend.RETRIEVE_HYDE, False
    words = []
    try:
        for r in sample:
            win = backend.retrieve_for_role(r["question"], r["role"], route=set(), widen=False)
            win = backend.widen_context(win, r["question"], r["role"], route=set())
            words.append(sum(len(c["text"].split()) for c in win))
    finally:
        backend.RETRIEVE_HYDE = hyde
    n = len(sample)
    per = [FIXED_USD + PER_KWORD_USD * w / 1000 for w in words]
    p1 = sum(per)
    mean = p1 / n
    safe_print(f"[arm] dry: {n} questions, window mean {sum(words)//n} words (max {max(words)})")
    for rate in P2_RATES:
        p2 = rate * n * mean
        total = p1 + p2 + COMPOSE_USD * n * (1 + rate) + 0.15
        safe_print(f"[arm]   second pass on {int(rate*100)}%: pass1 ${p1:.2f} + pass2 ${p2:.2f} "
                   f"+ compose ${COMPOSE_USD*n*(1+rate):.2f} + grading ~$0.15  =  ~${total:.2f}")
    return 0


def cmd_pass(which: str) -> int:
    _need_key()
    import backend
    from night.ledger import Ledger
    from night.probe import build_requests, run_batch
    out = C.OUT / f"probe_{TAG}_{which}.jsonl"
    if out.exists():
        safe_print(f"[arm] {out.name} already on disk — refusing to pay twice.")
        return 1
    rows = _as_probe_rows(json.loads(SAMPLE.read_text(encoding="utf-8")))
    if which == "p2":
        first = {r["id"]: r for r in C.read_jsonl(C.OUT / f"probe_{TAG}_p1.jsonl")}
        if not first:
            safe_print("[arm] no first pass on disk — run p1 (and night.collect) first.")
            return 1
        rows = [{**r, "first_answer": first[r["id"]]["answer"]} for r in rows
                if first.get(r["id"], {}).get("answer") and backend.lacked_from(first[r["id"]]["answer"])]
        safe_print(f"[arm] second pass for {len(rows)} of {len(first)} first answers that declared a gap")
    ledger = Ledger(C.LEDGER)
    reqs, meta = build_requests(rows)
    run_batch(reqs, meta, ledger, out, f"probe-{TAG}_{which}")
    return 0


def _final_rows() -> list[dict]:
    p1 = {r["id"]: r for r in C.read_jsonl(C.OUT / f"probe_{TAG}_p1.jsonl")}
    p2 = {r["id"]: r for r in C.read_jsonl(C.OUT / f"probe_{TAG}_p2.jsonl")}
    return [{**p1[i], **({"answer": p2[i]["answer"], "sources": p2[i].get("sources"),
                          "second_pass": True} if i in p2 and p2[i].get("answer") else {})}
            for i in p1]


def cmd_grade() -> int:
    _need_key()
    from night.grade import grade_file
    from night.ledger import Ledger
    out = C.OUT / f"probe_{TAG}.jsonl"
    C.write_jsonl(out, _final_rows())
    grade_file(out, Ledger(C.LEDGER), f"grade-{TAG}")
    return 0


def cmd_report() -> int:
    """The criterion as graded, and the free source check that feeds the review:
    a 'full' answer whose sources never included the target order is the
    confident-and-wrong candidate — read those first."""
    targets = {r["id"]: r for r in json.loads(SAMPLE.read_text(encoding="utf-8"))}
    rows = C.read_jsonl(C.OUT / f"grades_grade-{TAG}.jsonl")
    ans = [r for r in rows if targets[r["id"]]["doc_id"]]
    full = [r for r in ans if (r.get("grade") or {}).get("level") == "full"]
    suspect = [r["id"] for r in full if targets[r["id"]]["doc_id"] not in (r.get("sources") or [])]
    safe_print(f"[arm] with a target: {len(full)}/{len(ans)} graded full "
               f"({100*len(full)/max(1,len(ans)):.0f}%) — criterion 85% AFTER review")
    safe_print(f"[arm] review first — graded full without the target order among the sources: {suspect}")
    no = [r for r in rows if not targets[r["id"]]["doc_id"]]
    lv: dict[str, int] = {}
    for r in no:
        k = (r.get("grade") or {}).get("level", "ungraded")
        lv[k] = lv.get(k, 0) + 1
    safe_print(f"[arm] no-order topics: {lv} — every 'full' here must be read: it may be an invented rule")
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "dry"
    raise SystemExit({"sample": cmd_sample, "dry": cmd_dry, "p1": lambda: cmd_pass("p1"),
                      "p2": lambda: cmd_pass("p2"), "grade": cmd_grade, "report": cmd_report}[cmd]())
