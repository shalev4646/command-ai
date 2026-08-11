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

import re
import subprocess
import sys

import backend
from night import config as C
from night.ledger import Ledger

PY = sys.executable


def check_ids(new: set[str]) -> list[tuple[str, str, str]]:
    """Flag orders whose number was read backwards out of the PDF header.

    Wave 1 arrived with five of these: 310214x.pdf became order "4120.13",
    500405x.pdf became "5040.05". The extractor handles Hebrew letters fine —
    the body text of all five is clean, and 550 curated clauses across 102
    documents contain zero reversed numbers — but the header band, where the
    order number and publication date sit, comes out with its digit runs
    mirrored. The damage is therefore narrow and entirely in derived metadata.

    It is also silent, which is why this runs on every wave instead of living in
    a note: a wrong document_id is not a crash. The order simply stops answering
    to its real number, and any citation shown to a soldier points at an order
    that does not exist. The filename carries the number the site published it
    under, so it is the check: reverse the id and see if the filename agrees.
    """
    bad = []
    for d in backend.load_documents():
        did = str(d.get("document_id", ""))
        if did not in new:
            continue
        want = _number_from_filename(str(d.get("source_file", "")))
        if not want:
            continue                      # slug-named guides, no number to check
        core = did.replace("PM-", "").lstrip("0")
        if core not in (want, want.replace(".", "")):
            bad.append((did, want, str(d.get("source_file", ""))))
    return bad


def _number_from_filename(source_file: str) -> str | None:
    """`500405x.pdf` and `פמ-33-0113-בחירות...` both mean order NN.NNNN.

    Reading the whole digit run only works for the flat names; the dashed
    Hebrew ones split the number across two groups, and grabbing the first
    5-7 digits anywhere in the string picks up fragments of the Hebrew title
    instead — which reported `פמ-33-0113` as order "011.1202" and buried the
    real hits under false alarms.
    """
    runs = re.findall(r"\d+", re.sub(r"\.pdf$", "", source_file, flags=re.I))
    if runs and len(runs[0]) in (5, 6):
        digits = runs[0]
    elif len(runs) > 1 and len(runs[0]) <= 2 and len(runs[1]) == 4:
        digits = runs[0] + runs[1]
    else:
        return None
    return f"{digits[:-4].lstrip('0') or '0'}.{digits[-4:]}"


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

    # 1b — a reversed order number is cheap to fix now and expensive later: the
    #      id is baked into gate cases and citations the moment curation runs.
    for did, want, src in check_ids(new):
        C.log(f"[intake] SUSPECT ID {did} — {src} suggests {want}; "
              f"fix storage/json_store before curating")

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
