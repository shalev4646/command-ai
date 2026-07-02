# -*- coding: utf-8 -*-
"""Sanity check מהיר ללוגיקת ה-RAG, בלי ממשק Streamlit.

הרצה:  python eval.py            (שליפה + תשובת LLM מלאה)
       python eval.py --no-llm   (שליפה בלבד — מהיר וללא עלות API)

מריץ 3 שאלות קבועות בעברית (אחת לכל תפקיד), מדפיס את המקורות/הסעיפים
שנשלפו להקשר ואת תשובת ה-LLM. יציאה עם קוד 1 אם שאלה כלשהי נכשלה —
מתאים כבדיקת תקינות לפני git push.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from backend import get_ai_response, retrieve_for_role

QUESTIONS = [
    ("soldier", "מהן שעות השינה המינימליות המגיעות לחייל?"),
    ("commander", "אילו עונשים מוסמך מפקד להטיל בדין משמעתי?"),
    ("reserve", "אילו תגמולים מגיעים לחייל מילואים על שירות פעיל?"),
]


def main() -> int:
    skip_llm = "--no-llm" in sys.argv
    failures = 0

    for role, question in QUESTIONS:
        print("=" * 70)
        print(f"תפקיד: {role} | שאלה: {question}")
        print("-" * 70)
        try:
            chunks = retrieve_for_role(question, role)
            if not chunks:
                print("!! לא נשלפו קטעים — בדוק שה-json_store לא ריק")
                failures += 1
                continue
            print(f"נשלפו {len(chunks)} קטעים להקשר:")
            for c in chunks:
                print(f"  score={c['score']:.3f}  [{c['doc_id']}] {c['title']} — סעיף {c['clause']}")
            if skip_llm:
                continue
            answer = get_ai_response(question, role=role)
            print("\nתשובת ה-LLM:")
            print(answer)
        except Exception as e:
            print(f"!! שגיאה: {type(e).__name__}: {e}")
            failures += 1
        print()

    print("=" * 70)
    if failures:
        print(f"נכשלו {failures}/{len(QUESTIONS)} שאלות")
        return 1
    print(f"עברו {len(QUESTIONS)}/{len(QUESTIONS)} שאלות ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
