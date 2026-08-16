"""Finish a batch that outlived the process which submitted it.

A Batch API job is paid for at submit, lives on the server for 24 hours, and
returns whenever the provider's queue gets to it — which on 2026-08-12 meant a
30-request batch still had zero results after three hours. Nothing was wrong
with it; batches simply carry a 24h SLA, not a latency promise.

That makes "keep a session open until it lands" the wrong shape. `probe.run_batch`
now writes a claim ticket at submit time — the batch id plus the `meta` list that
maps each custom_id back to the question it came from — and this reads the ticket
back, collects whatever the batch produced, and runs the grading that would have
followed. Without the ticket the results are unattributable text: the answers
come back keyed by "p0".."p29" and nothing on the server says which question
that was.

    python -m night.collect probe-remeasure
"""
from __future__ import annotations

import json
import sys

import backend
from night import config as C
from night.ledger import Ledger
from night.probe import collect_batch


def run(label: str) -> None:
    ticket = C.OUT / f"batch_{label}.json"
    if not ticket.exists():
        raise SystemExit(f"no claim ticket at {ticket} — nothing to collect")
    t = json.loads(ticket.read_text(encoding="utf-8"))

    b = backend.client.messages.batches.retrieve(t["batch_id"])
    if b.processing_status != "ended":
        C.log(f"[collect] {t['batch_id']} is still {b.processing_status} "
              f"(succeeded={b.request_counts.succeeded}, "
              f"processing={b.request_counts.processing}) — try again later")
        return

    ledger = Ledger(C.LEDGER)
    # the reservation is settled only if this process is the one that made it;
    # a resumed collect passes rid=None so a dead process's settle is not repeated
    collect_batch(t["batch_id"], t["meta"], t.get("rid"), t["out_path"], label, ledger)

    # Any remeasure tag, not just the first one there ever was: runs are tagged
    # (probe-remeasure3, ...) so they stop overwriting each other's history, and
    # a hardcoded label here would silently skip the grading that makes the paid
    # answers mean anything.
    if label.startswith("probe-remeasure"):
        tag = label[len("probe-"):]
        import os
        os.environ["REMEASURE_TAG"] = tag
        from night.grade import grade_file
        grade_file(C.OUT / f"probe_{tag}.jsonl", Ledger(C.LEDGER), tag)
        import importlib
        import night.remeasure as rm
        importlib.reload(rm)          # TAG is read at import time
        rm.report()


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else "probe-remeasure3")
