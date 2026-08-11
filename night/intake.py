"""One command from a folder of downloaded orders to a gated, curated corpus.

The critical path is acquisition, and acquisition is manual — the orders site
sits behind bot protection that this project does not circumvent. So the job
here is to make each delivery cheap: drop PDFs in `pdf-ldf_law/`, run this, and
every downstream step happens in the right order with the right guards.

    ingest  ->  curate  ->  grow the gate  ->  verify  ->  report

Two properties matter more than speed. Every stage is resumable, because a wave
of 85 orders will not finish in one sitting. And the gate grows with the corpus:
415 cases were built against 98 orders, roughly four per order, and adding 85
orders without adding cases would quietly retire the only instrument that can
tell us whether the additions broke something.
"""
from __future__ import annotations

import subprocess
import sys

import backend
from night import config as C
from night.ledger import Ledger

PY = sys.executable


def _new_doc_ids(before: set[str]) -> set[str]:
    backend.load_documents.cache_clear() if hasattr(backend.load_documents, "cache_clear") else None
    return {d["document_id"] for d in backend.load_documents() if d.get("document_id")} - before


def step(name: str, argv: list[str]) -> bool:
    C.log(f"[intake] ===== {name} =====")
    r = subprocess.run([PY, *argv], cwd=str(C.ROOT))
    if r.returncode != 0:
        C.log(f"[intake] {name} FAILED (exit {r.returncode}) — stopping")
        return False
    return True


def main() -> None:
    ledger = Ledger(C.LEDGER)
    before = {d["document_id"] for d in backend.load_documents() if d.get("document_id")}
    C.log(f"[intake] starting at {len(before)} orders, ${ledger.remaining():.2f} of budget")

    # 1 — ingest whatever is new in pdf-ldf_law/. ensure_pdfs_ingested is the
    #     app's own entry point, so a wave lands exactly as the existing 98 did.
    added = backend.ensure_pdfs_ingested()
    new = _new_doc_ids(before)
    C.log(f"[intake] ingested {len(added)} files -> {len(new)} new orders")
    if not new:
        C.log("[intake] nothing new in pdf-ldf_law/ — drop the downloads there first")
        return

    # 2 — curate: every new order needs a key-facts block, or an anchor win
    #     hands the model a raw chunk (measured: usually the colophon).
    if not step("curate key-facts", ["-m", "night.curate"]):
        return

    # 3 — grow the gate before measuring anything. 3 probes per new order keeps
    #     the density the existing 415 cases were built at.
    if not step("grow the gate", ["-m", "night.probes", *sorted(new)]):
        return

    # 4 — verify nothing that used to work stopped working.
    if not step("run the gate", ["-m", "night.gate"]):
        return

    # 5 — re-measure on the SAME 54 saved questions, so before/after is paired
    #     rather than two independent samples.
    step("re-measure", ["-m", "night.remeasure"])

    C.log(Ledger(C.LEDGER).summary())


if __name__ == "__main__":
    main()
