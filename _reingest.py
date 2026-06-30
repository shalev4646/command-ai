import sys
sys.path.insert(0, "D:/app_soldier")
from ingestion.pdf_to_json import ingest
from storage.vector_store import get_index_stats
from pathlib import Path

pdf_dir = Path(r"D:\app_soldier\pdf-ldf_law")
for pdf in sorted(pdf_dir.glob("*.pdf")):
    print(f"Ingesting: {pdf.name}")
    result = ingest(str(pdf))
    print(f"  Saved: {result.name}")

stats = get_index_stats()
print(f"\nTotal chunks in DB: {stats['total_chunks']}")
