import json
import re
import sys
from pathlib import Path

import fitz  # PyMuPDF
from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

client = Anthropic()

META_PROMPT = """קרא את תחילת פקודת מטכ"ל הבאה וחלץ את המטא-דאטה שלה (מספר פקודה, כותרת, תאריך פרסום אם קיים).
שים לב: כותרות רבות מכילות גרשיים כחלק מקיצור (כמו צה"ל, מטכ"ל) — זה תקין, אל תשמיט אותם.

טקסט:
"""

QUESTIONS_PROMPT = """קרא את פקודת מטכ"ל הבאה והצע 5 שאלות נפוצות שחייל או מפקד עשוי לשאול על התוכן הספציפי של הפקודה הזו (בעברית, קצרות וממוקדות, לא כלליות).

טקסט:
"""

_METADATA_TOOL = {
    "name": "save_metadata",
    "description": "Save the extracted document_id, title, and publish date for this order.",
    "input_schema": {
        "type": "object",
        "properties": {
            "document_id": {"type": "string", "description": "e.g. PM-33.0213"},
            "title": {"type": "string"},
            "published": {"type": ["string", "null"], "description": "YYYY-MM-DD or null"},
        },
        "required": ["document_id", "title"],
    },
}

_QUESTIONS_TOOL = {
    "name": "save_questions",
    "description": "Save the suggested questions about this document.",
    "input_schema": {
        "type": "object",
        "properties": {
            "questions": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["questions"],
    },
}


def _get_tool_input(response) -> dict:
    for block in response.content:
        if block.type == "tool_use":
            return block.input
    return {}

CHUNK_SIZE = 1500  # תווים לכל chunk


def extract_text(pdf_path: Path) -> str:
    """Extract text using PyMuPDF (handles Hebrew RTL correctly)."""
    doc = fitz.open(str(pdf_path))
    pages = [page.get_text() for page in doc]
    doc.close()
    return "\n\n".join(p for p in pages if p.strip())


def extract_metadata(text: str) -> dict:
    """Extract just title/id from the first 2000 chars.

    Uses tool-use (structured output) rather than asking the model to hand-write
    JSON: Hebrew titles routinely contain literal embedded quotes (e.g. צה"ל),
    which broke plain-text JSON parsing. Tool inputs are parsed by the API
    itself, so embedded quotes are never a problem.
    """
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=256,
        tools=[_METADATA_TOOL],
        tool_choice={"type": "tool", "name": "save_metadata"},
        messages=[{"role": "user", "content": META_PROMPT + text[:2000]}],
    )
    result = _get_tool_input(response)
    return {
        "document_id": result.get("document_id") or "UNKNOWN",
        "title": result.get("title") or "פקודה לא מזוהה",
        "published": result.get("published"),
    }


def generate_suggested_questions(text: str) -> list[str]:
    """Ask the model for a handful of questions specific to this document's content."""
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=400,
        tools=[_QUESTIONS_TOOL],
        tool_choice={"type": "tool", "name": "save_questions"},
        messages=[{"role": "user", "content": QUESTIONS_PROMPT + text[:3000]}],
    )
    questions = _get_tool_input(response).get("questions", [])
    return [q for q in questions if isinstance(q, str) and q.strip()][:6]


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
    meta["source_file"] = pdf.name
    meta["suggested_questions"] = generate_suggested_questions(text)
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
