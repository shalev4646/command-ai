import json
import os
import shutil
from pathlib import Path

from anthropic import Anthropic
from dotenv import load_dotenv

from common import ROLES
from metadata_overrides import apply_overrides
from storage.vector_store import retrieve

load_dotenv(Path(__file__).parent / ".env")

client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY", "").strip())

MODEL = "claude-opus-4-8"
# Ceiling for thinking + answer combined. Adaptive thinking spends a few
# thousand tokens on table/legal reasoning before the ~1K-token structured
# answer; streaming means the large cap carries no HTTP-timeout risk.
MAX_OUTPUT_TOKENS = 8000

# Hard cap on how many retrieved chunks are stitched into the prompt. Kept
# deliberately small: the top few clauses carry the answer, and every extra
# chunk inflates prompt tokens (cost + latency) and erodes the per-request
# rate-limit budget when many soldiers query at once.
MAX_CONTEXT_CHUNKS = 5

_COMMON_RULES = """חוקים מוחלטים:
1. ענה אך ורק על בסיס הקטעים שסופקו לך בהקשר.
2. אם המידע לא קיים בקטעים — אמור בדיוק: "המידע לא קיים בפקודות שסופקו."
3. אל תשתמש בידע כללי על הצבא.
4. כל תשובה חייבת לכלול ציטוט מדויק + מספר סעיף + שם הפקודה."""

SYSTEM_PROMPT_SOLDIER = f"""אתה עוזר צבאי המסייע לחיילים להבין את זכויותיהם האישיות לפי פקודות מטכ"ל.
אתה פונה אל החייל בגוף שני, ומתמקד במה שמותר/אסור/מגיע לו כפרט — לא בשיקולי פיקוד.

{_COMMON_RULES}
5. התמקד בזכויות החייל, בתנאים למימושן, ובמה עומד לרשותו אם הזכות הופרה.

מבנה תשובה לשאלות "האם מגיע לי / מותר לי X?":
**פסיקה:** מותר / אסור / מגיע לי בתנאים
**מקור:** [שם הפקודה] סעיף X
**תנאים:** רשימה מפורטת
**מי מאשר:** דרגה נדרשת

מבנה תשובה לשאלות עובדתיות:
**תשובה:** תשובה ישירה
**מקור:** [שם הפקודה] סעיף X
"""

SYSTEM_PROMPT_COMMANDER = f"""אתה עוזר צבאי המסייע למפקדים להפעיל את סמכויותיהם הפיקודיות לפי פקודות מטכ"ל.
אתה פונה אל המפקד בגוף שני, ומתמקד בסמכויות אישור, בנהלי ענישה ובאחריות פיקודית — לא בזכויות אישיות של הפרט.

{_COMMON_RULES}
5. התמקד בסמכויות המפקד: מה הוא רשאי לאשר או לשלול, אילו עונשים מותר לו להטיל ובאילו תנאים, ומה חובות הדיווח/התיעוד שלו.

מבנה תשובה לשאלות "האם אני רשאי לאשר/לשלול X?":
**פסיקה:** מוסמך / לא מוסמך / מוסמך בתנאים
**מקור:** [שם הפקודה] סעיף X
**דרגה נדרשת לאישור:** (אם שונה מדרגת המפקד השואל)
**תנאים / הגבלות:** רשימה מפורטת

מבנה תשובה לשאלות עובדתיות (נהלים, ענישה, דיווח):
**תשובה:** תשובה ישירה
**מקור:** [שם הפקודה] סעיף X
"""

SYSTEM_PROMPT_RESERVE = f"""אתה עוזר צבאי המסייע לחיילי מילואים להבין את זכויותיהם וזכאויותיהם הייחודיות לפי פקודות מטכ"ל.
אתה פונה אל חייל המילואים בגוף שני, ומתמקד בזכויות, תגמולים ותנאים הספציפיים לשירות מילואים — לא בזכויות של חיילי חובה/סדיר או בשיקולי פיקוד.

{_COMMON_RULES}
5. התמקד בזכויות ובתגמולים הייחודיים למילואים, בתנאים למימושם, ובמה עומד לרשות חייל המילואים אם הזכות הופרה.

מבנה תשובה לשאלות "האם מגיע לי / מותר לי X?":
**פסיקה:** מותר / אסור / מגיע לי בתנאים
**מקור:** [שם הפקודה] סעיף X
**תנאים:** רשימה מפורטת
**מי מאשר:** דרגה נדרשת

מבנה תשובה לשאלות עובדתיות:
**תשובה:** תשובה ישירה
**מקור:** [שם הפקודה] סעיף X
"""

