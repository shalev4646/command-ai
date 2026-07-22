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
   שגיאות כתיב קלות, שאלות קצרות ומעורפלות. רץ בלי LLM כמו סט הזהב —
   בודק מה האינדקס סופג גולמי, בלי נרמול.
2ב. שגיאות הקלדה (TYPOS) — טיפו כבד במסלול הייצור של שאלה ראשונה: נרמול
   Haiku ואז אחזור; + חוזה NOCHANGE — שאלות נקיות חוזרות מהנרמול כלשונן.
   קריאת Haiku לשאלה (זניח), לכן בשכבת ה-LLM ולא ב---no-llm.
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
    ("soldier",   "איך אני יכול להוריד פרופיל רפואי?", ("32.0402", "32.0401")),
    ("commander", "מה הנוהל כשנראה שמצב הבריאות של חייל שלי השתנה?", "32.0402"),
    ("soldier",   "הייתי מעורב בתאונת דרכים עם רכב צבאי — מי מוסמך להתלות את הרישיון הצבאי שלי?", "33.1104"),
    ("soldier",   "אילו זכויות מגיעות לחיילת בהריון במהלך השירות?", "36.0406"),
    ("soldier",   "האם מגיעה היעדרות מהשירות לצורך טיפולי פוריות?", "36.0406"),
    ("soldier",   "לא סיימתי בית ספר יסודי — האם הצבא חייב להשלים לי השכלה בזמן השירות?", "37.0102"),
    ("commander", "מתי אפשר לשלול מחייל משוחרר את מענק השחרור והפיקדון שלו?", "35.0234"),
    ("commander", "איזה מענק מקבל משרת קבע בסיום שירותו ומה נחשב לשירות תקין לחישובו?", "20.0502"),
    ("soldier",   "איזו דרגת רישיון צבאי צריך כדי לנהוג ברכב משא כבד?", "58.0202"),
    # batch 7 (2026-07-20): 24 orders ingested from the site's canonical HTML
    # (ingestion/html_ingest.py) — one golden question per order, soldier
    # daily-life first, then reserve and career money
    ("soldier",   "אני שוכר דירה במהלך השירות — הצבא משתתף בשכר הדירה שלי?", "35.0307"),
    ("soldier",   "המצב הכלכלי בבית קשה — איך מבקשים הקלות בתנאי השירות?", "35.0807"),
    ("soldier",   "התחתנתי במהלך השירות — מגיע לי מענק מהצבא?", "35.0805"),
    ("soldier",   "אפשר לקבל הלוואה מהצבא כשאני בשירות חובה?", "35.0803"),
    ("soldier",   "אילו תשלומים מגיעים לי מהצבא כשאני משתחרר משירות חובה?", "35.0205"),
    ("soldier",   "מתי מגיעים לי דמי כלכלה מהצבא?", "56.0131"),
    ("soldier",   "האם נסיעה באוטובוס חינם לחיילים במדים?", "33.0120"),
    ("soldier",   "מותר לי להשתמש בטלפון האישי שלי בתוך הבסיס?", "21.0113"),
    ("soldier",   "מותר לי לצלם תמונות בתוך הבסיס?", "21.0210"),
    ("soldier",   "איך מגישים בקשה לעבור תפקיד או יחידה?", "31.0308"),
    ("soldier",   "אפשר לבקש שיבוץ קרוב לבית מסיבה נפשית?", "31.0116"),
    ("soldier",   "מה זה תרגול נוסף ומי מוסמך להטיל אותו עליי?", "33.0351"),
    ("soldier",   "האם המפקד שלי יכול לראות מידע מהתיק הרפואי שלי?", "61.0113"),
    ("soldier",   "משתחרר מהצבא על סעיף רפואי — איך מגישים בקשה להכרה בנכות?", "38.0122"),
    ("soldier",   "מי קובע את הפרופיל הרפואי שלי ובאיזה הליך?", ("32.0401", "32.0402")),
    ("soldier",   "חיילת שמתחתנת — יכולה להשתחרר משירות חובה?", "31.0109"),
    ("soldier",   "האם הרשעה בדין משמעתי בצבא נרשמת במרשם הפלילי?", "33.0146"),
    ("soldier",   "אילו תעודות צבאיות מנפיקים לחייל ולמה הן משמשות?", "30.0106"),
    ("reserve",   "אילו תשלומים מגיעים לי על שירות מילואים?", "35.0206"),
    ("reserve",   "מה זה תגמול מיוחד למילואים ומי זכאי לו?", "35.0209"),
    ("reserve",   "גויסתי בצו 8 ויש לי נסיבות אישיות קשות — למי פונים לתיאום השירות?", "31.0605"),
    ("commander", "איך עובד הסדר הפנסיה למשרתי הקבע החדשים?", "36.0106"),
    ("commander", "אני בקבע ושוכר דירה — מגיעה לי השתתפות בשכר הדירה?", "36.0513"),
    ("commander", "חייל שהיה במעצר וזוכה בדין — מקבל חזרה את השכר שנוכה לו?", "35.0227"),
    # batch 10 (2026-07-22): demand-gap additions — the deposit/grant law
    # (civil source, the most-asked discharge question), public-inquiries
    # response duty, fine/debt collection, private-equipment compensation
    ("soldier",   "כמה כסף מקבלים מהצבא אחרי השחרור ומה זה הפיקדון?", "חוק-קליטת-חיילים"),
    ("soldier",   "אפשר להשתמש בפיקדון של הצבא לרישיון נהיגה?", "חוק-קליטת-חיילים"),
    ("soldier",   "שלחתי פנייה בכתב לגורם בצבא ואף אחד לא עונה — מה עושים?", "8.0101"),
    ("soldier",   "איך גובים ממני קנס שקיבלתי בדין משמעתי?", "35.0221"),
    ("soldier",   "הציוד הפרטי שלי ניזוק במהלך פעילות — הצבא מפצה על זה?", "35.0223"),
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
    # vague money question — three orders answer it legitimately (תגמול נוסף,
    # תשלומי מילואים, תגמול מיוחד); widened to a tuple when the 2026-07-22
    # money-vocabulary docs (חוק הפיקדון, 35.0221) shifted the ranking
    ("reserve",   "כמה כסף מקבלים על מילואים חוץ מהמשכורת?", ("013.3", "35.0206", "35.0209")),
    # batch 6 (2026-07-20): slang phrasings for the newest orders
    ("soldier",   "אפשר להוריד פרופיל בצבא?", "32.0402"),
    ("soldier",   "עשיתי תאונה עם רכב צבאי, יקחו לי את הרישיון?", "33.1104"),
    ("soldier",   "חיילת בהריון, מה מגיע לה?", "36.0406"),
    ("soldier",   "לא סיימתי בית ספר, הצבא נותן להשלים לימודים?", "37.0102"),
    # batch 7 (2026-07-20): the HTML-ingested daily-life orders
    ("soldier",   "יש חינם באוטובוסים לחיילים?", "33.0120"),
    ("soldier",   "מותר פלאפון בבסיס?", "21.0113"),
    ("soldier",   "בא לי לעבור יחידה, איך עושים את זה?", "31.0308"),
]

