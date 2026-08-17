# -*- coding: utf-8 -*-
"""A re-ingest must never cost an order its curated block.

Curation is the expensive, hand-verified half of this corpus and retrieval
serves curated orders only, so an order that loses its key-facts section does
not degrade — it disappears. On 2026-08-17 four duplicate downloads ("order
(1).pdf") were ingested and three orders lost 1, 7 and 8 curated clauses in
silence, because the preservation logic matched the previous JSON by
`source_file` while the WRITE landed on the title-derived slug.

    venv\\Scripts\\python.exe tests\\test_ingest_preserves_curation.py
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from ingestion import pdf_to_json as P

CURATED = {
    "document_id": "33.0149",
    "title": "יצירות, ביצועים, אמצאות ופטנטים",
    "source_file": "33-0149.pdf",
    "raw_text": "טקסט גולמי כלשהו",
    "sections": [{"id": "key-facts", "title": "עיקרי הפקודה",
                  "clauses": [{"number": "מי הבעלים?", "text": "צה\"ל ומשהב\"ט."}]}],
    "anchor_questions": ["למי שייכת אמצאה שהמצאתי בשירות?"],
    "suggested_questions": {"soldier": ["שאלה מאוצרת"]},
    "questions_curated": True,
}


def main() -> int:
    failed = []

    def check(name, cond, detail=""):
        print(("  PASS  " if cond else "  FAIL  ") + name + (f"   {detail}" if detail else ""))
        if not cond:
            failed.append(name)

    with tempfile.TemporaryDirectory() as tmp:
        out_dir = Path(tmp)
        slug = P.re.sub(r"[^\w֐-׿-]", "-", CURATED["title"])[:60]
        (out_dir / f"{slug}.json").write_text(
            json.dumps(CURATED, ensure_ascii=False), encoding="utf-8")

        # the duplicate download: same order, a source filename nothing claims
        by_source = P._existing_doc_for(out_dir, "33-0149 (1).pdf")
        check("source-file lookup misses a renamed duplicate — the original bug",
              by_source is None)

        # what ingest() now does: fall back to the destination it is about to
        # overwrite. Exercised at this level because ingest() itself extracts a
        # real PDF and calls the paid API.
        dest = out_dir / f"{slug}.json"
        prev = by_source
        if prev is None and dest.exists():
            prev = json.loads(dest.read_text(encoding="utf-8"))
        check("destination lookup finds it", prev is not None)

        meta = {"document_id": "33.0149", "title": CURATED["title"],
                "source_file": "33-0149 (1).pdf", "raw_text": "טקסט"}
        for field in ("sections", "annex_exceptions", "anchor_questions"):
            if (prev or {}).get(field):
                meta[field] = prev[field]
        if prev and prev.get("questions_curated"):
            meta["suggested_questions"] = prev.get("suggested_questions") or {}
            meta["questions_curated"] = True

        kf = [s for s in meta.get("sections", []) if "key-facts" in (s.get("id") or "")]
        check("the curated key-facts section survives", len(kf) == 1)
        check("its clauses are intact", bool(kf) and len(kf[0]["clauses"]) == 1)
        check("anchor questions survive", meta.get("anchor_questions") == CURATED["anchor_questions"])
        check("the curated question bank is not regenerated",
              meta.get("questions_curated") is True
              and meta["suggested_questions"] == CURATED["suggested_questions"])

        # a genuinely new order must still be written normally
        fresh = P._existing_doc_for(out_dir, "99-9999.pdf")
        check("a new order has no previous doc to preserve", fresh is None)

    print(f"\n{'FAILED: ' + ', '.join(failed) if failed else 'all checks passed'}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
