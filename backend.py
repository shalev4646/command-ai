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

# Follow-up query rewriting runs on Haiku: it fires before every retrieval
# in an ongoing conversation, so it must be fast and cheap (~0.2s, well
# under a tenth of a cent), and turning chat context into a standalone
# search query is well within its reach.
REWRITE_MODEL = "claude-haiku-4-5-20251001"

# Hard cap on how many retrieved chunks are stitched into the prompt. Kept
# deliberately small: the top few clauses carry the answer, and every extra
# chunk inflates prompt tokens (cost + latency) and erodes the per-request
# rate-limit budget when many soldiers query at once. 6 leaves room for the
# leading order's guaranteed depth (top_doc_depth=3) plus 3 other orders.
MAX_CONTEXT_CHUNKS = 6

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


_REWRITE_PROMPT = """לפניך קטע משיחה בין משתמש לעוזר לפקודות מטכ"ל, ואחריו שאלת ההמשך האחרונה של המשתמש.
שכתב את שאלת ההמשך לשאלה עצמאית ומלאה, שאפשר לחפש איתה בפקודות בלי לראות את השיחה.

כללים:
1. אם השאלה האחרונה כבר עומדת בפני עצמה — החזר אותה כלשונה, ללא שינוי.
2. השלם מהשיחה רק את מה שחסר (הנושא, האוכלוסייה, הפקודה שמדובר בה) — אל תמציא פרטים.
3. שמור על שאלה קצרה וטבעית, כפי שמשתמש היה מנסח אותה.

השיחה עד כה:
{convo}

שאלת ההמשך: {question}"""

_REWRITE_TOOL = {
    "name": "save_search_query",
    "description": "Save the standalone, self-contained version of the user's follow-up question.",
    "input_schema": {
        "type": "object",
        "properties": {
            "standalone_question": {"type": "string", "description": "השאלה המשוכתבת, עצמאית ומובנת ללא הקשר השיחה"},
        },
        "required": ["standalone_question"],
    },
}


def _standalone_question(question: str, history: list[dict] | None) -> str:
    """Make a follow-up question searchable on its own.

    Retrieval sees only one query string, so "ומה לגבי מילואים?" after a
    sleep-hours exchange finds nothing. Given conversation history, Haiku
    folds the missing context back in ("כמה שעות שינה מגיעות לחייל
    מילואים?"); self-contained questions are returned as-is. Used for
    RETRIEVAL ONLY — the answering model still gets the original question
    with the full history. Any failure falls back to the raw question.
    """
    if not history:
        return question
    lines = []
    for m in history[-6:]:  # last 3 exchanges carry the referent
        label = "משתמש" if m.get("role") == "user" else "עוזר"
        content = str(m.get("content", ""))
        if len(content) > 400:
            content = content[:400] + "…"
        lines.append(f"{label}: {content}")
    try:
        response = client.messages.create(
            model=REWRITE_MODEL,
            max_tokens=200,
            tools=[_REWRITE_TOOL],
            tool_choice={"type": "tool", "name": "save_search_query"},
            messages=[{
                "role": "user",
                "content": _REWRITE_PROMPT.format(convo="\n".join(lines), question=question),
            }],
        )
        for block in response.content:
            if block.type == "tool_use":
                rewritten = str(block.input.get("standalone_question", "")).strip()
                if rewritten:
                    return rewritten
    except Exception:
        pass  # retrieval on the raw question is degraded, not broken
    return question


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
    # follow-ups ("ומה לגבי מילואים?") are unsearchable on their own —
    # retrieve with a standalone rewrite, but answer the original question
    search_query = _standalone_question(question, history)
    chunks = retrieve_for_role(search_query, role)
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


# The UI-facing fallback questions live in app.py (_FALLBACK_QUESTIONS) —
# deliberately NOT exported from here: Streamlit Cloud can re-execute app.py
# against a backend module cached from a previous build, and importing a
# newly-added backend name from app.py crashes the boot with ImportError.


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