# (role, question, expected_doc_id) — heavy-typo questions that go through the
# PRODUCTION first-question path: Haiku normalization, then retrieval. DIRTY
# above tests what the index absorbs raw; this layer tests what normalization
# must rescue. Runs in the LLM section (a Haiku call per question, ~nothing).
TYPOS = [
    # the 2026-07-10 pilot question that was refused live and stayed broken
    # until normalization existed ("חפשש", "להתשחרר")
    ("soldier", "כמה ימי חפשש מגיע לי אם אני אמור להתשחרר בקרוב?", "PM-35.0402"),
    ("soldier", "כמה ימי חפשה שנתית מגיעים לחיל סדיר?", "PM-35.0402"),
    # real pilot typo "טלווזיה" (the club order must lead even typo'd)
    ("soldier", "מותר לראות טלווזיה במועדון ביחידה?", "35.0818"),
    # real pilot phrasing "עליתי משמשרת" (משמרת)
    ("soldier", "אם עליתי משמשרת בלילה עד מתי מותר לי לישון?", "PM-33.0213"),
]

# (role, question) — CLEAN questions that must come back from the normalizer
# byte-identical. This is the always-on rewrite's safety contract: it repairs
# typos and touches nothing else, so every retrieval result the golden/dirty
# sets certified stays valid in production. Mirrors entries from GOLDEN/DIRTY/
# FACTS (kept verbatim copies — drift here is harmless, they're just clean
# questions).
NOCHANGE = [
    ("soldier",   "כמה ימי מחבוש אפשר להטיל על חייל בדין משמעתי?"),
    ("commander", "באילו תנאים מותר למנוע חופשה מחייל?"),
    ("reserve",   "האם חייל מילואים צריך אישור כדי לצאת לחוץ לארץ?"),
    ("soldier",   "כמה ימי חופש מגיעים לסדירניק בשנה?"),
    ("soldier",   "המפקד דופק אותי כל הזמן, למי אפשר להתלונן עליו?"),
    ("soldier",   "איזו עזרה בדיור מגיעה לי כחייל בודד?"),
]

