# -*- coding: utf-8 -*-
"""One arm of the paired window-shape measurement.

The knobs are read at backend import time, so each arm has to be its own
process. This module is that process: set the three RETRIEVE_* variables, name
a tag, and it probes and grades the SAME 72 questions the other arm gets.

    RETRIEVE_MAX_CHUNKS=8  RETRIEVE_MAX_PER_DOC=4 RETRIEVE_TOP_DOC_DEPTH=4 \
        python -m night.arm_window win_prod
    RETRIEVE_MAX_CHUNKS=16 RETRIEVE_MAX_PER_DOC=3 RETRIEVE_TOP_DOC_DEPTH=2 \
        python -m night.arm_window win16

Why these 72: the realstyle wave is the only held-out set left. The window
configs were chosen against the 59 pilot-150 arbitration targets, so pilot-150
is this change's tuning set and realstyle is the honest yardstick. The wave was
generated without seeing the corpus (`c884d19`), and 449 of the night's blind
questions are spent.

⚠ Both arms must run against the SAME corpus. Seven orders were deepened on
2026-08-28 (`661e6c3`); an arm compared against `grades_realstyle.jsonl`, which
predates that, would confound the deepening with the window shape. So the
before side is re-run rather than reused, and it costs a second arm.

`night.probe.build_requests` turns RETRIEVE_STRICT on for the duration, so a
run whose retrieval helpers are failing dies instead of composing a false
measurement (`d44fdc7`).
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

QUESTIONS = C.OUT / "realstyle_questions.json"


def main(tag: str) -> int:
    rows = json.loads(QUESTIONS.read_text(encoding="utf-8"))
    out = C.OUT / f"probe_{tag}.jsonl"
    if out.exists():
        safe_print(f"[arm] {out.name} already on disk — refusing to pay twice. "
                   f"Delete it to re-run.")
        return 1

    safe_print(f"[arm] {tag}: {len(rows)} questions | "
               f"chunks={backend.MAX_CONTEXT_CHUNKS} "
               f"cap={backend.RETRIEVE_MAX_PER_DOC} "
               f"depth={backend.RETRIEVE_TOP_DOC_DEPTH}")

    # Batch, not sync: half the price, and the price is why. The one number
    # quoted to the user on 2026-08-28 came from `probe-realstyle` ($2.25),
    # which `batch_probe-realstyle.json` shows was itself a batch run — quoting
    # a batch actual for a sync run is the same 2x mistake this project has now
    # made three times. Sync costs ~$4.50 an arm.
    #
    # ⚠ The arms must run SEQUENTIALLY. night.ledger guards with a
    # threading.Lock, which is per-process: two arms in parallel race on
    # ledger.json and one arm's entries are lost.
    ledger = Ledger(C.LEDGER)
    reqs, meta = build_requests(rows)
    run_batch(reqs, meta, ledger, out, f"probe-{tag}")
    grade_file(out, ledger, f"grade-{tag}")
    safe_print(f"[arm] {tag} done -> {out.name}, grades_{tag}.jsonl")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "win_prod"))
