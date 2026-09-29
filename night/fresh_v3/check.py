# -*- coding: utf-8 -*-
"""fresh_v3: re-prove every quote against the index (the head-100 build.py rule).

    venv\\Scripts\\python.exe night/fresh_v3/check.py

The set is HELD: this prints ids and counts only, never a question or an outcome
per row. A quote that no indexed chunk of its order contains means the corpus moved
under the set (a re-ingest, a new raw): fix the quote from the page, not the row.
Exit code 1 on any failure.
"""
from __future__ import annotations

import collections
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import storage.vector_store as vs  # noqa: E402

SET = Path(__file__).resolve().parent / "questions.json"


def _norm(s: str) -> str:
    s = re.sub(r'["״׳\'“”‘’]', "", s or "")
    return re.sub(r"\s+", " ", s).strip()


def main() -> int:
    rows = json.loads(SET.read_text(encoding="utf-8"))
    by_doc: dict[str, list[str]] = {}
    for c in vs._get_corpus():
        by_doc.setdefault(c["doc_id"], []).append(_norm(c["text"]))
    bad = []
    for r in rows:
        if not r["verdict"].startswith("ANSWERED"):
            if r.get("doc_id") or r.get("verified_quotes"):
                bad.append(f"{r['id']}: no-rule row carries a target")
            continue
        texts = by_doc.get(r["doc_id"])
        if not texts:
            bad.append(f"{r['id']}: order {r['doc_id']} not in the index")
            continue
        if not r["verified_quotes"]:
            bad.append(f"{r['id']}: answerable row without a quote")
        for q in r["verified_quotes"]:
            if not any(_norm(q) in t for t in texts):
                bad.append(f"{r['id']}: a quote is not in any chunk of {r['doc_id']}")
    for b in bad:
        print("ERROR", b)
    print(f"rows={len(rows)} {dict(collections.Counter(r['verdict'] for r in rows))} "
          f"roles={dict(collections.Counter(r['role'] for r in rows))} errors={len(bad)}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