SYSTEM_PROMPTS = {
    "soldier": SYSTEM_PROMPT_SOLDIER,
    "commander": SYSTEM_PROMPT_COMMANDER,
    "reserve": SYSTEM_PROMPT_RESERVE,
}

ALL_ROLES = ROLES


def load_documents() -> list[dict]:
    json_dir = Path(__file__).parent / "storage" / "json_store"
    docs = []
    for f in sorted(json_dir.glob("*.json")):
        try:
            docs.append(apply_overrides(json.loads(f.read_text(encoding="utf-8"))))
        except Exception:
            pass
    return docs


def _docs_for_role(role: str | None) -> list[dict]:
    """Documents applicable to a role. Docs without a `roles` tag (shouldn't
    happen post-ingestion, but defensive for older data) are treated as
    relevant to everyone rather than silently hidden."""
    docs = load_documents()
    if role is None:
        return docs
    return [d for d in docs if role in (d.get("roles") or ALL_ROLES)]


def retrieve_for_role(question: str, role: str) -> list[dict]:
    """Retrieve the chunks relevant to `question`, scoped to `role`'s documents.

    The single retrieval entry point for both production (_build_rag_context)
    and eval.py — so the sanity check always exercises the same pipeline the
    app uses.
    """
    doc_ids = [d["document_id"] for d in _docs_for_role(role) if d.get("document_id")]
    return retrieve(question, n_results=MAX_CONTEXT_CHUNKS, doc_ids=doc_ids)


def _context_from_chunks(chunks: list[dict]) -> str:
    if not chunks:
        return "אין מסמכים טעונים במערכת."
    parts = []
    for c in chunks:
        parts.append(f"[{c['doc_id']} | {c['title']} | סעיף {c['clause']}]\n{c['text']}")
    return "\n\n---\n\n".join(parts)


def _sources_from_chunks(chunks: list[dict]) -> list[dict]:
    """The distinct orders behind an answer, in retrieval-rank order.

    Only orders whose original PDF is actually on disk are returned — the
    UI links each source straight to its PDF, and a dead link is worse
    than no link.
    """
    pdf_dir = Path(__file__).parent / "pdf-ldf_law"
    by_id = {d["document_id"]: d for d in load_documents() if d.get("document_id")}
    sources, seen = [], set()
    for c in chunks:
        doc_id = c["doc_id"]
        if doc_id in seen:
            continue
        seen.add(doc_id)
        doc = by_id.get(doc_id)
        source_file = (doc or {}).get("source_file")
        if source_file and (pdf_dir / source_file).exists():
            sources.append({
                "doc_id": doc_id,
                "title": doc.get("title", c.get("title", "")),
                "source_file": source_file,
            })
    return sources


def stream_ai_answer(question: str, history: list[dict] | None = None, role: str = "soldier"):
    """Answer a question as a live stream.

    Returns (text_generator, sources): the generator yields answer-text deltas
    as the model produces them (UI renders them via st.write_stream), and
    sources — the distinct orders behind the answer, ranked by retrieval
    relevance — are computed up front from the retrieved chunks so the UI has
    them the moment the stream finishes. Adaptive thinking runs before the
    first text token (its deltas are not yielded), so the stream starts after
    a short reasoning pause.
    """
    chunks = retrieve_for_role(question, role)
    context = _context_from_chunks(chunks)
    system_prompt = SYSTEM_PROMPTS.get(role, SYSTEM_PROMPT_SOLDIER)

    messages = list(history or [])
    messages.append({"role": "user", "content": question})

    def _gen():
        with client.messages.stream(
            model=MODEL,
            max_tokens=MAX_OUTPUT_TOKENS,
            thinking={"type": "adaptive"},
            system=system_prompt + f"\n\nקטעים רלוונטיים מהפקודות:\n{context}",
            messages=messages,
        ) as stream:
            yield from stream.text_stream

    return _gen(), _sources_from_chunks(chunks)


def get_ai_answer(question: str, history: list[dict] | None = None, role: str = "soldier") -> dict:
    """Non-streaming variant of stream_ai_answer — same pipeline, whole answer.

    Returns {"text": <answer>, "sources": [{doc_id, title, source_file}...]}.
    Used by eval.py, so the sanity check exercises the exact production path.
    """
    text_gen, sources = stream_ai_answer(question, history, role)
    return {"text": "".join(text_gen), "sources": sources}


