import json
import os
from pathlib import Path

from anthropic import Anthropic
from dotenv import load_dotenv

from common import ROLES, safe_print
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
# rate-limit budget when many soldiers query at once. 8 leaves room for the
# leading order's guaranteed depth (top_doc_depth=4) plus 4 other orders —
# raised from 6 when the key-facts clauses added per order started crowding
# the basic raw-text content out of the leading order's slots.
MAX_CONTEXT_CHUNKS = 8

# Header that marks the retrieved-context section inside a user turn. The
# context rides in the user message (not the system prompt) so the system
# prompt and past turns stay byte-identical across a conversation — the
# stable prefix the API's prompt cache needs.
_CONTEXT_HEADER = "קטעים רלוונטיים מהפקודות:"

# History trimming happens in whole-exchange jumps, not as a rolling cap:
# a window that slides every turn changes the request prefix every turn and
# never hits the prompt cache. Dropping 3 exchanges at a time once 6 have
# accumulated costs one cache miss every 3 turns instead of every turn.
_HISTORY_MAX = 12   # messages (6 exchanges) before a trim
_HISTORY_DROP = 6   # messages (3 exchanges) dropped per trim

_COMMON_RULES = """חוקים מוחלטים:
1. ענה אך ורק על בסיס הקטעים שסופקו לך בהקשר.
2. אם אין בקטעים כלל שחל ישירות על המצב שנשאל — פתח באמירה המדויקת: "המידע לא קיים בפקודות שסופקו." מותר להוסיף אחריה מה כן קיים בקטעים (כלל שחל רק על הקשר אחר או צר יותר), תוך ציון מפורש שההקשר שונה.
   אם יש כלל שחל ישירות על המצב אך אינו נוקב בערך המדויק שנשאל (שעה, סכום, מספר ימים) — אל תסתפק בסירוב: הצג את הכלל כלשונו, הסבר מה נובע ממנו לשאלה, וציין במפורש מה הפקודות לא קובעות.
3. אל תשתמש בידע כללי על הצבא.
4. כל תשובה חייבת לכלול ציטוט מדויק + מספר סעיף + שם הפקודה.
5. לכל שאלה שיש לה שורה תחתונה נורמטיבית — פתח את התשובה בשורת **פסיקה:** שמתחילה במונח הפסיקה עצמו (מותר / אסור / זכאי / פטור / חייב / מוסמך / רשאי), גם אם השאלה לא נוסחה כ"האם מותר לי". מונח הפסיקה יכול לשאת סייג קצר (למשל "**פסיקה:** אסור בתנועה רגלית" או "**פסיקה:** מותר בתנאים") — אל תפתח ב"כן"/"לא" ואל תשתמש ב"**תשובה:**" אלא בשאלות עובדתיות (מה הנוהל, מה עושים, איך, כמה, מתי — גם כשהתשובה נוגעת בסמכויות). כשמונח הפסיקה הוא סמכות (מוסמך / רשאי) — לעולם אל תשאיר אותו חשוף: צרף אליו את מושא הסמכות ("**פסיקה:** מוסמך להטיל עד 7 ימי ריתוק")."""

SYSTEM_PROMPT_SOLDIER = f"""אתה עוזר צבאי המסייע לחיילים להבין את זכויותיהם האישיות לפי פקודות מטכ"ל.
אתה פונה אל החייל בגוף שני, ומתמקד במה שמותר/אסור/מגיע לו כפרט — לא בשיקולי פיקוד.

{_COMMON_RULES}
6. התמקד בזכויות החייל, בתנאים למימושן, ובמה עומד לרשותו אם הזכות הופרה.

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
6. התמקד בסמכויות המפקד: מה הוא רשאי לאשר או לשלול, אילו עונשים מותר לו להטיל ובאילו תנאים, ומה חובות הדיווח/התיעוד שלו. כשמפקד שואל על זכות אישית שלו (לא על סמכות) — ענה לגופה באותם כללים.

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
6. התמקד בזכויות ובתגמולים הייחודיים למילואים, בתנאים למימושם, ובמה עומד לרשות חייל המילואים אם הזכות הופרה.

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
        except Exception as e:
            # a corrupt JSON silently drops one order from the whole retrieval
            # corpus; log which file so it doesn't vanish without a trace
            safe_print(f"[backend] skipping unreadable doc {f.name}: {e!r}")
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
3. אל תשמיט את העילה או ההקשר שסביבם נסובה השיחה (למשל: סיבת הבקשה — טעמי דת,
   מצב רפואי, מצב כלכלי) — הם לרוב מילות המפתח שהחיפוש נשען עליהן.
4. תקן שגיאות כתיב והקלדה אם יש.
5. שמור על שאלה קצרה וטבעית, כפי שמשתמש היה מנסח אותה.

השיחה עד כה:
{convo}

שאלת ההמשך: {question}"""

