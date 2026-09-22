# -*- coding: utf-8 -*-
"""wave-7 drafts through the project's faithfulness gates — free, writes nothing.

For each night/wave7/defs/*.json: the order must exist in storage/json_store
and have no curated block yet; the draft's clauses run through
night.curate.check against the order's existing raw_text (digit-free mode when
the def says so). Problems block; warnings are for the human review.

    venv\\Scripts\\python.exe -m night.wave7.check_defs
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from common import safe_print  # noqa: E402
from night.curate import check  # noqa: E402

HERE = Path(__file__).resolve().parent
STORE = ROOT / "storage" / "json_store"


def _docs() -> dict[str, dict]:
    out = {}
    for f in STORE.glob("*.json"):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if d.get("document_id"):
            out[d["document_id"]] = d
    return out


def main() -> int:
    docs = _docs()
    rc = 0
    for path in sorted((HERE / "defs").glob("*.json")):
        spec = json.loads(path.read_text(encoding="utf-8"))
        doc_id = spec["document_id"]
        d = docs.get(doc_id)
        if d is None:
            safe_print(f"[wave7] {path.name}: {doc_id} NOT in json_store"); rc = 1
            continue
        if any("key-facts" in str(s.get("id") or "") for s in d.get("sections") or []):
            safe_print(f"[wave7] {path.name}: {doc_id} already has a curated block — a draft must not overwrite it")
            rc = 1
            continue
        digit_free = spec.get("section_id") == "key-facts-nodigits"
        section = {"id": spec.get("section_id", "key-facts"), "clauses": spec["clauses"]}
        problems, warnings = check(section, str(d.get("raw_text", "")), digit_free=digit_free)
        words = sum(len(str(c.get("text", "")).split()) for c in spec["clauses"])
        safe_print(f"[wave7] {doc_id:<12} {len(spec['clauses'])} clauses, {words} words, "
                   f"{'digit-free' if digit_free else 'digits'}: "
                   f"{'OK' if not problems else str(len(problems)) + ' PROBLEMS'}, {len(warnings)} warnings")
        for p in problems:
            safe_print(f"      PROBLEM {p}")
        for w in warnings[:8]:
            safe_print(f"      warn    {w}")
        if problems:
            rc = 1
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