def get_ai_response(question: str, history: list[dict] | None = None, role: str = "soldier") -> str:
    return get_ai_answer(question, history, role)["text"]


def get_pdf_bytes(source_file: str) -> bytes | None:
    """Read the original PDF for a loaded document, if it's still on disk."""
    pdf_path = Path(__file__).parent / "pdf-ldf_law" / source_file
    if not pdf_path.exists():
        return None
    return pdf_path.read_bytes()


def get_loaded_docs_info(role: str | None = None) -> list[dict]:
    """Return title + document_id + source PDF filename for documents applicable to `role` (all, if None)."""
    return [
        {
            "title": d.get("title", "?"),
            "id": d.get("document_id", "?"),
            "source_file": d.get("source_file"),
        }
        for d in _docs_for_role(role)
        if d.get("document_id")
    ]


# Shown by the UI only when the real pool is momentarily empty (documents
# still loading during a redeploy); never cached into the session.
DEFAULT_QUESTIONS = {
    "soldier": ["מה זכויותיי כחייל?", "האם מגיע לי שינה מספקת?", "מה העונש על עבירה משמעתית?"],
    "commander": ["אילו עונשים מוסמך מפקד להטיל בדין משמעתי?", "מה חובות הדיווח שלי כמפקד?"],
    "reserve": ["אילו תגמולים מגיעים לי כחייל מילואים?", "מה זכויותיי כחייל מילואים?"],
}


def get_suggested_questions(role: str = "soldier") -> list[str]:
    """Return questions pooled from documents applicable to `role`.

    Each document carries its own LLM-generated `suggested_questions`
    (produced at ingestion time) and `roles` tag, so this automatically
    covers whatever role-relevant documents happen to be loaded — no
    per-document hardcoding needed.
    """
    all_questions: list[str] = []
    for doc in _docs_for_role(role):
        qs = doc.get("suggested_questions")
        # per-role format: {role: [questions]} — show each audience only the
        # questions written for it (a soldier gets "כמה מגיע לי", a commander
        # gets authority-style questions on the same order)
        if isinstance(qs, dict):
            qs = qs.get(role)
        if not isinstance(qs, list):
            continue
        # ingestion once stored a broken char-split list; keep only real questions
        all_questions.extend(q for q in qs if isinstance(q, str) and len(q.strip()) >= 12)
    # may be empty (e.g. documents still loading during a redeploy) — the UI
    # shows generic defaults for that run WITHOUT caching them, so the real
    # pool is retried on the next rerun
    return all_questions


def ensure_pdfs_ingested(pdf_dir: Path | None = None) -> list[str]:
    """Scan pdf_dir for PDFs and ingest any that don't have a JSON yet. Returns newly ingested names."""
    if pdf_dir is None:
        pdf_dir = Path(__file__).parent / "pdf-ldf_law"
    if not pdf_dir.exists():
        return []

    from ingestion.pdf_to_json import ingest_folder

    json_dir = Path(__file__).parent / "storage" / "json_store"
    # Collect source_file values from existing JSONs
    ingested_files: set[str] = set()
    for f in json_dir.glob("*.json"):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            if d.get("source_file"):
                ingested_files.add(d["source_file"])
        except Exception:
            pass

    # the per-file fault tolerance (log, skip, continue) lives in ingest_folder
    return ingest_folder(pdf_dir, skip=ingested_files)


def sync_static_pdfs() -> int:
    """Mirror the source PDFs into ./static for Streamlit's static serving.

    enableStaticServing only exposes files under <app>/static, and the repo
    keeps the PDFs in pdf-ldf_law/ — so they're copied (not committed twice)
    at boot. Returns how many files were copied/refreshed."""
    base = Path(__file__).parent
    static_dir = base / "static"
    static_dir.mkdir(exist_ok=True)
    copied = 0
    for pdf in (base / "pdf-ldf_law").glob("*.pdf"):
        dest = static_dir / pdf.name
        if not dest.exists() or dest.stat().st_size != pdf.stat().st_size:
            shutil.copyfile(pdf, dest)
            copied += 1
    return copied


def warm_index() -> int:
    """Eagerly build the in-memory vector index (embedding-model download +
    chunk embedding). Called at app startup so the one-time cost lands at boot
    instead of inside the first user's question. Returns the chunk count."""
    from storage.vector_store import get_index_stats
    return get_index_stats()["total_chunks"]
