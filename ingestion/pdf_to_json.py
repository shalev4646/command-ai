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

1. קבע לאיזה קהל/קהלים יעד הפקודה רלוונטית — בחר אחת או יותר:
   - soldier: חיילים בשירות חובה/סדיר
   - commander: אנשי ונשות קבע (לרבות מפקדים)
   - reserve: חיילי מילואים
   אם הפקודה כללית ורלוונטית לכל אוכלוסיית צה"ל (למשל נהלי בטיחות, בריאות, כשרות, נהלים מנהליים כלליים) — סמן את שלוש האפשרויות. אם היא ספציפית לקהל מסוים (למשל הכותרת או התוכן מזכירים במפורש "מילואים" או "קבע") — סמן רק את מה שרלוונטי בפועל.

2. לכל קהל שסימנת, הצע 3–4 שאלות שאותו קהל באמת ישאל על הפקודה הזו. כללים מחייבים:
   - עברית תקנית וטבעית בלבד. קרא כל שאלה שוב לפני שמירה — ניסוח שבור, מילה שאינה קיימת או שגיאת דקדוק פוסלים אותה.
   - שאלות קצרות ומעשיות בגוף ראשון — מה שאדם אמיתי מקליד בצ'אט, לא ניסוח בירוקרטי.
   - כל שאלה חייבת לגעת בכאב או באינטרס מוחשי של השואל: כסף, חופשה, שינה, בריאות, פחד מעונש, פרטיות, הגנה מפני שרירות. אסורות שאלות טריוויה מנהלתיות ("מה מטרת הפקודה", "איזה סעיף חל על...", תקני שטח והגדרות ארגוניות).
   - אסור ז'רגון פנימי שחייל מן השורה לא מכיר (שמות מסלולים פנימיים, קיצורים נדירים) — גם אם מופיע בפקודה, נסח סביב העניין במילים מוכרות.
   - לחיילים (soldier): מה מגיע לי / מותר לי / מה קורה אם... למשל "כמה ימי חופשה מגיעים לי?" ולא "מהם עקרונות מדיניות החופשות".
   - למפקדים (commander — במערכת זו אנשי ונשות קבע): שאלות סמכות ("מה מותר לי לאשר או להטיל, מתי אני חייב לדווח, איפה הגבול") או זכויות קבע אישיות בגוף ראשון.
   - למילואים (reserve): זכויות, תגמולים, דחיות שירות והחזרים הייחודיים למילואים.
   - חשוב מכל: הצע רק שאלות שהתשובה המלאה עליהן מופיעה מפורשות בטקסט שלפניך. אל תציע שאלה על נושא שהטקסט רק מזכיר בכותרת או מפנה לגביו לפקודה אחרת.

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
    "description": "Save the target-audience roles and per-audience suggested questions for this document.",
    "input_schema": {
        "type": "object",
        "properties": {
            "roles": {
                "type": "array",
                "items": {"type": "string", "enum": list(ROLES)},
                "description": "Audiences this document applies to. Include all three for a general-purpose order.",
            },
            "questions_by_role": {
                "type": "object",
                "description": "3-4 practical questions per audience, only for audiences listed in roles.",
                "properties": {
                    "soldier": {"type": "array", "items": {"type": "string"}},
                    "commander": {"type": "array", "items": {"type": "string"}},
                    "reserve": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
        "required": ["roles", "questions_by_role"],
    },
}


def _get_tool_input(response) -> dict:
    for block in response.content:
        if block.type == "tool_use":
            return block.input
    return {}


# A full Hebrew order page carries ~1,500-2,500 chars of text. Well below
# that, the PDF is a scan with a partial text layer — ingesting it silently
# produces a document that *looks* loaded but can't answer questions about
# its missing sections (33.0111 shipped that way: the reporting-duty clauses
# existed only in the page images). Fail loudly; recover the text manually
# (order page on the IDF site / page-image reading) before ingesting.
MIN_CHARS_PER_PAGE = 900


def extract_text(pdf_path: Path) -> str:
    """Extract text using PyMuPDF (handles Hebrew RTL correctly)."""
    doc = fitz.open(str(pdf_path))
    pages = [page.get_text() for page in doc]
    n_pages = len(doc)
    doc.close()
    text = "\n\n".join(p for p in pages if p.strip())
    if n_pages and len(text) // n_pages < MIN_CHARS_PER_PAGE:
        raise ValueError(
            f"חילוץ טקסט דליל ({len(text)} תווים ב-{n_pages} עמודים — "
            f"כנראה PDF סרוק עם שכבת טקסט חלקית); יש לשחזר את הטקסט ידנית"
        )
    return text


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


def _split_stringified_list(s: str) -> list[str]:
    """Recover a list the model serialized as one string, tolerating the
    unescaped Hebrew-acronym quotes that make it invalid JSON."""
    try:
        parsed = json.loads(s)
        if isinstance(parsed, list):
            return [q for q in parsed if isinstance(q, str)]
    except Exception:
        pass
    parts = re.split(r'"\s*,\s*"', s.strip().strip("[]").strip().strip('"'))
    return [p.strip().strip('"') for p in parts if p.strip()]


def analyze_document(text: str) -> dict:
    """Ask the model which roles (soldier/commander/reserve) this document is
    relevant to, and for role-tailored practical questions, in a single call.

    Returns {"roles": [...], "questions": {role: [q, ...]}} — questions keyed
    per audience, so a soldier sees rights-style questions ("כמה מגיע לי?")
    while a commander sees authority-style ones for the same order.

    Sonnet on a 12K-char slice, not Haiku on 3K: these questions are the
    home-screen face of the app, and the Haiku bank shipped broken Hebrew and
    internal jargon lifted from garbled PDF text (pilot feedback 2026-07-09).
    Runs once per newly ingested order (~4¢), so runtime cost is unaffected."""
    response = client.messages.create(
        model="claude-sonnet-5",
        max_tokens=1500,
        tools=[_ANALYSIS_TOOL],
        tool_choice={"type": "tool", "name": "save_analysis"},
        messages=[{"role": "user", "content": ANALYSIS_PROMPT + text[:12000]}],
    )
    result = _get_tool_input(response)
    roles = [r for r in result.get("roles", []) if r in _VALID_ROLES] or list(_VALID_ROLES)
    by_role = result.get("questions_by_role") or {}
    # the whole object occasionally arrives serialized as one string
    if isinstance(by_role, str):
        try:
            by_role = json.loads(by_role)
        except Exception:
            by_role = {}
    if not isinstance(by_role, dict):
        by_role = {}
    questions: dict[str, list[str]] = {}
    for role in roles:
        qs = by_role.get(role) or []
        # the model occasionally returns an array serialized as one string —
        # iterating it then char-splits into garbage ("[", "\"", "א", ...).
        # json.loads alone isn't enough: Hebrew acronyms (ולת"ם, צה"ל) embed
        # unescaped quotes that break it, so _split_stringified_list falls
        # back to the quote-comma-quote element boundary.
        if isinstance(qs, str):
            qs = _split_stringified_list(qs)
        qs = [q.strip() for q in qs if isinstance(q, str) and len(q.strip()) >= 12]
        if qs:
            questions[role] = qs[:4]
    return {"questions": questions, "roles": roles}


def _existing_doc_for(out_dir: Path, source_file: str) -> dict | None:
    """The stored JSON previously written for this source PDF, if any."""
    for f in out_dir.glob("*.json"):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if d.get("source_file") == source_file:
            return d
    return None


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

    # Hand-maintained fields survive a re-ingest: key-facts sections and annex
    # tables are verified manually (raw extraction mangles them), and a curated
    # question bank (questions_curated) replaced the auto-generated one —
    # regenerating any of these would silently undo that work. To force a full
    # rebuild for one order, delete its JSON before ingesting.
    prev = _existing_doc_for(out_dir, pdf.name)
    if prev:
        for field in ("sections", "annex_exceptions", "anchor_questions"):
            if prev.get(field):
                meta[field] = prev[field]

    analysis = analyze_document(text)
    if prev and prev.get("questions_curated"):
        meta["suggested_questions"] = prev.get("suggested_questions") or {}
        meta["questions_curated"] = True
    else:
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
