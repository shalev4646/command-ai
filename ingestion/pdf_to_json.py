import json
import re
import sys
from pathlib import Path

import fitz  # PyMuPDF
import pdfplumber
from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

client = Anthropic()

META_PROMPT = """קרא את תחילת פקודת מטכ"ל והחזר JSON עם מטא-דאטה בלבד (ללא הסברים):

{"document_id": "PM-XX-XXXX", "title": "כותרת הפקודה", "published": "YYYY-MM-DD או null"}

טקסט:
"""

CHUNK_SIZE = 1500  # תווים לכל chunk


def extract_text(pdf_path: Path) -> str:
    """Extract text using PyMuPDF (handles Hebrew RTL correctly)."""
    doc = fitz.open(str(pdf_path))
    pages = [page.get_text() for page in doc]
    doc.close()
    return "\n\n".join(p for p in pages if p.strip())


def extract_metadata(text: str) -> dict:
    """Extract just title/id from the first 2000 chars."""
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=256,
        messages=[{"role": "user", "content": META_PROMPT + text[:2000]}],
    )
    raw = response.content[0].text.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    try:
        return json.loads(raw)
    except Exception:
        return {"document_id": "UNKNOWN", "title": "פקודה לא מזוהה", "published": None}


def split_into_chunks(text: str, doc_id: str, title: str) -> list[dict]:
    """Split raw text into overlapping chunks for indexing."""
    chunks = []
    words = text.split()
    step = CHUNK_SIZE
    overlap = 200

    i = 0
    chunk_num = 0
    while i < len(words):
        chunk_words = words[i:i + step]
        chunk_text = " ".join(chunk_words)
        chunks.append({
            "id": f"{doc_id}__chunk{chunk_num}",
            "text": f"{title}\n{chunk_text}",
            "doc_id": doc_id,
            "title": title,
            "section": f"chunk{chunk_num}",
            "clause": str(chunk_num),
            "tags": "",
        })
        chunk_num += 1
        i += step - overlap

    return chunks


def ingest(pdf_path: str) -> Path:
    pdf = Path(pdf_path)
    text = extract_text(pdf)
    if not text.strip():
        raise ValueError(f"לא נמצא טקסט ב-{pdf.name}")

    meta = extract_metadata(text)
    doc_id = meta.get("document_id", "UNKNOWN")
    title = meta.get("title", pdf.stem)

    # save minimal JSON for sidebar display
    out_dir = Path(__file__).parent.parent / "storage" / "json_store"
    out_dir.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^\w֐-׿-]", "-", title)[:60]
    meta["raw_text"] = text
    out_path = out_dir / f"{slug}.json"
    out_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    # index raw text chunks into vector store
    from storage.vector_store import _get_collection
    col = _get_collection()
    chunks = split_into_chunks(text, doc_id, title)
    col.upsert(
        ids=[c["id"] for c in chunks],
        documents=[c["text"] for c in chunks],
        metadatas=[{k: v for k, v in c.items() if k not in ("id", "text")} for c in chunks],
    )

    return out_path


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python pdf_to_json.py <path_to_pdf>")
        sys.exit(1)
    result = ingest(sys.argv[1])
    print(f"Saved: {result}")