# (role, question) — questions whose answer is NOT in any ingested order
# (civil law, courses, equipment charging). The pipeline still retrieves the
# nearest chunks, so these are hallucination bait: the pass condition is an
# honest refusal, not an answer. The medical-profile question that used to sit
# here moved to GOLDEN when 32.0402 (שינוי כושר בריאותי) was ingested.
NOSCOPE = [
    ("soldier",   "מה תנאי הקבלה לקורס טיס?"),
    ("soldier",   "אילו הטבות מגיעות לחייל משוחרר בלימודים אקדמיים?"),
    ("reserve",   "כמה ימי מילואים מותר לקרוא לי בשנה לפי החוק?"),
    # retired to OBSERVE after the 2026-07-21 batch-7 expansion: the pikadon
    # question (35.0234/35.0205 now state timings and purposes) and the
    # equipment-charge question (31.0103 carries the forms procedure) both
    # have legitimate partial answers now — a cited rule-2 answer is correct
    # behaviour there, which this layer would miscount as fabrication.
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
    # moved from NOSCOPE — the profile twins both answer: 32.0402 is the
    # change procedure, 32.0401 the determination/authority order
    ("soldier", "איך אני יכול להוריד פרופיל רפואי?", ("32.0402", "32.0401"),
     [["ועדה רפואית"], ["32.0402", "32.0401", "כושר בריאותי"]]),
    ("soldier", "הייתי מעורב בתאונה עם רכב צבאי — באילו תנאים יתלו לי את הרישיון הצבאי?", "33.1104",
     [["יסוד להאשים", "עבירה", "ממצ\"פ", "שיטור"], ["33.1104", "התליית", "להתלות"]]),
    ("commander", "מתי אפשר לשלול מחייל משוחרר את מענק השחרור והפיקדון?", "35.0234",
     [["אי-התאמה", "אי התאמה", "הרשעה", "עבירה"], ["35.0234", "שלילת"]]),
    ("soldier", "כמה ימי מחבוש מקסימום אפשר לקבל בדין משמעתי?", "PM-33.0302",
     [["30"], ["33.0302", "דין משמעתי"]]),
    ("soldier", "חיילת בהריון — אילו הקלות והתאמות מגיעות לה בשירות?", "36.0406",
     [["36.0406", "הורות"]]),
    # batch 7 (2026-07-21): answer-correctness for the new daily-life orders,
    # grounded in the clean site-HTML texts (no key-facts needed — the text
    # isn't mangled; these guard the facts staying retrievable end-to-end)
    ("soldier", "ביקשתי לעבור תפקיד — תוך כמה זמן חייבים לענות לי ולאן פונים אם לא ענו?", "31.0308",
     [["ארבעים", "40"], ["בקו\"ם"], ["31.0308", "שינוי שיבוץ"]]),
    ("soldier", "מתי מגיעים לי דמי כלכלה מהצבא?", "56.0131",
     [["מטבח", "הזנה", "משמרות"]]),
    ("soldier", "מותר לי להשתמש בטלפון האישי שלי בבסיס?", "21.0113",
     [["בלמ\"ס", "מסווג", "שמור"]]),
    ("commander", "התחלתי קבע ב-2020 — איזה הסדר פנסיה חל עליי?", "36.0106",
     [["צוברת"], ["36.0106", "פנסי"]]),
    ("soldier", "אני חייל נשוי שגר בשכירות — הצבא משתתף לי בשכר הדירה?", "35.0307",
     [["חוזה שכירות", "אחזקת דירה"], ["35.0307", "השתתפות בשכ"]]),
    ("soldier", "המפקד שלי דורש לדעת מה יש לי בתיק הרפואי — הוא יכול לקבל את המידע בלי אישור שלי?", "61.0113",
     [["הסכמ"], ["61.0113", "חסיון"]]),
    ("soldier", "אני נוסע באוטובוס הביתה מהבסיס — צריך לשלם?", "33.0120",
     [["חינם"], ["33.0120", "תחבורה ציבורית"]]),
    ("soldier", "התחתנתי במהלך השירות — מגיע לי מענק מהצבא ומה גובהו?", "35.0805",
     [["טוראי"], ["35.0805", "מענק נישואין"]]),
    ("commander", "מתי מותר לבצע תרגול נוסף ומי רשאי לאשר אותו שלא ביום גילוי הליקוי?", "33.0351",
     [["סגן"], ["ביום שבו נתגלה", "שעות הפעילות"]]),
    ("reserve", "עשיתי שבוע מילואים — מאיפה מגיע לי התגמול על ימי העבודה שהפסדתי?", "35.0206",
     [["ביטוח לאומי", "המוסד לביטוח"], ["35.0206", "תשלומים"]]),
    # batch 8 (2026-07-22): the last 13 daily-life orders without answer-
    # correctness coverage; facts grounded in the clean site-HTML texts
    ("soldier", "נשלחתי לבקו\"ם להשתחרר משירות חובה — אילו תשלומים מגיעים לי ביום השחרור?", "35.0205",
     [["נסיעה"], ["כלכלה"], ["35.0205", "חיילים משתחררים"]]),
    ("soldier", "אני רוצה לצלם בתוך הבסיס — ממי צריך לקבל היתר צילום?", "21.0210",
     [["מפקד המתקן", "מפקד מתקן", "מפקד הבסיס"], ["קב\"ם", "ביטחון מידע", "ביטחון המידע"], ["21.0210", "במחנות"]]),
    # civilian venues asking for the army ID as a deposit — clause 6 forbids it
    ("soldier", "במועדון ביקשו שאשאיר את תעודת החוגר כערבון בכניסה — מותר לי להשאיר אותה?", "30.0106",
     [["אסור", "אין למסור", "לא ימסור", "לא למסור"], ["30.0106", "תעודות צבאיות"]]),
    ("soldier", "אני חיילת שמתחתנת בקרוב — מתי מודיעים למפקד ומתי משתחררים משירות חובה?", "31.0109",
     [["עשרים ואחד", "21 ימים", "21 יום"], ["עשרה ימים", "10 ימים"], ["31.0109"]]),
    ("soldier", "אושר לי שיבוץ קרוב לבית מסיבה נפשית — לאילו הקלות אני זכאי בפועל?", "31.0116",
     [["שישים", "60"], ["שלוש"], ["31.0116"]]),
    # the family petitions the ולת"ם at the city-officer's office, not the unit
    ("reserve", "אני במילואים בחירום והמשפחה בבית במצוקה — לאן המשפחה יכולה לפנות כדי לבקש לשחרר אותי?", "31.0605",
     [["ולת\"מ", "ולת\"ם", "ועדה לתיאום", "הוועדה לתיאום"], ["קצין העיר"]]),
    # clause 26: closed police files reach the MP and מחב"ם only
    ("soldier", "יש לי תיק סגור במשטרה — מי בצבא בכלל יכול לקבל מידע על זה?", "33.0146",
     [["המשטרה הצבאית", "משטרה צבאית", "מצ\"ח"], ["מחב\"ם", "ביטחון מידע", "ביטחון המידע"]]),
    ("reserve", "לא שולם לי התגמול המיוחד למילואים — עד מתי אפשר להגיש השגה על חישוב ימי השירות?", "35.0209",
     [["שלושה חודשים", "3 חודשים"], ["מוקד"]]),
    # pre-trial arrest: 50% pay, refunded in arrears on acquittal
    ("soldier", "אני במעצר עד סוף המשפט — ממשיכים לשלם לי משכורת בינתיים?", "35.0227",
     [["50", "חמישים", "מחצית"], ["יוחזרו", "יוחזר", "זוכה", "זיכוי", "הפרשי"]]),
    ("soldier", "אני בקשיים כלכליים — אפשר לקבל הלוואה או מענק מהצבא, ובאיזה גובה?", "35.0803",
     [["טוראי"], ["קצין הת\"ש", "הת\"ש"]]),
    # anchor n-gram kept verbatim — free-form rewordings drift off 35.0807
    ("soldier", "איך מבקשים הקלות בתנאי השירות ותוך כמה זמן חייבים להחליט בבקשה?", "35.0807",
     [["24"], ["35.0807"]]),
    # קבע rent aid: 7 cumulative years, tapering 100/75/50
    ("commander", "אני משרת קבע נשוי וגר בשכירות ליד הבסיס — כמה שנים הצבא משתתף לי בשכר הדירה?", "36.0513",
     [["שבע", "7 שנים"], ["75%", "50%"], ["36.0513"]]),
    ("soldier", "חליתי במהלך השירות והשתחררתי בגלל זה — עד מתי אפשר להגיש בקשה להכרה בנכות?", "38.0122",
     [["שלוש שנים", "3 שנים"], ["אגף השיקום", "משהב\"ט", "משרד הביטחון"]]),
    # batch 9 (2026-07-22): the 4 orders refreshed from the portal in a406f4d
    # got new texts but had no answer-correctness coverage at all
    ("soldier", "מסוכן באזור המגורים שלי — מי מוסמך לאשר לי לשאת נשק הביתה ולכמה זמן האישור תקף?", "2.0101",
     [["אל\"ם"], ["חצי שנה", "שישה חודשים", "6 חודשים"], ["2.0101"]]),
    ("soldier", "יום כיף יחידתי יורד לי מימי החופשה השנתית?", "05.104",
     [["ימי פעילות", "לא יימנו", "לא יורד", "אינם יורדים", "לא נמנים"], ["05.104", "35.0408", "גיבוש"]]),
    ("soldier", "רותקתי לבסיס ואני החיילת היחידה ביחידה — מותר להשאיר אותי ללון שם לבד?", "PM-33.0207",
     [["שתי חיילות"], ["רע\"ן הפרט", "אישור חריג"], ["33.0207", "השירות המשותף"]]),
    # clause 45(b): three traffic convictions within two years = mandatory
    # six-month revocation of the military licence
    ("soldier", "צברתי שלוש הרשעות על עבירות תנועה ברכב צבאי בתוך שנתיים — מה יקרה לרישיון הצבאי שלי?", "58.0202",
     [["שישה חודשים", "6 חודשים", "חצי שנה"], ["58.0202", "רישיון נהיגה"]]),
    # batch 10 (2026-07-22): the demand-gap additions — deposit/grant law
    # (rates are May-2026, indexed monthly; the numeric asserts pin the rate
    # PREFIXES so a monthly index update doesn't break the eval), public
    # inquiries, fine collection, equipment compensation, sick-days (ג')
    ("soldier", "מה גובה הפיקדון והמענק שמקבלים אחרי השחרור, ומתי הכסף נכנס לחשבון?", "חוק-קליטת-חיילים",
     [["990"], ["684", "685"], ["60"], ["חוק", "מקור אזרחי"]]),
    ("soldier", "שלחתי פנייה בכתב לגורם בצבא ולא ענו לי — תוך כמה זמן חייבים להשיב?", "8.0101",
     [["45"], ["8.0101", "והנמקה"]]),
    ("soldier", "קיבלתי קנס בדין משמעתי — כמה מותר להוריד לי מהמשכורת כל חודש?", "35.0221",
     [["50"], ["35.0221", "הפקעת משכורת", "גביית קנסות"]]),
    # moved from OBSERVE: was a corpus gap, now 35.0221 covers the collection
    # route (clause 41: shortage-in-equipment payment deducted from pay)
    ("commander", "מה הנוהל לחיוב חייל על אובדן ציוד צבאי?", "35.0221",
     [["ינוכה", "ניכוי", "מהשכר", "מן השכר"], ["35.0221", "52.0301"]]),
    ("soldier", "הטלפון הפרטי שלי נשבר במהלך אימון — אפשר לקבל פיצוי מהצבא?", "35.0223",
     [["לפנים משורת הדין", "הצהרה", "30"], ["35.0223", "ציוד אזרחי", "ציוד פרטי"]]),
    # the real-user gimel question (metrics 2026-07-11): a ג' day exists only
    # by a doctor's determination — the refreshed clean 61.0104 must say so
    ("soldier", "אני מרגיש חולה — מותר לי להישאר בבית בגימלים בלי אישור מרופא?", "61.0104",
     [["רופא"], ["מנוחה", "אינו כשיר", "בלתי כשיר", "גורם מוסמך"], ["61.0104", "טיפול רפואי"]]),
]

