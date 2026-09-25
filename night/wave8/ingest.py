# -*- coding: utf-8 -*-
"""Wave-8 ingestion from OCR text — the 11 definitions in night/wave8/defs, $0.

For each definition: its OCR text (sources_pending/ocr/<source stem>.ocr.txt,
after the eye-fixes of EYEFIX_26.09.md) -> ingestion.text_ingest.document_from_def
(same document shape the PDF path writes; the curated block rides inside) ->
the two free gates on the block against the document's own raw_text
(night.curate.check, night.numbers) -> write.

    venv\\Scripts\\python.exe -m night.wave8.ingest                 # dry: gates + JSON into the scratch folder
    INGEST_FROM_TEXT=1 venv\\Scripts\\python.exe -m night.wave8.ingest --write [--only <id> ...]

Dry needs no flag and touches nothing under storage/. --write needs
INGEST_FROM_TEXT=1 (text_ingest refuses otherwise), never overwrites an existing
document, and re-indexes each written document (local ONNX embeddings, no API).
INGEST_SHORT_ORDERS is forced on here: nine of the eleven are one-page orders
that the sparse-text gate exists to reject when the flag is off.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("INGEST_SHORT_ORDERS", "1")   # before ingestion imports read it

from common import safe_print  # noqa: E402
from ingestion import text_ingest as T  # noqa: E402
from night import numbers as N  # noqa: E402
from night.curate import check  # noqa: E402

DEFS = ROOT / "night" / "wave8" / "defs"
OCR = ROOT / "sources_pending" / "ocr"
SCRATCH = Path(os.environ.get("WAVE8_DRY_DIR") or (ROOT / "night" / "wave8" / "_dry"))


def ocr_path_for(defn: dict) -> Path:
    stem = Path(defn["source_pdf"]).stem
    return OCR / f"{stem}.ocr.txt"


def gate(doc: dict) -> tuple[list[str], list[str], list[tuple]]:
    section = doc["sections"][0]
    digit_free = bool(section.get("digit_free"))
    problems, warnings = check(section, doc["raw_text"], digit_free=digit_free)
    misses = []
    if not digit_free:
        for c in section["clauses"]:
            m = [x for x in N.numbers_in(c["text"]) if not N.present(x, doc["raw_text"])]
            if m:
                misses.append((c["number"][:40], m))
    return problems, warnings, misses


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--only", action="append", default=[])
    args = ap.parse_args()
    out_dir = T.JSON_STORE if args.write else SCRATCH
    rc, written = 0, []
    for f in sorted(DEFS.glob("*.json")):
        defn = json.loads(f.read_text(encoding="utf-8"))
        did = defn["document_id"]
        if args.only and did not in args.only:
            continue
        src = ocr_path_for(defn)
        if not src.exists():
            safe_print(f"[ingest] {did}: no OCR text at {src.name}"); rc = 1; continue
        try:
            doc = T.document_from_def(defn, src)
        except Exception as e:  # noqa: BLE001
            safe_print(f"[ingest] {did}: build failed — {type(e).__name__}: {e}"); rc = 1; continue
        problems, warnings, misses = gate(doc)
        words = [len(c["text"].split()) for c in doc["sections"][0]["clauses"]]
        safe_print(f"[ingest] {did:<14} {len(doc['raw_text']):>6} chars raw, block {len(words)} clauses (max {max(words)} words), "
                   f"roles {','.join(doc['roles'])}, problems {len(problems)}, warnings {len(warnings)}, number misses {len(misses)}"
                   f"{' , status_note' if doc.get('status_note') else ''}")
        for x in problems:
            safe_print(f"          PROBLEM {x[:150]}")
        for x in misses:
            safe_print(f"          NUMBER  {x}")
        if problems or misses:
            rc = 1
            continue
        try:
            path = T.write_document(doc, out_dir)
        except (PermissionError, FileExistsError) as e:
            safe_print(f"          not written: {e}"); rc = 1; continue
        if args.write:
            from storage import vector_store as vs
            n = vs.index_document(doc, save_cache=True)
            safe_print(f"          WRITTEN {path.name} — re-indexed {n} chunks")
            written.append(did)
        else:
            safe_print(f"          dry -> {path}")
    if args.write:
        safe_print(f"[ingest] written: {written or 'nothing'} — now the corpus gates against k7base")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
