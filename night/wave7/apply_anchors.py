# -*- coding: utf-8 -*-
"""Apply night/wave7/anchors_phrasing.json — hand-written anchors, $0, gated.

For every order in the file: night.anchors.check (mirror of a saved eval
question, duplicate of an existing question, risk topic the order is silent
about) → the kept anchors are APPENDED to the order's `anchor_questions`
(never shown in the UI, retrieval only), the JSON is rewritten the way
night.anchors.run writes it, and storage/vector_store.index_document
re-indexes the order (embeds the new strings, saves embedding_cache.npz once
at the end). The applied record goes to night/out/anchors_phrasing_applied.json.

    venv\\Scripts\\python.exe -m night.wave7.apply_anchors            # dry: gates only
    venv\\Scripts\\python.exe -m night.wave7.apply_anchors --write    # write + index

⛔ A corpus write. Only after the coordinating session said the corpus is
open, never while a paired measurement is in flight, and the gates of
night/ANCHORS_PHRASING_CRITERION.md run before and after on the same tree.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from common import safe_print  # noqa: E402
from night import anchors as A  # noqa: E402
from night.rehearse import doc_path  # noqa: E402

HERE = Path(__file__).resolve().parent
SPEC = HERE / "anchors_phrasing.json"
RECORD = ROOT / "night" / "out" / "anchors_phrasing_applied.json"


def main(write: bool) -> int:
    import backend
    docs = {d["document_id"]: d for d in backend.load_documents() if d.get("document_id")}
    evals = A.eval_questions()
    if not evals:
        raise SystemExit("[wave7] refusing: no saved eval questions to guard against mirroring")
    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    plan: dict[str, list[str]] = {}
    for doc_id, qs in spec.items():
        if doc_id.startswith("_"):
            continue
        d = docs.get(doc_id)
        if d is None:
            safe_print(f"[wave7] {doc_id}: NOT in corpus — skipped")
            continue
        kept, _problems, warnings = A.check(qs, d, evals)   # MIN_KEEP is the generator's rule, not ours
        for w in warnings:
            safe_print(f"[wave7] {doc_id}: {w}")
        safe_print(f"[wave7] {doc_id:<18} {len(kept)}/{len(qs)} pass the gates "
                   f"(existing anchors {len(d.get('anchor_questions') or [])})")
        if kept:
            plan[doc_id] = kept
    total = sum(len(v) for v in plan.values())
    safe_print(f"[wave7] {total} anchors for {len(plan)} orders")
    if not write:
        safe_print("[wave7] dry run — nothing written. Use --write to apply.")
        return 0

    from storage.vector_store import _save_emb_cache, index_document
    record = {"_note": "night/wave7/anchors_phrasing.json applied 2026-09-22 (ANCHORS_PHRASING_CRITERION); "
                       "appended to anchor_questions, re-indexed, cache saved once", "applied": {}}
    for doc_id, kept in plan.items():
        path = doc_path(doc_id)
        raw_doc = json.loads(path.read_text(encoding="utf-8"))
        existing = list(raw_doc.get("anchor_questions") or [])
        new = [q for q in kept if q not in existing]
        raw_doc["anchor_questions"] = existing + new
        path.write_text(json.dumps(raw_doc, ensure_ascii=False, indent=2), encoding="utf-8")
        n = index_document(json.loads(path.read_text(encoding="utf-8")), save_cache=False)
        record["applied"][doc_id] = new
        safe_print(f"[wave7] {doc_id:<18} +{len(new)} anchors -> {len(raw_doc['anchor_questions'])} total, {n} chunks")
    _save_emb_cache()
    RECORD.write_text(json.dumps(record, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    safe_print(f"[wave7] written; record -> {RECORD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main("--write" in sys.argv[1:]))