# שאלות אמת עמומות — נדפסות לקריאה ידנית בלבד (אין להן pass/fail חד):
# חלקן פער-קורפוס אמיתי, חלקן תשובה חלקית לגיטימית (כלל 2 בפרומפט).
# (2026-07-22: שלוש שאלות עברו ל-FACTS אחרי שנסגר הפער — פיקדון/מענק ל"חוק-
# קליטת-חיילים", נשק-הביתה ל-2.0101, חיוב-על-אובדן-ציוד ל-35.0221.)
OBSERVE = [
    # 8.0101 covers the 45-day duty; whether the commander gets updated is a
    # nuance the order doesn't state — keep as a human-judgement read
    ("soldier", "פניתי לפניות הציבור בגלל מצוקה שקשורה ליחס המפקד — הם חייבים לעדכן את המפקד?"),
    # pilot, downvoted: club hours are unit-set (35.0818), Shabbat rules in PM-34.0101
    ("soldier", "האם מותר לראות טלוויזיה במועדון בשבת?"),
]

TOP_K = 3


def _run_retrieval_set(name: str, cases: list) -> int:
    failures = 0
    print("=" * 70)
    print(f"{name} — {len(cases)} שאלות אחזור (הפקודה הנכונה בטופ-{TOP_K})")
    print("=" * 70)
    for role, question, expected in cases:
        # `expected` is one doc id, or a tuple when sibling orders both answer
        # the question legitimately (e.g. 32.0401 קביעה / 32.0402 שינוי for
        # "להוריד פרופיל") — any of them in the top-K passes
        accepted = expected if isinstance(expected, tuple) else (expected,)
        try:
            chunks = retrieve_for_role(question, role)
            top_docs = []
            for c in chunks:  # distinct docs, in rank order
                if c["doc_id"] not in top_docs:
                    top_docs.append(c["doc_id"])
            ok = any(e in top_docs[:TOP_K] for e in accepted)
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


