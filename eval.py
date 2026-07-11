# -*- coding: utf-8 -*-
"""Sanity check מהיר ללוגיקת ה-RAG, בלי ממשק Streamlit.

הרצה:  python eval.py            (סט זהב לאחזור + 3 תשובות LLM מלאות)
       python eval.py --no-llm   (סט הזהב בלבד — מהיר וללא עלות API)

שני רבדים:
1. סט זהב (GOLDEN) — ~20 שאלות שמכסות את כל הפקודות הטעונות; לכל שאלה
   נבדק שהפקודה הנכונה מופיעה בין 3 הקטעים המובילים שנשלפו. רץ בלי LLM,
   בחינם, ולכן משמש שער לפני כל שינוי (מודל, קליטת פקודות, כוונון rerank).
2. שאלות "מלוכלכות" (DIRTY) — אותם נושאים בניסוח של חייל אמיתי: סלנג,
   שגיאות כתיב, שאלות קצרות ומעורפלות. רץ בלי LLM כמו סט הזהב.
3. שאלות המשך (FOLLOWUP) — תרחישי שיחה שבהם שאלת ההמשך חסרת הקשר;
   נבדק ששכתוב השאילתה (Haiku) מחזיר את הפקודה הנכונה לטופ-3.
4. מחוץ למאגר (NOSCOPE) — שאלות שאין להן תשובה בפקודות הטעונות; נבדק
   שהמודל עונה "המידע לא קיים בפקודות שסופקו" ולא ממציא מקור. עולה כסף
   (קריאת Opus מלאה לכל שאלה), לכן מדולג עם --no-llm.
5. עשן LLM (SMOKE) — 3 שאלות שעוברות את כל הצינור כולל המודל, להדפסה
   ידנית של איכות התשובה.
6. מבני (STRUCTURAL) — שלמות שכבות הדאטה של פיצ'רי ה-UI: מיפוי סעיף→עמוד,
   תאריכי נוסח, מסלולי "למי פונים", סכימת המכתבים, וזהות-בייטים של תוכן
   המשתמש כשאין פרופיל (שומר הקאש). רץ בחינם, בלי LLM.

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
    # regression: "right doc, wrong chunk" — the no-show question retrieved
    # only a punishment-table chunk until top_doc_depth + the attendance
    # key-facts section (2026-07-07)
    ("soldier",   "מה קורה אם לא מגיעים לדיון משמעתי שזומנתי אליו?", "PM-33.0302"),
    ("commander", "האם יום גיבוש ליחידה יורד לחיילים מימי החופשה?", "05.104"),
    ("soldier",   "מה אסור לי להעלות לאינסטגרם או לטיקטוק בזמן השירות?", "PM-33.0161"),
    ("commander", "מה עושים כשחייל ביחידה מאיים לפגוע בעצמו?", "33.0219"),
    # batch 4 (2026-07-08): appearance/hair letter + joint service + religion
    # (ingested manually — API monthly limit exhausted mid-batch)
    ("soldier",   "כמה קצר חייב להיות השיער שלי ואילו תספורות אסורות?", "33-05-01"),
    ("soldier",   "האם אני חייב אישור כדי לגדל זקן?", "33-05-01"),
    ("soldier",   "מותר לי להסתובב עם עגיל במדים?", "33-05-01"),
    ("soldier",   "מותר לי להיכנס לחדר של הבנות ביחידה?", "PM-33.0207"),
    ("soldier",   "האם האוכל שמגישים בבסיס חייב להיות כשר?", "PM-34.0101"),
    ("commander", "אילו עבודות מותר לבצע ביחידה בשבת?", "PM-34.0101"),
    # batch 5 (2026-07-11): abroad-during-service, private work, alcohol —
    # ingested from the site's canonical HTML (the media PDFs are stale or
    # digit-scrambled); release-leave key-facts added to the leave order
    ("soldier",   "כמה ימים מותר לי לטוס לחו\"ל במהלך כל השירות הסדיר?", "31.0701"),
    ("soldier",   "כמה זמן מראש מגישים בקשה לצאת לחו\"ל?", "31.0701"),
    ("commander", "מה עושים עם חייל שטס לחו\"ל בלי אישור?", "31.0701"),
    ("soldier",   "אני רוצה לעבוד במלצרות אחרי שעות הבסיס — זה מותר?", "33.0115"),
    ("commander", "מי מוסמך לאשר לחייל חובה לעבוד בעבודה פרטית בגלל מצב כלכלי?", "33.0115"),
    ("soldier",   "נתפסתי עם בקבוק וודקה בחדר בבסיס — מה בדיוק אסור?", "33.0220"),
    ("commander", "האם מותר להגיש אלכוהול באירוע יחידתי ומי רשאי לאשר?", "33.0220"),
    # regression: the release-leave annex table is RTL-mangled in raw_text —
    # answered "not found" until the key-facts clauses (2026-07-11)
    ("soldier",   "כמה ימי חופשת שחרור מקבלים לפני השחרור?", "PM-35.0402"),
]

# (role, question, expected_doc_id) — same contract as GOLDEN, but phrased the
# way soldiers actually type: slang (סדירניק, לחטוף, סופש), typos (חפשה, חול),
# and short/vague questions with almost no lexical overlap with the order title.
DIRTY = [
    ("soldier",   "כמה ימי חופש מגיעים לסדירניק בשנה?", "PM-35.0402"),
    ("soldier",   "כמה ימי חפשה שנתית מגיעים לי?", "PM-35.0402"),
    ("soldier",   "המפקד מעיר אותנו אחרי 4 שעות שינה, זה בסדר?", "PM-33.0213"),
    ("soldier",   "מתי מותר להעיר חייל באמצע הלילה?", "PM-33.0213"),
    ("soldier",   "כמה מחבוש אפשר לחטוף על משפט בצבא?", "PM-33.0302"),
    ("soldier",   "מותר להעלות סטורי מהבסיס לאינסטגרם?", "PM-33.0161"),
    ("soldier",   "אפשר לנסוע הביתה עם הנשק בסופש?", "2.0101"),
    ("soldier",   "חבר שלי לא חזר מחופשה כבר שבוע, הוא נחשב עריק?", "31.0513"),
    ("soldier",   "המפקד דופק אותי כל הזמן, למי אפשר להתלונן עליו?", "33.0336"),
    ("soldier",   "כמה כסף מקבל חייל סדיר בחודש?", "35.0201"),
    ("soldier",   "ההורים שלי במצב כלכלי קשה, הצבא יכול לעזור להם?", "35.0210"),
    ("soldier",   "איפה בכלל מותר לעשן בבסיס?", "PM-33.0137"),
    ("soldier",   "יורדים עליי על התספורת, מה בכלל מותר?", "33-05-01"),
    ("soldier",   "מותר עגיל בצבא?", "33-05-01"),
    ("commander", "חייל שלי מאיים שיפגע בעצמו, מה אני עושה?", "33.0219"),
    ("reserve",   "מילואימניק צריך להגיד לצבא לפני שהוא טס לחול?", "31.0703"),
    ("reserve",   "כמה כסף מקבלים על מילואים חוץ מהמשכורת?", "013.3"),
]

# (role, question) — questions whose answer is NOT in any ingested order
# (civil law, courses, medical profile, equipment charging). The pipeline
# still retrieves the nearest chunks, so these are hallucination bait: the
# pass condition is an honest refusal, not an answer.
NOSCOPE = [
    ("soldier",   "מה תנאי הקבלה לקורס טיס?"),
    ("soldier",   "איך אני יכול להוריד פרופיל רפואי?"),
    ("soldier",   "מה גובה הפיקדון והמענק שמקבלים אחרי השחרור?"),
    ("soldier",   "אילו הטבות מגיעות לחייל משוחרר בלימודים אקדמיים?"),
    ("commander", "מה הנוהל לחיוב חייל על אובדן ציוד צבאי?"),
    ("reserve",   "כמה ימי מילואים מותר לקרוא לי בשנה לפי החוק?"),
]

# The sentence the system prompt mandates for missing information. When it
# appears verbatim the refusal is unambiguous, and a **מקור:** block after it
# is context ("here is what I *do* have"), not fabrication.
_MANDATED_REFUSAL = "המידע לא קיים בפקודות שסופקו"

# Looser rewordings, accepted only when the answer cites no source: a mixed
# answer ("לא מצאתי סעיף מדויק, אבל לפי...") that goes on to cite a **מקור:**
# is a fabrication and must fail.
_REFUSAL_MARKERS = (
    "לא קיים בפקודות",
    "אין בפקודות",
    "לא מצאתי",
    "לא נמצא בפקודות",
    "אינו מופיע בפקודות",
)

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


def _run_retrieval_set(name: str, cases: list) -> int:
    failures = 0
    print("=" * 70)
    print(f"{name} — {len(cases)} שאלות אחזור (הפקודה הנכונה בטופ-{TOP_K})")
    print("=" * 70)
    for role, question, expected in cases:
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


def run_golden() -> int:
    return _run_retrieval_set("סט זהב", GOLDEN)


def run_dirty() -> int:
    return _run_retrieval_set("שאלות מלוכלכות", DIRTY)


def run_noscope() -> int:
    failures = 0
    print("=" * 70)
    print(f"מחוץ למאגר — {len(NOSCOPE)} שאלות (מצופה סירוב כן, לא תשובה מומצאת)")
    print("=" * 70)
    for role, question in NOSCOPE:
        try:
            answer = get_ai_response(question, role=role)
        except Exception as e:
            print(f"✗ [{role}] {question}\n    !! שגיאה: {type(e).__name__}: {e}")
            failures += 1
            continue
        refused_soft = any(m in answer for m in _REFUSAL_MARKERS)
        cited = "**מקור:**" in answer
        ok = _MANDATED_REFUSAL in answer or (refused_soft and not cited)
        if ok:
            print(f"✓ [{role}] {question}")
        else:
            print(f"✗ [{role}] {question}")
            snippet = " ".join(answer.split())[:220]
            reason = "סירב חלקית אבל ציטט מקור" if refused_soft else "ענה במקום לסרב"
            print(f"    המודל {reason}: {snippet}")
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


def run_structural() -> int:
    """Free integrity gate for the UI features' data layers — the things a
    reingest or a hand edit can silently break: clause-page mappings, doc
    dates, escalation coverage, the letters schema, and the byte-identity
    of the no-profile user turn (the prompt-cache/eval contract)."""
    import backend
    import doc_dates
    import escalation_paths
    import letters

    failures = 0
    checks: list[tuple[str, bool, str]] = []
    docs = backend.load_documents()
    doc_ids = [d.get("document_id") for d in docs if d.get("document_id")]

    # clause_pages.json: loads, covers a healthy share of docs, and every
    # mapped page is a positive int reachable through page_for_clause
    pages = backend._get_clause_pages()
    n_pages = sum(len(v) for v in pages.values())
    checks.append(("clause_pages נטען ולא ריק", bool(pages) and n_pages > 500,
                   f"{len(pages)} מסמכים, {n_pages} מיפויים"))
    bad_pages = [
        (d, c) for d, m in pages.items() for c, p in m.items()
        if not (isinstance(p, int) and p > 0)
    ]
    checks.append(("כל עמוד ממופה הוא int חיובי", not bad_pages, f"{bad_pages[:3]}"))
    sample_ok = all(
        backend.page_for_clause(d, next(iter(m))) is not None
        for d, m in list(pages.items())[:5] if m
    )
    checks.append(("page_for_clause מחזיר עמוד למדגם", sample_ok, "5 מסמכים ראשונים"))
    checks.append(("page_for_clause בטוח לקלט חסר",
                   backend.page_for_clause(None, None) is None
                   and backend.page_for_clause("אין-כזה", "w1") is None, ""))

    # doc_dates: parses, plausible years, badge formats
    dated = [d for d in doc_ids if doc_dates.date_for(d)]
    years = [int(doc_dates.date_for(d)[:4]) for d in dated]
    checks.append(("תאריכי נוסח: כיסוי סביר", len(dated) >= 25, f"{len(dated)}/{len(doc_ids)}"))
    checks.append(("תאריכי נוסח: שנים הגיוניות", all(1948 <= y <= 2027 for y in years),
                   f"{sorted(set(years))[:4]}..."))
    import re as _re
    # badge() is None below _MIN_BADGE_YEAR (old extractions understate
    # freshness) and a well-formed date string from the threshold on
    bad_badges = [
        d for d in dated
        if (int(doc_dates.date_for(d)[:4]) >= doc_dates._MIN_BADGE_YEAR)
        != bool(_re.fullmatch(r"(\d{2}\.)?\d{2}\.\d{4}", doc_dates.badge(d) or ""))
    ]
    checks.append(("תגי תאריך: סף + פורמט עקביים", not bad_badges, f"{bad_badges[:3]}"))

    # escalation: total coverage, sane shapes, gating both-ways
    paths_ok = all(
        (p := escalation_paths.path_for(d)) and p.get("steps")
        and 1 <= len(p["steps"]) <= 5 and all(isinstance(s, str) and s for s in p["steps"])
        for d in doc_ids + ["מזהה-שלא-קיים"]
    )
    checks.append(("מסלול פנייה תקין לכל פקודה (+fallback)", paths_ok, f"{len(doc_ids)} מזהים"))
    gate_show = escalation_paths.relevant_for("מגיע לי יום חופשה ולא מאשרים", "PM-35.0402")
    gate_hide = not escalation_paths.relevant_for("מותר להכניס נרגילה לבסיס?", "PM-33.0137")
    gate_always = escalation_paths.relevant_for("מה קורה אם לא מגיעים לדיון?", "PM-33.0302")
    checks.append(("גייטינג הרצועה: מציג/מסתיר/תמיד", gate_show and gate_hide and gate_always,
                   f"show={gate_show} hide={gate_hide} always={gate_always}"))

    # letters: schema shape only (composing costs money). Fields are
    # (label, placeholder) or (label, placeholder, True) for content fields
    # that feed the retrieval query; every type needs at least one.
    lt_ok = all(
        v.get("title") and v.get("query")
        and v.get("fields") and all(len(f) in (2, 3) for f in v["fields"])
        and any(len(f) > 2 and f[2] for f in v["fields"])
        for v in letters.LETTER_TYPES.values()
    )
    checks.append(("סכימת סוגי המכתבים", lt_ok, f"{len(letters.LETTER_TYPES)} סוגים"))
    lq = letters._retrieval_query(
        letters.LETTER_TYPES["special_leave"],
        {"שם מלא ודרגה": "טוראי ישראל ישראלי", "סיבת הבקשה": "אבל במשפחה"},
    )
    checks.append(("אחזור מכתב: סיבה נכנסת, שם לא",
                   "אבל במשפחה" in lq and "ישראל ישראלי" not in lq, lq))

    # THE cache contract: no profile == the exact historical user turn
    q, ctx = "שאלה לדוגמה", "הקשר לדוגמה"
    legacy = f"{q}\n\n{backend._CONTEXT_HEADER}\n{ctx}"
    checks.append(("זהות-בייטים של תוכן משתמש בלי פרופיל",
                   backend._compose_user_content(q, ctx, None).encode() == legacy.encode()
                   and backend._compose_user_content(q, ctx, []).encode() == legacy.encode(), ""))
    with_p = backend._compose_user_content(q, ctx, ["חייל בודד"])
    checks.append(("שורת פרופיל נכנסת בין השאלה להקשר",
                   with_p.startswith(q) and "חייל בודד" in with_p
                   and with_p.endswith(f"{backend._CONTEXT_HEADER}\n{ctx}"), ""))

    print("=" * 70)
    print(f"בדיקות מבניות — {len(checks)} בדיקות (שכבות דאטה של פיצ'רי UI)")
    print("=" * 70)
    for name, ok, detail in checks:
        print(f"{'✓' if ok else '✗'} {name}" + (f"  ({detail})" if detail and not ok else ""))
        if not ok:
            failures += 1
    return failures


def main() -> int:
    failures = run_structural()
    failures += run_golden()
    failures += run_dirty()
    if "--no-llm" not in sys.argv:
        failures += run_followup()
        failures += run_noscope()
        failures += run_smoke()

    print("=" * 70)
    if failures:
        print(f"נכשלו {failures} בדיקות")
        return 1
    print("כל הבדיקות עברו ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
