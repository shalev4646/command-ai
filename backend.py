import json
import os
from pathlib import Path

from anthropic import Anthropic
from dotenv import load_dotenv

from storage.vector_store import retrieve, get_index_stats

load_dotenv(Path(__file__).parent / ".env")

client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY", "").strip())

SYSTEM_PROMPT = """אתה עוזר צבאי המסייע לחיילים ומפקדים להבין פקודות מטכ"ל.

חוקים מוחלטים:
1. ענה אך ורק על בסיס הקטעים שסופקו לך בהקשר.
2. אם המידע לא קיים בקטעים — אמור בדיוק: "המידע לא קיים בפקודות שסופקו."
3. אל תשתמש בידע כללי על הצבא.
4. כל תשובה חייבת לכלול ציטוט מדויק + מספר סעיף + שם הפקודה.

מבנה תשובה לשאלות "האם מותר לי X?":
**פסיקה:** מותר / אסור / מותר בתנאים
**מקור:** [שם הפקודה] סעיף X
**תנאים:** רשימה מפורטת
**מי מאשר:** דרגה נדרשת

מבנה תשובה לשאלות עובדתיות:
**תשובה:** תשובה ישירה
**מקור:** [שם הפקודה] סעיף X
"""


def _build_rag_context(question: str) -> str:
    chunks = retrieve(question, n_results=8)
    if not chunks:
        return "אין מסמכים טעונים במערכת."
    parts = []
    for c in chunks:
        parts.append(f"[{c['doc_id']} | {c['title']} | סעיף {c['clause']}]\n{c['text']}")
    return "\n\n---\n\n".join(parts)


def load_documents() -> list[dict]:
    json_dir = Path(__file__).parent / "storage" / "json_store"
    docs = []
    for f in sorted(json_dir.glob("*.json")):
        try:
            docs.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception:
            pass
    return docs


def get_ai_response(question: str, history: list[dict] | None = None) -> str:
    context = _build_rag_context(question)

    messages = list(history or [])
    messages.append({"role": "user", "content": question})

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        system=SYSTEM_PROMPT + f"\n\nקטעים רלוונטיים מהפקודות:\n{context}",
        messages=messages,
    )
    return response.content[0].text


def get_loaded_docs_info() -> list[dict]:
    """Return title + document_id for each loaded document."""
    return [
        {"title": d.get("title", "?"), "id": d.get("document_id", "?")}
        for d in load_documents()
        if d.get("document_id")
    ]


def get_index_info() -> dict:
    return get_index_stats()
