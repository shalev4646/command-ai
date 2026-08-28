# -*- coding: utf-8 -*-
"""The second-search arm, measured against an arm that is already on disk.

The second pass only fires where the first answer declared a gap, so the
questions it cannot touch are identical in both arms by construction. That is
what makes this arm cheap: the 22 of 72 answers that did NOT declare a gap are
carried over from `win_prod` verbatim, unbilled, and only the 50 that did are
re-composed and re-answered.

    RETRIEVE_SECOND_PASS=4 python -m night.arm_second second4

⚠ The base arm must have run under the SAME retrieval config as this one, minus
the second pass. `win_prod` is production (n=8, cap=4, depth=4), so this arm
must not also set RETRIEVE_MAX_CHUNKS — otherwise the comparison carries the
window-shape change measured the same day and neither cause is separable.

⚠ And it must have run against the SAME corpus. `win_prod` was probed after the
2026-08-28 deepening (`661e6c3`); nothing has touched json_store since.

Grading reads the union file, so the paired comparison is over all 72.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import backend
from common import safe_print
from night import config as C
from night.grade import grade_file
from night.ledger import Ledger
from night.probe import build_requests, run_batch

BASE = "grade-win_prod"          # the arm whose answers seed the second pass
QUESTIONS = C.OUT / "realstyle_questions.json"


def main(tag: str, control: bool = False) -> int:
    """`control=True` runs the CONTROL arm: the same questions, a fresh model
    call, and NO second search.

    It exists because 63 of the 72 are re-answered, and a fresh call resamples
    the model. Without this arm, "full answers 15 -> 23" cannot be told apart
    from a lucky draw — and this project has been burned by exactly that
    (`57f4fea`: two runs of the identical configuration scored 28% and 40%).
    Resampling alone should move verdicts in BOTH directions; the second-pass
    arm lost zero. The control is what turns that argument into a measurement.
    """
    if control and backend.RETRIEVE_SECOND_PASS != 0:
        safe_print("[arm] a control arm must run with RETRIEVE_SECOND_PASS=0, "
                   f"not {backend.RETRIEVE_SECOND_PASS} — refusing.")
        return 1
    if not control and backend.RETRIEVE_SECOND_PASS <= 0:
        safe_print("[arm] RETRIEVE_SECOND_PASS is 0 — this arm would be a "
                   "byte-for-byte re-run of the base and would prove nothing.")
        return 1
    if backend.MAX_CONTEXT_CHUNKS != 8 or backend.RETRIEVE_MAX_PER_DOC != 4 \
            or backend.RETRIEVE_TOP_DOC_DEPTH != 4:
        safe_print(f"[arm] window is {backend.MAX_CONTEXT_CHUNKS}/"
                   f"{backend.RETRIEVE_MAX_PER_DOC}/{backend.RETRIEVE_TOP_DOC_DEPTH}, "
                   f"but the base arm ran at production 8/4/4. Two changes in "
                   f"one arm are not separable — refusing.")
        return 1

    out = C.OUT / f"probe_{tag}.jsonl"
    if out.exists():
        safe_print(f"[arm] {out.name} already on disk — refusing to pay twice.")
        return 1

    base = {r["id"]: r for r in C.read_jsonl(C.OUT / f"grades_{BASE}.jsonl")}
    if not base:
        safe_print(f"[arm] no base arm on disk — expected grades_{BASE}.jsonl")
        return 1

    questions = json.loads(QUESTIONS.read_text(encoding="utf-8"))
    todo, carried = [], []
    for q in questions:
        prev = base.get(q["id"])
        first = (prev or {}).get("answer") or ""
        if prev and backend.lacked_from(first):
            # The control selects the SAME questions by the SAME rule and
            # simply withholds the seed, so the two arms differ in one thing.
            todo.append({**q} if control else {**q, "first_answer": first})
        elif prev:
            # answered without declaring a gap: the second pass is gated off it,
            # so re-running would buy an identical answer at full price
            carried.append({**prev})

    safe_print(f"[arm] {tag}: {len(todo)} re-answered, {len(carried)} carried "
               f"from {BASE} unbilled | "
               + ("CONTROL — fresh call, no second search"
                  if control else f"reserved seats={backend.RETRIEVE_SECOND_PASS}"))

    ledger = Ledger(C.LEDGER)
    reqs, meta = build_requests(todo)
    run_batch(reqs, meta, ledger, out, f"probe-{tag}")

    # fold the carried answers back in, so grading and the pairing cover all 72
    fresh = C.read_jsonl(out)
    with out.open("w", encoding="utf-8") as fh:
        for r in fresh + carried:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    safe_print(f"[arm] {tag}: {len(fresh)} fresh + {len(carried)} carried = "
               f"{len(fresh) + len(carried)} rows")

    grade_file(out, ledger, f"grade-{tag}")
    safe_print(f"[arm] {tag} done -> {out.name}")
    return 0


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    raise SystemExit(main(args[0] if args else "second4",
                          control="--control" in sys.argv))
