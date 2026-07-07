# -*- coding: utf-8 -*-
"""Sanity check מהיר ללוגיקת ה-RAG, בלי ממשק Streamlit.

הרצה:  python eval.py            (סט זהב לאחזור + 3 תשובות LLM מלאות)
       python eval.py --no-llm   (סט הזהב בלבד — מהיר וללא עלות API)

שני רבדים:
1. סט זהב (GOLDEN) — ~20 שאלות שמכסות את כל הפקודות הטעונות; לכל שאלה
   נבדק שהפקודה הנכונה מופיעה בין 3 הקטעים המובילים שנשלפו. רץ בלי LLM,
   בחינם, ולכן משמש שער לפני כל שינוי (מודל, קליטת פקודות, כוונון rerank).
2. שאלות המשך (FOLLOWUP) — תרחישי שיחה שבהם שאלת ההמשך חסרת הקשר;
   נבדק ששכתוב השאילתה (Haiku) מחזיר את הפקודה הנכונה לטופ-3.
3. עשן LLM (SMOKE) — 3 שאלות שעוברות את כל הצינור כולל המודל, להדפסה
   ידנית של איכות התשובה.

יציאה עם קוד 1 אם שאלה כלשהי נכשלה — מתאים כבדיקת תקינות לפני git push.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from backend import get_ai_response, retrieve_for_role

# (role, question, expected_doc_id) — the expected order must rank in the
# top-3 retrieved chunks. Questions are deliberately phrased unlike the
# order titles, so lexical overlap alone can't carry the retrieval.
GOLDEN = [
    ("commander", "מה תקרת הקנס שרשאי קצין שיפוט להטיל על טוראי?", "PM-33.0302"),
    ("soldier",   "כמה ימי מחבוש אפשר להטיל על חייל בדין משמעתי?", "PM-33.0302"),
    ("soldier",   "האם מותר לעשן בחדר האוכל בבסיס?", "PM-33.0137"),
    ("soldier",   "מה זה ריתוק משקי ומתי אפשר להטיל אותו?", "5040.05"),
    ("soldier",   "האם חייבים לחתום קבע אחרי קורס מקצועי?", "31.0203"),
    ("soldier",   "כמה ימי חופשה שנתית מגיעים לחייל בשירות חובה?", "PM-35.0402"),
    ("reserve",   "האם חייל מילואים צריך אישור כדי לצאת לחוץ לארץ?", "31.0703"),
    ("soldier",   "אילו הנחות בטיסות מגיעות לחיילים בשירות סדיר?", "36.0218"),
    ("commander", "מתי משרת קבע נדרש להחזיר הטבות כספיות כשהוא משתחרר?", "36.0527"),
    ("soldier",   "מה שעות הפתיחה המותרות של מועדון ביחידה?", "35.0818"),
    ("commander", "באילו תנאים מותר למנוע חופשה מחייל?", "PM-33.0352"),
    ("soldier",   "איך מגישים בקשת חנינה על פסק דין של בית דין צבאי?", "30.33"),
    ("soldier",   "מה קורה לחייל שנתפס משתמש בסמים?", "33.0111"),
    ("commander", "איך מאשרים חופשה ללא תשלום למשרת קבע?", "31.0517"),
    ("soldier",   "כמה שעות שינה רצופות מגיעות לחייל?", "PM-33.0213"),
    ("commander", "איזו דרגה נדרשת כדי לאשר חריגה משעות השינה של חיילים?", "PM-33.0213"),
    ("reserve",   "כמה נקודות זכות מקבל חייל מילואים על 20 ימי שירות בשנת 2016?", "013.3"),
    ("reserve",   "עד מתי אפשר להגיש השגה על חישוב התגמול הנוסף למילואים?", "013.3"),
    ("soldier",   "מה עושים עם חמץ שנמצא בבסיס במהלך פסח?", "PM-34.0205"),
    ("commander", "האם האיסור על הכנסת חמץ חל גם על אזרחים שנכנסים למחנה?", "PM-34.0205"),
    # batch 1 (2026-07-06): orders downloaded from the public IDF orders site
    ("soldier",   "אילו הטבות מגיעות לחייל שההורים שלו גרים בחוץ לארץ?", "35.0808"),
    ("soldier",   "האם חייל יכול לקבל טיפול רפואי בבית חולים אזרחי?", "61.0104"),
    ("commander", "כמה ימי מחלה יכול לצבור איש קבע ומה מקבלים עליהם בשחרור?", "36.0413"),
    ("commander", "כמה ימי חופשה שנתית מגיעים למשרת קבע?", "36.0401"),
    ("soldier",   "האם מותר לשלב פריטי לבוש אזרחיים עם מדים?", "33.0501"),
    ("soldier",   "למי אפשר לפנות אם נעשה לי עוול ביחידה והמפקד לא מטפל?", "33.0336"),
    ("commander", "האם מותר לערוך חיפוש בחפצים האישיים של חייל?", "PM-33.0309"),
    ("soldier",   "מה עושים אם חייל נפגע מהטרדה מינית ביחידה?", "33.0145"),
    ("reserve",   "איך סטודנט יכול לדחות שירות מילואים בגלל תקופת מבחנים?", "31.0603"),
    # regression: the July-2025 33.0111 PDF is a scan with a partial text
    # layer — the reporting-duty clauses were recovered from the order's
    # page on the IDF site (2026-07-06)
    ("soldier",   "מי צריך לדווח למצ\"ח על חייל החשוד בשימוש בסמים?", "33.0111"),
    # batch 2 (2026-07-07)
    ("soldier",   "האם מותר לי לקחת את הנשק הביתה כשאני יוצא לסוף שבוע?", "2.0101"),
    ("soldier",   "אחרי כמה ימים חייל שלא חזר מחופשה נחשב עריק?", "31.0513"),
    ("reserve",   "מה קורה למי שלא התייצב לצו מילואים?", "31.0521"),
    ("soldier",   "אילו אישורים ותהליכים עוברים ביחידה לפני השחרור מהצבא?", "31.0103"),
    ("soldier",   "איך ההורים שלי יכולים לקבל תשלום חודשי מהצבא אם המצב הכלכלי קשה?", "35.0210"),
    ("soldier",   "ממה מורכבת המשכורת החודשית של חייל בשירות חובה?", "35.0201"),
    ("commander", "האם המשפחה שלי זכאית לטיפול שיניים דרך הצבא?", "36.0511"),
    ("soldier",   "אילו תנאים מיוחדים יש לחייל חרדי בצבא?", "31.0901"),
    ("commander", "מה ההבדל בין שירות קבע לקבע מובהק?", "3.0501"),
    # regression: the 36.0413 definitions chunk is RTL-mangled and never
    # ranked — the service-connection question answered "not found" until
    # key-facts sections were added (2026-07-07)
    ("commander", "האם מחלה שלא קשורה לשירות מזכה בחופשת מחלה?", "36.0413"),
    # batch 3 (2026-07-07)
    ("soldier",   "האם חייבים להשתתף במסדר בוקר וכמה פעמים בשבוע יש כזה?", "PM-33.0202"),
    ("commander", "האם יום גיבוש ליחידה יורד לחיילים מימי החופשה?", "05.104"),
    ("soldier",   "מה אסור לי להעלות לאינסטגרם או לטיקטוק בזמן השירות?", "PM-33.0161"),
    ("commander", "מה עושים כשחייל ביחידה מאיים לפגוע בעצמו?", "33.0219"),
]

SMOKE = [
    ("soldier", "מהן שעות השינה המינימליות המגיעות לחייל?"),
    ("commander", "אילו עונשים מוסמך מפקד להטיל בדין משמעתי?"),
    ("reserve", "אילו תגמולים מגיעים לחייל מילואים על שירות פעיל?"),
]

# (role, history, follow-up, expected_doc_id) — the rewrite must fold the
# conversation context back into the query so the right order ranks top-3.
# Runs in the LLM layer (the rewrite itself is a Haiku call), not --no-llm.
FOLLOWUP = [
    ("reserve",
     [{"role": "user", "content": "כמה שעות שינה רצופות מגיעות לחייל?"},
      {"role": "assistant", "content": "**תשובה:** חייל זכאי ל-7 שעות שינה רצופות בין 22:00 ל-06:00. **מקור:** [PM-33.0213] סעיפים 6, 8."}],
     "ומה לגבי חייל מילואים?", "PM-33.0213"),
    ("soldier",
     [{"role": "user", "content": "כמה ימי חופשה שנתית מגיעים לחייל בשירות חובה?"},
      {"role": "assistant", "content": "**תשובה:** חייל בשירות חובה זכאי ל-18 ימי חופשה שנתית. **מקור:** [PM-35.0402] סעיף 4."}],
     "ומי מאשר אותה?", "PM-35.0402"),
]

TOP_K = 3


def run_golden() -> int:
    failures = 0
    print("=" * 70)
    print(f"סט זהב — {len(GOLDEN)} שאלות אחזור (הפקודה הנכונה בטופ-{TOP_K})")
    print("=" * 70)
    for role, question, expected in GOLDEN:
        try:
            chunks = retrieve_for_role(question, role)
            top_docs = []
            for c in chunks:  # distinct docs, in rank order
                if c["doc_id"] not in top_docs:
                    top_docs.append(c["doc_id"])
            ok = expected in top_docs[:TOP_K]
        except Exception as e:
            print(f"✗ [{role}] {question}\n    !! שגיאה: {type(e).__name__}: {e}")
            failures += 1
            continue
        if ok:
            print(f"✓ [{role}] {question}")
        else:
            print(f"✗ [{role}] {question}")
            print(f"    ציפינו {expected}, קיבלנו: {top_docs[:TOP_K]}")
            failures += 1
    return failures


def run_followup() -> int:
    from backend import _standalone_question

    failures = 0
    print("=" * 70)
    print(f"שאלות המשך — {len(FOLLOWUP)} תרחישים (שכתוב + אחזור בטופ-{TOP_K})")
    print("=" * 70)
    for role, history, question, expected in FOLLOWUP:
        try:
            rewritten = _standalone_question(question, history)
            chunks = retrieve_for_role(rewritten, role)
            top_docs = []
            for c in chunks:
                if c["doc_id"] not in top_docs:
                    top_docs.append(c["doc_id"])
            ok = expected in top_docs[:TOP_K]
        except Exception as e:
            print(f"✗ [{role}] {question}\n    !! שגיאה: {type(e).__name__}: {e}")
            failures += 1
            continue
        if ok:
            print(f"✓ [{role}] {question}  ←  {rewritten}")
        else:
            print(f"✗ [{role}] {question}  ←  {rewritten}")
            print(f"    ציפינו {expected}, קיבלנו: {top_docs[:TOP_K]}")
            failures += 1
    return failures


def run_smoke() -> int:
    failures = 0
    for role, question in SMOKE:
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
            answer = get_ai_response(question, role=role)
            print("\nתשובת ה-LLM:")
            print(answer)
        except Exception as e:
            print(f"!! שגיאה: {type(e).__name__}: {e}")
            failures += 1
        print()
    return failures


def main() -> int:
    failures = run_golden()
    if "--no-llm" not in sys.argv:
        failures += run_followup()
        failures += run_smoke()

    print("=" * 70)
    if failures:
        print(f"נכשלו {failures} בדיקות")
        return 1
    print("כל הבדיקות עברו ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
