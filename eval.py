# -*- coding: utf-8 -*-
"""Sanity check מהיר ללוגיקת ה-RAG, בלי ממשק Streamlit.

הרצה:  python eval.py            (סט זהב לאחזור + 3 תשובות LLM מלאות)
       python eval.py --no-llm   (סט הזהב בלבד — מהיר וללא עלות API)
       python eval.py --facts    (+ שכבת נכונות-תשובות, ~20 קריאות Opus ≈ $2)

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
7. נכונות תשובות (FACTS, ‏--facts בלבד) — הרובד היחיד שבודק שהתשובה עצמה
   נכונה ולא רק שהאחזור מצא את הפקודה: לכל שאלה עובדות-מפתח מאומתות מול
   הפקודות (מספרים, דרגות, ציטוט הפקודה) שחייבות להופיע בתשובת ה-LLM, וסירוב
   נחשב כישלון. חלק מהשאלות הן שאלות אמיתיות מהפיילוט שסורבו/קיבלו 👎.
   יקר (~20 קריאות Opus ≈ $2) — לכן מאחורי דגל, לא בברירת המחדל.
   OBSERVE — שאלות אמת עמומות שמודפסות לקריאה ידנית בלבד, בלי pass/fail.

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
    # batch 6 (2026-07-20): the 7 orders of the 2026-07-12 corpus expansion
    # shipped with no eval coverage at all (raw_text-only ingestion). The
    # medical-profile question moved here FROM NOSCOPE — it was hallucination
    # bait until 32.0402 was ingested and now has a real in-corpus answer.
    ("soldier",   "איך אני יכול להוריד פרופיל רפואי?", "32.0402"),
    ("commander", "מה הנוהל כשנראה שמצב הבריאות של חייל שלי השתנה?", "32.0402"),
    ("soldier",   "הייתי מעורב בתאונת דרכים עם רכב צבאי — מי מוסמך להתלות את הרישיון הצבאי שלי?", "33.1104"),
    ("soldier",   "אילו זכויות מגיעות לחיילת בהריון במהלך השירות?", "36.0406"),
    ("soldier",   "האם מגיעה היעדרות מהשירות לצורך טיפולי פוריות?", "36.0406"),
    ("soldier",   "לא סיימתי בית ספר יסודי — האם הצבא חייב להשלים לי השכלה בזמן השירות?", "37.0102"),
    ("commander", "מתי אפשר לשלול מחייל משוחרר את מענק השחרור והפיקדון שלו?", "35.0234"),
    ("commander", "איזה מענק מקבל משרת קבע בסיום שירותו ומה נחשב לשירות תקין לחישובו?", "20.0502"),
    ("soldier",   "איזו דרגת רישיון צבאי צריך כדי לנהוג ברכב משא כבד?", "58.0202"),
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
    # batch 6 (2026-07-20): slang phrasings for the newest orders
    ("soldier",   "אפשר להוריד פרופיל בצבא?", "32.0402"),
    ("soldier",   "עשיתי תאונה עם רכב צבאי, יקחו לי את הרישיון?", "33.1104"),
    ("soldier",   "חיילת בהריון, מה מגיע לה?", "36.0406"),
    ("soldier",   "לא סיימתי בית ספר, הצבא נותן להשלים לימודים?", "37.0102"),
]

# Real pilot questions whose retrieval is KNOWN-BROKEN — printed every run so
# the gap stays visible, but not counted as failures (the gate stays green).
# (role, question, expected_doc_id), same contract as GOLDEN.
XFAIL_RETRIEVAL = [
    # 2026-07-10, refused live and still failing: heavy typos ("חפשש",
    # "להתשחרר") defeat both the embedding and the lexical variants —
    # PM-35.0402 doesn't crack the top-5. Candidate fix: run the Haiku
    # rewrite on first questions too (today it fires only on follow-ups),
    # normalizing typos before retrieval.
    ("soldier", "כמה ימי חפשש מגיע לי אם אני אמור להתשחרר בקרוב?", "PM-35.0402"),
]

# (role, question) — questions whose answer is NOT in any ingested order
# (civil law, courses, equipment charging). The pipeline still retrieves the
# nearest chunks, so these are hallucination bait: the pass condition is an
# honest refusal, not an answer. The medical-profile question that used to sit
# here moved to GOLDEN when 32.0402 (שינוי כושר בריאותי) was ingested.
NOSCOPE = [
    ("soldier",   "מה תנאי הקבלה לקורס טיס?"),
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
    # Real pilot conversations whose follow-up was refused/downvoted live
    # (2026-07-10 / 2026-07-19) — the rewrite must carry the referent back in.
    ("soldier",
     [{"role": "user", "content": "מותר למפקד שלי להעיר אותי ב2 בלילה לעשות מסדר?"},
      {"role": "assistant", "content": "**פסיקה:** אסור, למעט חריגים שאושרו כדין. **מקור:** [PM-33.0213] — כל חייל זכאי לשבע שעות שינה רצופות בין 22:00 ל-06:00."}],
     "אז למי מותר להחריג משהו כזה?", "PM-33.0213"),
    ("commander",
     [{"role": "user", "content": "חייל ביקש להשתחרר מפעילות נופש כי היא פוגעת באמונתו — אני חייב לשחרר אותו?"},
      {"role": "assistant", "content": "**פסיקה:** חייב להתחשב באורח חייו הדתי של החייל. **מקור:** [PM-34.0101] הדת בצה\"ל."}],
     "כמה זמן מראש הוא צריך לבקש את השחרור?", "PM-34.0101"),
]

# ── שכבת FACTS: נכונות התשובה עצמה (רץ רק עם --facts, עולה כסף) ──
# (role, question, expected_doc_id, fact_groups): התשובה חייבת (א) לא לסרב,
# (ב) לאזכר את הפקודה הצפויה בין 3 המקורות המובילים של האחזור, (ג) להכיל
# מכל fact_group לפחות חלופה אחת. העובדות מאומתות מול סעיפי ה-key-facts,
# punishment_authority.py ו-entitlements.py (שאומתו מול ה-PDF מילה-במילה).
# חלק מהשאלות = שאלות אמת מהפיילוט שסורבו/דוסלקו (מסומנות) — רגרסיה חיה.
FACTS = [
    ("soldier", "כמה שעות שינה רצופות מגיעות לי ובאילו שעות?", "PM-33.0213",
     [["שבע", "7"], ["22:00"], ["33.0213", "שעות השינה"]]),
    # pilot 2026-07-09, refused live — the 4h+3h completion rule answers it
    ("soldier", "כמה מותר לקצץ לי משעות השינה בשביל תורנות שמירה?", "PM-33.0213",
     [["4", "ארבע"], ["33.0213", "שעות השינה"]]),
    # pilot 2026-07-19, follow-up downvoted — the approval-ranks clause answers it
    ("soldier", "למי מותר לאשר חריגה משעות השינה של חיילים?", "PM-33.0213",
     [["סא\"ל", "אל\"ם", "תא\"ל", "אלוף", "מח\"ט"], ["33.0213", "שעות השינה"]]),
    # pilot 2026-07-09, refused live — 35.0808 has a dedicated housing clause
    ("soldier", "איזו עזרה בדיור מגיעה לי כחייל בודד?", "35.0808",
     [["שכר דירה", "שכ\"ד", "קיבוץ", "בית החייל"], ["35.0808", "בודד"]]),
    # pilot, got both 👍 and 👎 — 30-day first-degree-relative visit leave
    ("soldier", "ההורים שלי גרים בחו\"ל — כמה ימי חופשה מגיעים לי בשנה כדי לבקר אותם?", "35.0808",
     [["30"], ["35.0808", "בודד"]]),
    # pilot 2026-07-12, refused live — 31.0103 has an exact lost-ID clause
    ("soldier", "איבדתי את תעודת החוגר רגע לפני השחרור — מה עושים לי?", "31.0103",
     [["דין", "שיפוט", "תלונה"], ["31.0103", "שחרור ופיטור"]]),
    # asked 4x locally — forbidden in foot movement, single earpiece allowed
    ("soldier", "מותר לי ללכת עם אוזניות כשאני במדים?", "33-05-01",
     [["אסור"], ["תנועה רגלית", "אוזניה בודדת", "בודדת"], ["33-05-01", "הופעה ולבוש"]]),
    # pilot, downvoted — the ban list: political/military statements, wide groups
    ("commander", "חיילים שלי מעלים סרטונים מהבסיס לטיקטוק — מה בדיוק אסור להם לפרסם?", "PM-33.0161",
     [["מפלגתי", "מדיני", "צבאי", "ביטחוני"], ["33.0161", "הרשתי"]]),
    ("soldier", "כמה זמן לפני כניסת השבת חייבים לשחרר אותי הביתה לחופשה?", "PM-34.0101",
     [["שעתיים"], ["34.0101", "הדת"]]),
    ("soldier", "כמה ימי חופשת שחרור מקבלים לפני השחרור?", "PM-35.0402",
     [["7", "10", "14", "שבעה", "עשרה"], ["35.0402", "חופשות לחיילים"]]),
    ("soldier", "כמה ימי חופשה שנתית מגיעים לחייל בשירות חובה?", "PM-35.0402",
     [["18"], ["35.0402", "חופשות לחיילים"]]),
    # moved from NOSCOPE — 32.0402 is exactly the profile-change procedure
    ("soldier", "איך אני יכול להוריד פרופיל רפואי?", "32.0402",
     [["ועדה רפואית"], ["32.0402", "כושר בריאותי"]]),
    ("soldier", "הייתי מעורב בתאונה עם רכב צבאי — באילו תנאים יתלו לי את הרישיון הצבאי?", "33.1104",
     [["יסוד להאשים", "עבירה", "ממצ\"פ", "שיטור"], ["33.1104", "התליית", "להתלות"]]),
    ("commander", "מתי אפשר לשלול מחייל משוחרר את מענק השחרור והפיקדון?", "35.0234",
     [["אי-התאמה", "אי התאמה", "הרשעה", "עבירה"], ["35.0234", "שלילת"]]),
    ("soldier", "כמה ימי מחבוש מקסימום אפשר לקבל בדין משמעתי?", "PM-33.0302",
     [["30"], ["33.0302", "דין משמעתי"]]),
    ("soldier", "חיילת בהריון — אילו הקלות והתאמות מגיעות לה בשירות?", "36.0406",
     [["36.0406", "הורות"]]),
]

# שאלות אמת עמומות — נדפסות לקריאה ידנית בלבד (אין להן pass/fail חד):
# חלקן פער-קורפוס אמיתי, חלקן תשובה חלקית לגיטימית (כלל 2 בפרומפט).
OBSERVE = [
    # likely corpus gap: פניות הציבור procedure isn't the קבילות order
    ("soldier", "פניתי לפניות הציבור בגלל מצוקה שקשורה ליחס המפקד — הם חייבים לעדכן את המפקד?"),
    # amounts are civil law (out of corpus) but 35.0234 states the timing —
    # rule 2 expects a partial answer, not a bare refusal
    ("soldier", "כמה כסף אני אמור לקבל מענק שחרור ואחרי כמה זמן הוא יכנס לחשבון הבנק?"),
    # pilot, downvoted: club hours are unit-set (35.0818), Shabbat rules in PM-34.0101
    ("soldier", "האם מותר לראות טלוויזיה במועדון בשבת?"),
    # pilot, refused: 2.0101 covers approvals (purpose-bound, ≤6 months)
    ("soldier", "מסוכן באזור שאני גר — אפשר לבקש אישור לשאת נשק הביתה גם אם אני לא חייב?"),
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


def run_xfail() -> None:
    """Known-broken retrieval cases: printed for visibility, never counted.
    A case that starts passing is called out so it can graduate to DIRTY."""
    print("=" * 70)
    print(f"XFAIL — {len(XFAIL_RETRIEVAL)} כשלי אחזור ידועים (לא נספרים)")
    print("=" * 70)
    for role, question, expected in XFAIL_RETRIEVAL:
        try:
            chunks = retrieve_for_role(question, role)
            top_docs = []
            for c in chunks:
                if c["doc_id"] not in top_docs:
                    top_docs.append(c["doc_id"])
            ok = expected in top_docs[:TOP_K]
        except Exception:
            ok = False
        if ok:
            print(f"🎉 [{role}] {question} — עבר! אפשר להעביר ל-DIRTY")
        else:
            print(f"⚠ [{role}] {question} (עדיין נכשל, ציפינו {expected})")


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


def run_facts() -> int:
    """The answer-correctness gate: full pipeline per question, deterministic
    assertions on the answer text. Costs ~$0.10/question — run with --facts
    before shipping retrieval/prompt/model changes, not on every push."""
    from backend import get_ai_answer

    failures = 0
    print("=" * 70)
    print(f"נכונות תשובות (FACTS) — {len(FACTS)} שאלות (צינור מלא + עובדות-מפתח)")
    print("=" * 70)
    for role, question, expected_doc, fact_groups in FACTS:
        try:
            result = get_ai_answer(question, role=role)
        except Exception as e:
            print(f"✗ [{role}] {question}\n    !! שגיאה: {type(e).__name__}: {e}")
            failures += 1
            continue
        answer = result["text"]
        top_sources = [s["doc_id"] for s in result["sources"][:TOP_K]]
        problems = []
        # a BARE refusal is one that opens the answer (the shape rule 2
        # mandates). A nuanced answer that delivers the facts and only then
        # qualifies ("לחייל שאינו בודד — לא נקבע") must not fail here.
        if _MANDATED_REFUSAL in " ".join(answer.split())[:160]:
            problems.append("סירב למרות שהתשובה קיימת בפקודות")
        if expected_doc not in top_sources:
            problems.append(f"הפקודה הצפויה לא במקורות המובילים ({top_sources})")
        for group in fact_groups:
            if not any(alt in answer for alt in group):
                problems.append(f"חסרה עובדה: {' / '.join(group[:3])}")
        if problems:
            print(f"✗ [{role}] {question}")
            for p in problems:
                print(f"    {p}")
            snippet = " ".join(answer.split())[:200]
            print(f"    תשובה: {snippet}")
            failures += 1
        else:
            print(f"✓ [{role}] {question}")
    return failures


def run_observe() -> None:
    """Ambiguous real-world questions — full answers printed for a human read;
    no pass/fail (the right behaviour is a judgement call per rule 2)."""
    from backend import get_ai_answer

    for role, question in OBSERVE:
        print("=" * 70)
        print(f"OBSERVE [{role}] {question}")
        print("-" * 70)
        try:
            result = get_ai_answer(question, role=role)
            print(result["text"])
            print(f"\n(מקורות: {[s['doc_id'] for s in result['sources'][:4]]})")
        except Exception as e:
            print(f"!! שגיאה: {type(e).__name__}: {e}")
        print()


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

    # verdict-colour classifier (verdict.py) — the single source shared with
    # the share card; assert the colour rules the card relies on
    import verdict
    vc_cases = [
        ("**פסיקה:** מותר", ["yes"]),
        ("**פסיקה:** אסור בתנועה רגלית", ["no"]),
        ("**פסיקה:** מותר בתנאים מסוימים", ["cond"]),
        ("**פסיקה:** מותר או אסור — תלוי", ["cond"]),      # opposing term → conditional
        ("**פסיקה:** לא אסור", ["accent"]),                # double negative → neutral
        ("**פסיקה:** לא נמצא במאגר", ["none"]),
        ("**פסיקה:** אסור אם א-ג; מותר אם ד", ["no", "yes"]),  # compound split
        ("אין כאן פסיקה", []),
    ]
    vc_ok = all([c["cls"] for c in verdict.verdict_clauses(t)] == exp for t, exp in vc_cases)
    checks.append(("סיווג צבעי פסיקה (verdict.py)", vc_ok, ""))

    # clause-image pipeline (fitz render + highlight + crop): a real answer's
    # top source must produce non-trivial PNG bytes on its mapped page. Guards
    # the whole "הצג סעיף מקור" feature — missing fitz, a moved PDF, or a
    # render regression all fail here instead of only at click time.
    img_ok = False
    try:
        chunks = retrieve_for_role("כמה שעות שינה רצופות מגיעות לחייל", "soldier")
        src = backend._sources_from_chunks(chunks)[0]
        pg = backend.page_for_clause(src["doc_id"], src["clause"])
        png = backend.render_clause_image(src["source_file"], pg, src.get("highlight", ""))
        img_ok = isinstance(png, (bytes, bytearray)) and png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) > 5000
    except Exception:
        img_ok = False
    checks.append(("תצוגת סעיף: render מחזיר PNG תקין", img_ok, ""))

    # PWA icons committed to the repo — the head injection + manifest serve
    # them; a deleted/corrupt icon breaks Add-to-Home-Screen silently
    icons_ok = all(
        (p := Path(__file__).parent / "branding" / "icons" / f"icon-{n}.png").exists()
        and p.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
        for n in (180, 192, 512)
    )
    checks.append(("אייקוני PWA קיימים ותקינים", icons_ok, ""))

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
    run_xfail()
    if "--no-llm" not in sys.argv:
        failures += run_followup()
        failures += run_noscope()
        failures += run_smoke()
    if "--facts" in sys.argv:
        failures += run_facts()
        run_observe()

    print("=" * 70)
    if failures:
        print(f"נכשלו {failures} בדיקות")
        return 1
    print("כל הבדיקות עברו ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