def run_typos() -> int:
    """The first-question production path on heavy typos: Haiku normalization
    then retrieval, plus the NOCHANGE identity contract on clean questions."""
    from backend import _standalone_question

    failures = 0
    print("=" * 70)
    print(f"שגיאות הקלדה — {len(TYPOS)} שאלות (נרמול + אחזור בטופ-{TOP_K})")
    print("=" * 70)
    for role, question, expected in TYPOS:
        try:
            rewritten = _standalone_question(question, None)
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

    print("-" * 70)
    print(f"NOCHANGE — {len(NOCHANGE)} שאלות נקיות (חייבות לחזור כלשונן)")
    for role, question in NOCHANGE:
        try:
            rewritten = _standalone_question(question, None)
            ok = rewritten == question
        except Exception as e:
            print(f"✗ [{role}] {question}\n    !! שגיאה: {type(e).__name__}: {e}")
            failures += 1
            continue
        if ok:
            print(f"✓ [{role}] {question}")
        else:
            print(f"✗ [{role}] {question}")
            print(f"    הנרמול שינה שאלה נקייה ל: {rewritten}")
            failures += 1
    return failures


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
        # tuple = sibling orders that both answer legitimately (same contract
        # as the retrieval sets)
        accepted = expected_doc if isinstance(expected_doc, tuple) else (expected_doc,)
        problems = []
        # a BARE refusal is one that opens the answer (the shape rule 2
        # mandates). A nuanced answer that delivers the facts and only then
        # qualifies ("לחייל שאינו בודד — לא נקבע") must not fail here.
        if _MANDATED_REFUSAL in " ".join(answer.split())[:160]:
            problems.append("סירב למרות שהתשובה קיימת בפקודות")
        if not any(e in top_sources for e in accepted):
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
    if "--no-llm" not in sys.argv:
        failures += run_typos()
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
