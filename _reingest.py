import sys
sys.path.insert(0, "D:/app_soldier")
from ingestion.pdf_to_json import ingest_folder
from storage.vector_store import get_index_stats

ingest_folder(r"D:\app_soldier\pdf-ldf_law")
stats = get_index_stats()
print(f"\nTotal chunks in DB: {stats['total_chunks']}")
