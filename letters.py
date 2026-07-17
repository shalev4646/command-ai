"""מחולל מכתבים — טיוטות פנייה רשמיות המבוססות על פקודות מטכ"ל.

Each letter is one Anthropic call (no caching value — every letter is
unique), grounded in retrieved order excerpts so the draft can cite the
clause it relies on. The output is explicitly a DRAFT the soldier must
review and complete — missing personal details are rendered as [___]
placeholders, never invented.
"""

from backend import (
    MODEL,
    _context_from_chunks,
    _sources_from_chunks,
    client,
    retrieve_for_role,
)

# Formal-letter output is short and formulaic; a tight cap keeps the cost
# of a single letter well under one regular Q&A answer.
_LETTER_MAX_TOKENS = 900

_SYSTEM = """אתה מנסח מכתבים רשמיים עבור חיילי צה"ל, לפי הנהוג בצבא.

כללי הניסוח:
- מבנה: תאריך, אל (בעל התפקיד הנמען), מאת, הנדון, גוף המכתב, סיום מכובד ("בכבוד רב," / "בברכה,"), חתימה (שם, דרגה, מ"א, יחידה).
- טון רשמי, ענייני ומכבד. משפטים קצרים. בלי סופרלטיבים ובלי איומים.
- כאשר קטעי הפקודות המצורפים תומכים בבקשה — הפנה אליהם במפורש בגוף המכתב (מספר פקודה וסעיף). אל תצטט פקודה שלא הופיעה בקטעים.
- פרט אישי שלא נמסר לך — כתוב במקומו [___]. לעולם אל תמציא שמות, תאריכים או מספרים אישיים.
- אל תבטיח תוצאה ואל תנסח קביעות משפטיות מוחלטות; זו בקשה, לא פסיקה.
- שורת הסיום האחרונה, מופרדת בקו: "— טיוטה שנוצרה ב-CommandAI. קרא, השלם את החסר וודא את הפרטים לפני הגשה."
"""

# letter_key -> UI + retrieval recipe. `query` feeds the regular RAG
# retrieval so the draft cites the same orders the chatbot would. A field's
# optional third element marks it CONTENT (True): its value joins the
# retrieval query, so "סיבת הבקשה: אבל במשפחה" pulls the bereavement-leave
# clauses, not just the type's generic ones. Personal fields (name, dates,
# recipient) stay out — they only add embedding noise.
LETTER_TYPES: dict[str, dict] = {
    "special_leave": {
        "title": "בקשת חופשה מיוחדת",
        "query": "חופשה מיוחדת מטעמים אישיים או משפחתיים לחייל בשירות חובה",
        "fields": [
            ("שם מלא ודרגה", "טוראי ישראל ישראלי"),
            ("הנמען", "מפקד הפלוגה"),
            ("סיבת הבקשה", "אירוע משפחתי / נסיבות אישיות", True),
            ("התאריכים המבוקשים", "12-14.8.2026"),
        ],
    },
    "discipline_appeal": {
        "title": "ערר על פסק דין משמעתי",
        "query": "ערר על החלטת דין משמעתי עונש קצין שיפוט",
        "fields": [
            ("שם מלא ודרגה", ""),
            ("מתי נערך הדין ובפני מי", ""),
            ("מה נפסק", "ריתוק / מחבוש / קנס", True),
            ("נימוקי הערר", "עונש חמור מדי / נסיבות שלא נשקלו", True),
        ],
    },
    "ombudsman_complaint": {
        "title": "קבילה לנציב קבילות החיילים",
        "query": "הגשת קבילה לנציב קבילות החיילים",
        "fields": [
            ("שם מלא ודרגה", ""),
            ("נושא הקבילה", "", True),
            ("מה נעשה עד כה ביחידה", "פניתי למפקד בתאריך..."),
        ],
    },
    "commander_interview": {
        "title": "בקשה לראיון אצל מפקד",
        "query": "זכות חייל לראיון אצל מפקד",
        "fields": [
            ("שם מלא ודרגה", ""),
            ("בפני מי מבוקש הראיון", "מפקד הגדוד"),
            ("נושא הראיון", "", True),
        ],
    },
    "reserve_deferral": {
        "title": "בקשת דחיית שירות מילואים (ולת\"ם)",
        "query": "דחיית שירות מילואים ולתם בקשה",
        "fields": [
            ("שם מלא ודרגה", ""),
            ("מועד הצו ומשך השירות", ""),
            ("סיבת הדחייה", "לימודים / עבודה / נסיבות אישיות", True),
        ],
    },
}


def _retrieval_query(lt: dict, details: dict[str, str]) -> str:
    """The type's base query, enriched with the user's CONTENT-field values
    (capped — a rambling reason field shouldn't drown the base query)."""
    content_labels = {f[0] for f in lt["fields"] if len(f) > 2 and f[2]}
    extra = " ".join(
        v.strip() for k, v in details.items()
        if k in content_labels and v and v.strip()
    )[:120]
    return f"{lt['query']} {extra}".strip()


def compose_letter(letter_key: str, details: dict[str, str], role: str = "soldier") -> dict:
    """Draft a formal letter of type `letter_key` from the user's `details`.

    Returns {"text": <letter>, "sources": [{doc_id, title, source_file}...]}
    — same sources contract as a chat answer, so the UI can reuse the
    existing source-link row under the draft.
    """
    lt = LETTER_TYPES[letter_key]
    chunks = retrieve_for_role(_retrieval_query(lt, details), role)
    context = _context_from_chunks(chunks)

    filled = "\n".join(
        f"- {label}: {value.strip()}" for label, value in details.items() if value and value.strip()
    ) or "(לא נמסרו פרטים — השאר מקומות ריקים)"
    user_content = (
        f"נסח עבורי: {lt['title']}.\n\n"
        f"הפרטים שמסר החייל:\n{filled}\n\n"
        f"קטעים רלוונטיים מהפקודות:\n{context}"
    )

    msg = client.messages.create(
        model=MODEL,
        max_tokens=_LETTER_MAX_TOKENS,
        system=_SYSTEM,
        messages=[{"role": "user", "content": user_content}],
    )
    # same usage shape as the chat path's usage_holder, so metrics.log_question can
    # log a letter row (and its cost) exactly like a chat question
    usage = {
        "input_tokens": msg.usage.input_tokens,
        "output_tokens": msg.usage.output_tokens,
        "cache_creation_input_tokens": getattr(msg.usage, "cache_creation_input_tokens", 0) or 0,
        "cache_read_input_tokens": getattr(msg.usage, "cache_read_input_tokens", 0) or 0,
    }
    return {
        "text": msg.content[0].text,
        "sources": _sources_from_chunks(chunks),
        "usage": usage,
        # a draft cut mid-sentence by the token cap must say so — the UI
        # warns instead of letting a half-letter pass as complete
        "truncated": msg.stop_reason == "max_tokens",
    }
