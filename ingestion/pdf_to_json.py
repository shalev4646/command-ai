import json
import re
import sys
from pathlib import Path

import fitz  # PyMuPDF
from anthropic import Anthropic
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent))
from common import ROLES, safe_print

load_dotenv(Path(__file__).parent.parent / ".env")

client = Anthropic()

META_PROMPT = """קרא את תחילת פקודת מטכ"ל הבאה וחלץ את המטא-דאטה שלה (מספר פקודה, כותרת, תאריך פרסום אם קיים).
שים לב: כותרות רבות מכילות גרשיים כחלק מקיצור (כמו צה"ל, מטכ"ל) — זה תקין, אל תשמיט אותם.

טקסט:
"""

ANALYSIS_PROMPT = """קרא את פקודת מטכ"ל הבאה ובצע שני דברים:

1. הצע 5 שאלות נפוצות שמישהו מקהל היעד הרלוונטי עשוי לשאול על התוכן הספציפי של הפקודה הזו (בעברית, קצרות וממוקדות, לא כלליות).

2. קבע לאיזה קהל/קהלים יעד הפקודה רלוונטית — בחר אחת או יותר:
   - soldier: חיילים בשירות חובה/סדיר
   - commander: אנשי ונשות קבע (לרבות מפקדים)
   - reserve: חיילי מילואים
   אם הפקודה כללית ורלוונטית לכל אוכלוסיית צה"ל (למשל נהלי בטיחות, בריאות, כשרות, נהלים מנהליים כלליים) — סמן את שלוש האפשרויות. אם היא ספציפית לקהל מסוים (למשל הכותרת או התוכן מזכירים במפורש "מילואים" או "קבע") — סמן רק את מה שרלוונטי בפועל.

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

_ANALYSIS_TOOL = {
    "name": "save_analysis",
    "description": "Save the suggested questions and target-audience roles for this document.",
    "input_schema": {
        "type": "object",
        "properties": {
            "questions": {"type": "array", "items": {"type": "string"}},
            "roles": {
                "type": "array",
                "items": {"type": "string", "enum": list(ROLES)},
                "description": "Audiences this document applies to. Include all three for a general-purpose order.",
            },
        },
        "required": ["questions", "roles"],
    },
}


def _get_tool_input(response) -> dict:
    for block in response.content:
        if block.type == "tool_use":
            return block.input
    return {}


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


_VALID_ROLES = set(ROLES)


def analyze_document(text: str) -> dict:
    """Ask the model for suggested questions and which roles (soldier/commander/
    reserve) this document is relevant to, in a single call."""
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=500,
        tools=[_ANALYSIS_TOOL],
        tool_choice={"type": "tool", "name": "save_analysis"},
        messages=[{"role": "user", "content": ANALYSIS_PROMPT + text[:3000]}],
    )
    result = _get_tool_input(response)
    questions = result.get("questions", [])
    roles = result.get("roles", [])
    return {
        "questions": [q for q in questions if isinstance(q, str) and q.strip()][:6],
        "roles": [r for r in roles if r in _VALID_ROLES] or list(_VALID_ROLES),
    }


def ingest(pdf_path: str) -> Path:
    pdf = Path(pdf_path)
    text = extract_text(pdf)
    if not text.strip():
        raise ValueError(f"לא נמצא טקסט ב-{pdf.name}")

    meta = extract_metadata(text)
    title = meta.get("title", pdf.stem)

    # save minimal JSON for sidebar display
    out_dir = Path(__file__).parent.parent / "storage" / "json_store"
    out_dir.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^\w֐-׿-]", "-", title)[:60]
    meta["raw_text"] = text
    meta["source_file"] = pdf.name
    analysis = analyze_document(text)
    meta["suggested_questions"] = analysis["questions"]
    # The LLM's original classification is stored as-is; manual overrides from
    # metadata_override.json are applied at read time (backend.load_documents),
    # so removing an override entry later reverts the document cleanly.
    meta["roles"] = analysis["roles"]
    out_path = out_dir / f"{slug}.json"
    out_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    # reuse the same chunking/indexing logic used for bulk (re)indexing, so
    # there's one place that defines how documents get chunked
    from storage.vector_store import index_document
    index_document(meta)

    return out_path


def ingest_folder(pdf_dir: str | Path, skip: set[str] | None = None) -> list[str]:
    """Ingest every PDF in a folder, fault-tolerantly: a failure on one file
    (API error, corrupt PDF, empty text) is logged and skipped so the rest of
    the batch still gets processed. `skip` is a set of PDF filenames to leave
    alone (already-ingested files). Returns names of successfully ingested PDFs."""
    pdf_dir = Path(pdf_dir)
    done: list[str] = []
    failed: list[str] = []
    pdfs = [p for p in sorted(pdf_dir.glob("*.pdf")) if not skip or p.name not in skip]
    for i, pdf in enumerate(pdfs, 1):
        safe_print(f"[{i}/{len(pdfs)}] מעבד: {pdf.name}")
        try:
            ingest(str(pdf))
            done.append(pdf.name)
        except Exception as e:
            failed.append(pdf.name)
            safe_print(f"  שגיאה בעיבוד {pdf.name} — מדלג וממשיך: {type(e).__name__}: {e}")
    if pdfs:
        safe_print(f"הסתיים: {len(done)} הצליחו, {len(failed)} נכשלו" + (f" ({', '.join(failed)})" if failed else ""))
    return done


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    if len(sys.argv) < 2:
        print("Usage: python pdf_to_json.py <path_to_pdf_or_folder>")
        sys.exit(1)
    target = Path(sys.argv[1])
    if target.is_dir():
        ingest_folder(target)
    else:
        result = ingest(sys.argv[1])
        print(f"Saved: {result}")