# First questions get a narrower treatment: typo repair ONLY. Soldiers type
# fast ("חפשש", "להתשחרר", "טלווזיה" — all real pilot questions) and both the
# embedding and the lexical variants miss on mangled words, so the bot refuses
# questions it can answer. Slang is deliberately protected — it's signal the
# DIRTY eval set covers, not noise.
_NORMALIZE_PROMPT = """לפניך שאלה שמשתמש הקליד לחיפוש בפקודות מטכ"ל.

כללים:
1. תקן אך ורק שגיאות כתיב והקלדה (למשל "חפשש"→"חופשה", "להתשחרר"→"להשתחרר").
2. אל תשנה ניסוח, סדר מילים או סלנג ("סדירניק", "לחטוף", "סופש" אינם שגיאה),
   ואל תוסיף או תשמיט מידע.
3. אין שגיאות — החזר את השאלה בדיוק כלשונה.

השאלה: {question}"""

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
    """Make a question searchable on its own. RETRIEVAL ONLY — the answering
    model always gets the original question (with the full history).

    Two modes, one Haiku call either way (~0.3s, well under a tenth of a cent):
    - With history: "ומה לגבי מילואים?" after a sleep-hours exchange finds
      nothing, so Haiku folds the missing referent back in. Since 2026-07-20
      it must also keep the conversation's עילה (religious/medical/economic
      reason) — dropping it reproduced a live pilot refusal.
    - Without history (first question): typo repair only. "כמה ימי חפשש מגיע
      לי אם אני אמור להתשחרר" retrieved nothing until normalized; slang stays
      untouched, and clean questions must come back verbatim (eval NOCHANGE).
    Any failure falls back to the raw question — degraded, never broken.
    """
    if not history:
        # vocabulary gate: only pay the Haiku round-trip (~1.5s) when the
        # question carries a word the corpus doesn't know in any match-form —
        # the typo signature. Clean questions (the common case) skip straight
        # to retrieval with zero added latency. Gate failure falls through to
        # normalization: slower, never broken.
        try:
            from storage.vector_store import has_unknown_terms
            if not has_unknown_terms(question):
                return question
        except Exception:
            pass
        prompt = _NORMALIZE_PROMPT.format(question=question)
    else:
        lines = []
        for m in history[-6:]:  # last 3 exchanges carry the referent
            label = "משתמש" if m.get("role") == "user" else "עוזר"
            # history user turns carry their retrieval context (see
            # stream_ai_answer) — the rewrite only needs the question itself
            content = str(m.get("content", "")).split(f"\n\n{_CONTEXT_HEADER}")[0]
            if len(content) > 400:
                content = content[:400] + "…"
            lines.append(f"{label}: {content}")
        prompt = _REWRITE_PROMPT.format(convo="\n".join(lines), question=question)
    try:
        # bound this non-critical pre-retrieval call: without an explicit timeout
        # the SDK default is 10 min × 3 retries, so a flaky network leaves the
        # user staring at the spinner for minutes before the raw-question
        # fallback below kicks in. 8s × 2 attempts caps the wait instead.
        # temperature=0: a rewriting utility must be repeatable — the eval
        # gates (TYPOS, NOCHANGE, FOLLOWUP) assert on its exact output.
        response = client.with_options(timeout=8.0, max_retries=1).messages.create(
            model=REWRITE_MODEL,
            max_tokens=200,
            temperature=0,
            tools=[_REWRITE_TOOL],
            tool_choice={"type": "tool", "name": "save_search_query"},
            messages=[{"role": "user", "content": prompt}],
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


def clause_key(section: str | None, clause: str | None) -> str | None:
    """The key under which storage/clause_pages.json stores a chunk's page.

    Bare clause strings collide across chunk kinds within one order: raw-text
    window positions, key-facts clause numbers and annex row numbers all use
    small integers (013.3 carries all three). So raw windows are keyed
    "w<first-window>" (a stitched "2–4" range starts at window 2, which is
    where the cited passage begins) and structured clauses are keyed
    "<section>:<clause>". _build_clause_pages.py emits exactly these keys —
    it must never drift from this function.
    """
    if not clause:
        return None
    if (section or "").startswith("chunk"):
        return "w" + str(clause).split("–")[0]
    return f"{section}:{clause}"


def _sources_from_chunks(chunks: list[dict]) -> list[dict]:
    """The distinct orders behind an answer, in retrieval-rank order.

    Only orders whose original PDF is actually on disk are returned — the
    UI links each source straight to its PDF, and a dead link is worse
    than no link. "clause" is the clause_key of the order's highest-ranked
    chunk WITH a known page, so the UI can deep-link the PDF to the cited
    passage via page_for_clause: key-facts chunks are hand-written summaries
    with no PDF location, and when one outranks the raw windows (they were
    added precisely to win those rankings) the next-ranked window is still
    the passage the answer drew on. When nothing resolves, the top chunk's
    key is recorded anyway — the page lookup returns None, the link stays
    page-less, and the metrics log still says which clause led.
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
            clause = clause_key(c.get("section"), c.get("clause"))
            # `highlight` is the text of the chunk we deep-link to, so the UI
            # can mark that exact passage on the rendered page. It rides the
            # SAME chunk that resolved to a page (a raw-text window whose
            # text is extracted from the PDF, so page.search_for can find
            # it) — not a key-facts summary, which has no PDF text to match.
            highlight = c.get("text", "")
            for cc in chunks:
                if cc["doc_id"] != doc_id:
                    continue
                key = clause_key(cc.get("section"), cc.get("clause"))
                if page_for_clause(doc_id, key) is not None:
                    clause = key
                    highlight = cc.get("text", "")
                    break
            sources.append({
                # civil-law sources (חוק, not a פ"מ) must not be labelled as an
                # order in the UI — the source dialog drops the "פ״מ" prefix for
                # these and shows "מקור אזרחי" instead.
                "civil_source": bool((doc or {}).get("civil_source")),
                "doc_id": doc_id,
                "title": doc.get("title", c.get("title", "")),
                "source_file": source_file,
                "clause": clause,
                "highlight": highlight[:160],
            })
    return sources


def render_clause_image(source_file: str, page: int, highlight: str = "") -> bytes | None:
    """A PNG of the cited clause's PDF page, with the passage highlighted.

    Shown INSIDE the app (a dialog), so a soldier sees the exact clause
    marked without leaving for a lost PDF tab and without any reliance on
    the viewer honouring #page (iOS Safari does not). `page` is 1-based;
    `highlight` is the cited chunk's text — matched on the page with
    fitz.search_for and marked, then the image is cropped to a readable band
    around the marks. Never raises: any failure returns None and the caller
    falls back to the full-PDF link.
    """
    if not source_file or not page:
        return None
    try:
        import fitz  # already a dependency (ingestion/pdf_to_json.py)

        pdf_path = Path(__file__).parent / "pdf-ldf_law" / source_file
        if not pdf_path.exists():
            return None
        doc = fitz.open(str(pdf_path))
        try:
            idx = page - 1
            if idx < 0 or idx >= doc.page_count:
                return None
            pg = doc[idx]
            # Locate the passage by short content phrases, not one long
            # string: these orders are laid out in tables and their text
            # layer is often broken (RTL-scrambled digits, injected spaces,
            # boilerplate duplicated many times), so a long exact match never
            # lands. Instead, search 3-word phrases and find the horizontal
            # BAND where the most DISTINCT phrases co-locate — that band is
            # the cited passage. Weighting by distinct phrases (not raw hit
            # count) beats the running-header trap: a title duplicated 20×
            # in the text layer is ONE phrase, while the real passage draws
            # hits from many different phrases.
            import re as _re
            from collections import defaultdict

            words = _re.findall(r'[֐-׿0-9"׳״\':]+', highlight or "")
            BAND = 42
            band_phrases: dict = defaultdict(set)   # band index -> {phrase idx}
            band_rects: dict = defaultdict(list)
            pidx = 0
            for i in range(0, max(1, len(words) - 2), 3):
                phrase = " ".join(words[i:i + 3])
                if len(phrase) >= 9:
                    for r in pg.search_for(phrase):
                        b = int(r.y0 // BAND)
                        band_phrases[b].add(pidx)
                        band_rects[b].append(r)
                    pidx += 1

            rects: list = []
            if band_phrases:
                # anchor = band with the most distinct phrases (ties: more
                # rects), then keep the marks in a window around it
                anchor = max(band_phrases, key=lambda b: (len(band_phrases[b]), len(band_rects[b])))
                # a real passage draws >=2 distinct phrases; a lone match is
                # too weak to trust as a location — show the whole page then
                if len(band_phrases[anchor]) >= 2:
                    lo, hi = (anchor - 3) * BAND, (anchor + 4) * BAND
                    for b, rs in band_rects.items():
                        if lo <= b * BAND <= hi:
                            rects.extend(rs)

            for r in rects:
                pg.add_highlight_annot(r)
            zoom = 2.2
            mat = fitz.Matrix(zoom, zoom)
            ph = pg.rect.height
            if rects:
                top, bot = min(r.y0 for r in rects), max(r.y1 for r in rects)
                clip = fitz.Rect(
                    0, max(0, top - 95),
                    pg.rect.width, min(ph, bot + 150),
                )
                return pg.get_pixmap(matrix=mat, clip=clip).tobytes("png")
            # no confident location — the correct full page, viewer can zoom
            return pg.get_pixmap(matrix=mat).tobytes("png")
        finally:
            doc.close()
    except Exception:
        return None


# {doc_id: {clause_key: 1-based page}}, precomputed by _build_clause_pages.py
# (which needs PyMuPDF and must be rerun after reingesting). Loaded once per
# process — the file is a build artifact, not runtime state.
_CLAUSE_PAGES_PATH = Path(__file__).parent / "storage" / "clause_pages.json"
_clause_pages: dict | None = None


def _get_clause_pages() -> dict:
    global _clause_pages
    if _clause_pages is None:
        try:
            _clause_pages = json.loads(_CLAUSE_PAGES_PATH.read_text(encoding="utf-8"))
        except Exception:
            # missing/corrupt file degrades to page-less PDF links, never errors
            _clause_pages = {}
    return _clause_pages


def page_for_clause(doc_id: str | None, clause: str | None) -> int | None:
    """1-based PDF page where a source's cited clause starts, or None.

    `clause` is a clause_key (see _sources_from_chunks). None means "no page
    known" — callers must fall back to the plain PDF link.
    """
    if not doc_id or not clause:
        return None
    try:
        page = _get_clause_pages().get(str(doc_id), {}).get(str(clause))
    except AttributeError:
        # a hand-edited JSON with the wrong shape must not break rendering
        return None
    return page if isinstance(page, int) and page > 0 else None


def _compose_user_content(question: str, context: str, profile: list[str] | None) -> str:
    """Assemble the user-turn text sent to the API.

    With no profile the result is BYTE-IDENTICAL to the historical
    f"{question}\\n\\n{_CONTEXT_HEADER}\\n{context}" — replayed history turns
    (the prompt-cache prefix) and the eval gates were built against that
    exact shape, so the default path must never drift. A non-empty profile
    adds one parenthetical line between the question and the context header;
    its trailing clause keeps the model from dragging an irrelevant detail
    into every answer. `profile` holds the asker's personal details — status
    pills (חייל בודד...) and, when set, service type/track (שירות סדיר,
    מסלול שירות: ...) — so the label reads "פרטי השואל", not just מעמד.
    """
    if not profile:
        return f"{question}\n\n{_CONTEXT_HEADER}\n{context}"
    return (
        f"{question}\n\n"
        f"(פרטי השואל: {', '.join(profile)}. "
        f"התחשב בהם רק אם הם רלוונטיים לשאלה.)\n\n"
        f"{_CONTEXT_HEADER}\n{context}"
    )


def stream_ai_answer(question: str, history: list[dict] | None = None, role: str = "soldier",
                     profile: list[str] | None = None):
    """Answer a question as a live stream.

    Returns (text_generator, sources, sent_user_content, usage_holder): the
    generator yields answer-text deltas as the model produces them (UI renders
    them via st.write_stream), sources — the distinct orders behind the answer,
    ranked by retrieval relevance — are computed up front from the retrieved
    chunks so the UI has them the moment the stream finishes, and
    sent_user_content is the exact user-turn text sent to the API (question +
    retrieved context). usage_holder is an initially-empty dict the generator
    fills with the answer's token usage once the stream completes — read it
    after consuming the generator (it is per-call, so concurrent sessions never
    share usage). Callers that keep a conversation going must replay
    sent_user_content — not the bare question — as that turn's history
    content, so follow-up requests share a byte-identical prefix and hit the
    prompt cache. `profile` is the asker's personal statuses (חייל בודד,
    נשוי/אה...) — folded into the user turn only when non-empty (see
    _compose_user_content). Adaptive thinking runs before the first text
    token (its deltas are not yielded), so the stream starts after a short
    reasoning pause.
    """
    # follow-ups ("ומה לגבי מילואים?") are unsearchable on their own, and
    # first questions often carry typos that sink retrieval — search with the
    # Haiku rewrite/normalization, but answer the original question
    search_query = _standalone_question(question, history)
    chunks = retrieve_for_role(search_query, role)
    context = _context_from_chunks(chunks)
    system_prompt = SYSTEM_PROMPTS.get(role, SYSTEM_PROMPT_SOLDIER)

    past = [
        {"role": m["role"], "content": m["content"]}
        for m in (history or [])
    ]
    while len(past) > _HISTORY_MAX:
        past = past[_HISTORY_DROP:]

    user_content = _compose_user_content(question, context, profile)

    # Two cache breakpoints (prefix caching, 5-min TTL): the static role
    # prompt, and everything up to the end of history. Turn 1 is below the
    # model's 4096-token cacheable minimum and gains nothing; from turn 2 the
    # context-bearing history pushes the prefix past it, and follow-ups read
    # the cached span at 0.1x input price.
    system_blocks = [{
        "type": "text",
        "text": system_prompt,
        "cache_control": {"type": "ephemeral"},
    }]
    if past:
        past[-1] = {
            "role": past[-1]["role"],
            "content": [{
                "type": "text",
                "text": str(past[-1]["content"]),
                "cache_control": {"type": "ephemeral"},
            }],
        }
    messages = past + [{"role": "user", "content": user_content}]

    # usage rides back in a caller-owned dict, filled when the stream finishes
    # — NOT a module global. Streamlit serves each session on its own thread in
    # one process, so a shared global was a cross-session race: one session
    # could read another's usage/cost/search_query into its metrics row. Each
    # call gets its own holder, so concurrent answers never clobber each other.
    usage_holder: dict = {}

    def _gen():
        with client.messages.stream(
            model=MODEL,
            max_tokens=MAX_OUTPUT_TOKENS,
            thinking={"type": "adaptive"},
            system=system_blocks,
            messages=messages,
        ) as stream:
            yield from stream.text_stream
            final = stream.get_final_message()
            usage = final.usage
            usage_holder.update({
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "cache_creation_input_tokens": getattr(usage, "cache_creation_input_tokens", 0) or 0,
                "cache_read_input_tokens": getattr(usage, "cache_read_input_tokens", 0) or 0,
                # the rewritten retrieval query rides along for the metrics log
                "search_query": search_query if search_query != question else "",
                # the answer hit the shared thinking+answer token cap and was
                # cut mid-sentence; app.py warns the user (mirrors letters.py)
                "truncated": final.stop_reason == "max_tokens",
            })

    return _gen(), _sources_from_chunks(chunks), user_content, usage_holder


def get_ai_answer(question: str, history: list[dict] | None = None, role: str = "soldier",
                  profile: list[str] | None = None) -> dict:
    """Non-streaming variant of stream_ai_answer — same pipeline, whole answer.

    Returns {"text": <answer>, "sources": [{doc_id, title, source_file}...]}.
    Used by eval.py (always without `profile`, so its answers keep the exact
    historical user-turn shape), and the sanity check exercises the exact
    production path.
    """
    text_gen, sources, *_ = stream_ai_answer(question, history, role, profile)
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


def warm_index() -> int:
    """Eagerly build the in-memory vector index (embedding-model download +
    chunk embedding). Called at app startup so the one-time cost lands at boot
    instead of inside the first user's question. Returns the chunk count."""
    from storage.vector_store import get_index_stats
    return get_index_stats()["total_chunks"]
