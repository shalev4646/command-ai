"""Reingest every order PDF, then rebuild the derived data artifacts.

Run this after adding/replacing a PDF in pdf-ldf_law/. Reingestion refreshes
the vector store; the two builders regenerate the JSON that the UI features
read — clause->page deep-links (storage/clause_pages.json) and per-order
version dates (storage/doc_dates.json). They ran separately before, so a
reingest that forgot them left the deep-links and freshness badges silently
pointing at the OLD layout. Chaining them here removes that trap.

Builders are subprocesses on purpose: they need fitz and print their own
review output, and a failure in one must not abort the others (the reingest
itself already succeeded by then).
"""
import subprocess
import sys

sys.path.insert(0, "D:/app_soldier")
from ingestion.pdf_to_json import ingest_folder
from storage.vector_store import get_index_stats

ingest_folder(r"D:\app_soldier\pdf-ldf_law")
stats = get_index_stats()
print(f"\nTotal chunks in DB: {stats['total_chunks']}")

# Regenerate the derived artifacts the UI reads. Keep going if one fails —
# a broken builder should surface loudly, not silently skip the others.
_BUILDERS = ("_build_clause_pages.py", "_build_doc_dates.py")
print("\n" + "=" * 60)
print("Rebuilding derived data (clause pages, doc dates)...")
print("=" * 60)
for script in _BUILDERS:
    print(f"\n$ python {script}")
    result = subprocess.run([sys.executable, script], cwd="D:/app_soldier")
    if result.returncode != 0:
        print(f"!! {script} exited {result.returncode} — its artifact may be stale; rerun it manually.")

print("\nDone. Run `python eval.py --no-llm` to gate, then commit the "
      "refreshed embedding_cache.npz + storage/*.json together.")
