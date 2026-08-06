"""Ingest only the PDFs that aren't in the corpus yet.

_reingest.py calls ingest_folder() with no `skip`, so it re-runs every PDF in
pdf-ldf_law/ — one Sonnet call per file (~4¢, per analyze_document's own
docstring), i.e. ~$3.30 for the current 87 to add a handful. Hand-maintained
fields do survive (ingest() carries sections / annex_exceptions /
anchor_questions / questions_curated forward), but paying to churn dozens of
working documents in order to add a few is the wrong trade.

`skip` already exists on ingest_folder; nothing passes it. This does.

Use _reingest.py when the *chunking or extraction* changed and every document
genuinely has to be rebuilt. Use this when you dropped new PDFs in.

Run:  python _ingest_new.py         → list what would run + cost, no API calls
      python _ingest_new.py --go    → ingest, then rebuild derived artifacts
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

PDF_DIR = ROOT / "pdf-ldf_law"
STORE = ROOT / "storage" / "json_store"
COST_PER_ORDER = 0.04  # analyze_document on Sonnet 5, per its own docstring

already = set()
for f in STORE.glob("*.json"):
    src = json.loads(f.read_text(encoding="utf-8")).get("source_file")
    if src:
        already.add(src)

pending = sorted(p.name for p in PDF_DIR.glob("*.pdf") if p.name not in already)

print(f"PDF בתיקייה: {len(list(PDF_DIR.glob('*.pdf')))} | כבר בקורפוס: {len(already)}")
print(f"ממתינים להטמעה: {len(pending)}")
for name in pending:
    print(f"   {name:<18} {(PDF_DIR / name).stat().st_size / 1024:>6.0f}KB")
print(f"\nעלות משוערת: ~${len(pending) * COST_PER_ORDER:.2f}"
      f"  (מול ~${len(already) * COST_PER_ORDER:.2f} לריצה מלאה של _reingest.py)")

if "--go" not in sys.argv:
    print("\nריצה יבשה. להרצה אמיתית: --go")
    sys.exit(0)
if not pending:
    print("\nאין מה להטמיע.")
    sys.exit(0)

from ingestion.pdf_to_json import ingest_folder
from storage.vector_store import get_index_stats

print("\n" + "=" * 60)
done = ingest_folder(PDF_DIR, skip=already)
print(f"\nהוטמעו: {len(done)} | סה\"כ צ'אנקים: {get_index_stats()['total_chunks']}")

# The derived artifacts the UI reads — clause deep-links and freshness dates.
# Skipping them is the trap _reingest.py's own docstring warns about: the
# links keep pointing at the old layout, silently.
for builder in ("_build_clause_pages.py", "_build_doc_dates.py"):
    print(f"\n--- {builder}")
    subprocess.run([sys.executable, str(ROOT / builder)], cwd=ROOT)
