# -*- coding: utf-8 -*-
"""מחשבון זכאויות — ערכי זכאות מדויקים, מעוגנים בפקודות מטכ"ל.

הכלי מציג ערכים דטרמיניסטיים (ימי חופשה, אחוזי תשלום למשפחה, מבנה דמי הקיום)
ישירות מלשון הפקודה, יחד עם ציטוט הסעיף/הנספח שממנו נלקח כל ערך.

הכוונה כללית בלבד — אינו ייעוץ ואינו מחליף את הפקודה המחייבת. בכל סתירה,
נוסח הפקודה הרשמי הוא הקובע. ערך שהפקודה אינה נוקבת בו במספר (סכום הנקבע
בתקנות/בידי הרמטכ"ל, טבלת אחוזים או נוסחה) מוצג כמבנה — לא כסכום שקלים מומצא.

Pure, curated data + lookup helpers — ZERO LLM tokens, no Anthropic call ever.
Every figure below was read from the order text in storage/json_store AND
verified against the source PDF in pdf-ldf_law:
  * PM-35.0402 leave appendix — release-leave 7/10/14 and annual 18 confirmed
    on the PDF's leave table (נספח החופשות).
  * 35.0210 family-payment percentage table (100/80/90/95/100/80/85/90) and the
    "basic wage = 50% of the average wage" / 120% ceiling, confirmed on the PDF.
  * 35.0201 subsistence — the order fixes NO shekel figure; the amount is "set
    by the Chief of Staff with the Defense Minister's approval" (סעיף 1) and
    CPI-updated, so only the component STRUCTURE is surfaced.
This file is intentionally hand-curated and NOT derived from the live vector
index, so a re-ingestion cannot silently change a cited number. If the orders
are re-ingested and a value moves, rebuild this file by hand.
"""

from __future__ import annotations

# ── Shown under every result. Not a value — a standing caveat. ──
DISCLAIMER = (
    "הכוונה כללית בלבד, אינה ייעוץ ואינה מחליפה את הפקודה המחייבת. "
    "בכל סתירה — נוסח הפקודה הרשמי הוא הקובע."
)

# Short order labels reused in every citation ("לפי פ\"מ …, …").
_DOC_LEAVE = 'פ"מ 35.0402'          # "חופשות לחיילים המשרתים בשירות חובה"
_DOC_SUBSIST = 'פ"מ 35.0201'        # "דמי קיום לחיילים בשירות חובה"
_DOC_FAMILY = 'פ"מ 35.0210'         # "חוקת התשלומים למשפחות חיילים בשירות חובה"


def cite(doc: str, clause: str) -> str:
    """The mandated citation line, e.g. 'לפי פ\"מ 35.0402, נספח החופשות'."""
    return f"לפי {doc}, {clause}"


# ─────────────────────────────────────────────────────────────────────────────
# Calculator A — ימי חופשה (leave days), from PM-35.0402.
#
# Every day-count below is quoted verbatim from the order's appendix leave table
# ("נספח החופשות") unless a body section is named. `days` is a STRING on purpose:
# some rows are "24 שעות" or "על חשבון החופשה השנתית", not an integer of days —
# never coerce these into a made-up number.
# ─────────────────────────────────────────────────────────────────────────────

# category_key -> {title, pick_label, cases:[{label, days, approver, account,
#   clause, note}]}. A category with one case renders its result directly; a
# multi-case category (release / family / personal) shows a second picker.
LEAVE_CATEGORIES: dict[str, dict] = {
    "release": {
        "title": "חופשת שחרור",
        "pick_label": "מסלול השירות / תעודת לוחם",
        "cases": [
            {
                "label": "רמ\"פ א' ומעלה (ללא תעודת לוחם)",
                "days": "7 ימים",
                "approver": "מפקד",
                "account": "על חשבון המערכת",
                "clause": "הנספח — שורת \"חופשת שחרור\"",
                "note": "כולל שישי ושבת. ניצול חופשת השחרור נעשה לאחר מיצוי מלא "
                        "של יתרת ימי החופשה השנתית.",
            },
            {
                "label": "רמ\"פ א' ומעלה + זכאי לתעודת לוחם",
                "days": "10 ימים",
                "approver": "מפקד",
                "account": "על חשבון המערכת",
                "clause": "הנספח — שורת \"חופשת שחרור\"",
                "note": "כולל שישי ושבת. תעודת לוחם כהגדרתה בהק\"א אכ\"א 01-07-30.",
            },
            {
                "label": "רמ\"פ א' ומעלה + זכאי לתעודת לוחם חוד",
                "days": "14 ימים",
                "approver": "מפקד",
                "account": "על חשבון המערכת",
                "clause": "הנספח — שורת \"חופשת שחרור\"",
                "note": "כולל שישי ושבת. תעודת לוחם חוד בהתאם להגדרות אכ\"א-חתומכ\"א.",
            },
            {
                "label": "רמ\"פ ב' ומטה",
                "days": "אין מכסת שחרור ייעודית",
                "approver": "מפקד",
                "account": "על חשבון החופשה השנתית",
                "clause": "הנספח — שורת \"חופשת שחרור\"",
                "note": "החייל רשאי לנצל את יתרת ימי החופשה השנתית שלו לפני שחרורו "
                        "כחופשת שחרור.",
            },
        ],
    },
    "annual": {
        "title": "חופשה שנתית",
        "cases": [
            {
                "label": "חופשה שנתית — הוראות כלליות",
                "days": "18 ימים",
                "approver": "מפקד",
                "account": "חופשה שנתית",
                "clause": "סעיף 11 והנספח — שורת \"חופשה שנתית\"",
                "note": "שבתות, מועדי ישראל, חגי מדינה וימי שישי אינם נמנים במניין "
                        "המכסה (סעיף 12). לחלקי שנה — חישוב יחסי (סעיף 29).",
            },
        ],
    },
    "command_days": {
        "title": "ימי מפקד",
        "cases": [
            {
                "label": "ימי מפקד (לפי שיקול דעת המפקד)",
                "days": "5 ימים",
                "approver": "מפקד או על-פי הנחיית רמ\"ט אכ\"א",
                "account": "חופשה שנתית",
                "clause": "סעיף 11 והנספח — שורת \"חופשת מפקד\"",
                "note": "ימים אלה אינם ניתנים לצבירה משנה לשנה.",
            },
        ],
    },
    "basic_training": {
        "title": "חופשה בהכשרה ראשונית",
        "cases": [
            {
                "label": "ימי חופשה בהכשרה ראשונית",
                "days": "עד 5 ימים",
                "approver": "מפקד בדרגת סא\"ל לפחות",
                "account": "על חשבון המערכת",
                "clause": "הנספח — שורת \"חופשה בהכשרה ראשונית\"",
                "note": "ניתנים בששת החודשים הראשונים לשירות; אינם ניתנים לצבירה.",
            },
        ],
    },
    "line": {
        "title": "חופשת קו (תעסוקה מבצעית)",
        "cases": [
            {
                "label": "חופשת קו לחייל לוחם בתעסוקה מבצעית",
                "days": "עד 4 ימים רצופים",
                "approver": "מפקד יחידה",
                "account": "על חשבון המערכת",
                "clause": "הנספח — שורת \"חופשת קו\"",
                "note": "הימים אינם כוללים את יום היציאה לחופשה ואת יום החזרה ממנה.",
            },
        ],
    },
    "family": {
        "title": "חופשה מיוחדת משפחתית",
        "pick_label": "עילת החופשה המשפחתית",
        # Mandated body context: special leave is governed by sections 13-15; the
        # per-event day-counts live in the appendix "חופשה מיוחדת משפחתית" block.
        # 58 days is the OVERALL family-leave ceiling the events draw from.
        "cases": [
            {
                "label": "מכסה כוללת (חופשה מיוחדת משפחתית)",
                "days": "58 ימים",
                "approver": "מפקד בדרגת רס\"ן לפחות",
                "account": "חופשה מיוחדת משפחתית",
                "clause": "סעיפים 13–15 והנספח — כותרת \"חופשה מיוחדת משפחתית\"",
                "note": "זו המכסה הכוללת שממנה נגזרות העילות הפרטניות שלהלן.",
            },
            {
                "label": "נישואין",
                "days": "10 ימים",
                "approver": "מפקד בדרגת רס\"ן לפחות",
                "account": "חופשה מיוחדת משפחתית",
                "clause": "הנספח — \"חופשה מיוחדת משפחתית / נישואין\"",
                "note": "בחירום 5 ימים. ניתן לנצל מחודש לפני מועד הנישואין ועד חודש "
                        "אחריו; יום הנישואין נמנה במניין.",
            },
            {
                "label": "לידה של בת זוג",
                "days": "8 ימים",
                "approver": "מפקד בדרגת רס\"ן לפחות",
                "account": "חופשה מיוחדת משפחתית",
                "clause": "הנספח — \"חופשה מיוחדת משפחתית / לידה של בת זוג\"",
                "note": "בחירום 5 ימים. ניתן לנצל מחודש לפני הלידה ועד חודש אחריה.",
            },
            {
                "label": "אימוץ ילד",
                "days": "8 ימים",
                "approver": "מפקד בדרגת רס\"ן לפחות",
                "account": "חופשה מיוחדת משפחתית",
                "clause": "הנספח — \"חופשה מיוחדת משפחתית / אימוץ ילד\"",
                "note": "בחירום 5 ימים. ילד שגילו עד 10 שנים ובכפוף לצו בית משפט. אם שני "
                        "ההורים המאמצים חיילים — רק אחד מהם מנצל.",
            },
            {
                "label": "היריון של בת זוג (טיפולים/בדיקות)",
                "days": "3 ימים",
                "approver": "בהתאם לכללי חופשה שנתית",
                "account": "חופשה שנתית",
                "clause": "הנספח — \"חופשה מיוחדת משפחתית / היריון של בת זוג\"",
                "note": "הבקשה תוגש בליווי הצהרה הנתמכת באישורים רפואיים.",
            },
            {
                "label": "טיפולי פוריות",
                "days": "16 ימים",
                "approver": "מפקד בדרגת רס\"ן לפחות",
                "account": "חופשה מיוחדת משפחתית",
                "clause": "הנספח — \"חופשה מיוחדת משפחתית / טיפולי פוריות\"",
                "note": "בליווי הצהרה הנתמכת באישור המוסד הרפואי המבצע.",
            },
            {
                "label": "ליווי בן/בת זוג לטיפולי פוריות",
                "days": "5 ימים",
                "approver": "מפקד בדרגת רס\"ן לפחות",
                "account": "חופשה מיוחדת משפחתית",
                "clause": "הנספח — \"חופשה מיוחדת משפחתית / ליווי לטיפולי פוריות\"",
                "note": "",
            },
            {
                "label": "מצב רפואי של ילד",
                "days": "8 ימים",
                "approver": "מפקד בדרגת רס\"ן לפחות",
                "account": "חופשה מיוחדת משפחתית",
                "clause": "הנספח — \"חופשה מיוחדת משפחתית / מצב רפואי של ילד\"",
                "note": "מחלה ממארת של ילד הנמצא בחזקת החייל — עד 90 ימים. נדרשים "
                        "אישורים רפואיים מתאימים.",
            },
            {
                "label": "מצב רפואי של בן/בת זוג",
                "days": "6 ימים",
                "approver": "מפקד בדרגת רס\"ן לפחות",
                "account": "חופשה מיוחדת משפחתית",
                "clause": "הנספח — \"חופשה מיוחדת משפחתית / מצב רפואי של בן זוג\"",
                "note": "כהגדרת \"בן זוג חולה\" בחוק דמי מחלה; בליווי הצהרה ואישור רפואי.",
            },
            {
                "label": "מצב רפואי של הורה",
                "days": "6 ימים",
                "approver": "מפקד בדרגת רס\"ן לפחות",
                "account": "חופשה מיוחדת משפחתית",
                "clause": "הנספח — \"חופשה מיוחדת משפחתית / מצב רפואי של הורה\"",
                "note": "בכפוף להצהרה שאחֵי המשרת לא מימשו זכאות זו ושההורה אינו במוסד "
                        "סיעודי; בליווי אישור רפואי.",
            },
            {
                "label": "פציעה/מחלה/אשפוז של קרוב מדרגה ראשונה",
                "days": "28 ימים (בפעימות של 7 ימים)",
                "approver": "מפקד בדרגת סא\"ל לפחות (במערך הלוחם — סרן לפחות)",
                "account": "חופשה מיוחדת משפחתית",
                "clause": "הנספח — \"חופשה מיוחדת משפחתית / טעמים רפואיים\"",
                "note": "ניתנת כשיש צורך ממשי בהימצאות החייל בקרבת קרובו. הארכה נוספת "
                        "עד 30 ימים באישור מפקד בדרגת אל\"ם לפחות.",
            },
            {
                "label": "שמחה משפחתית",
                "days": "24 שעות",
                "approver": "מפקד",
                "account": "חופשה שנתית (או משפחתית אם נגמרה השנתית)",
                "clause": "הנספח — \"חופשה מיוחדת משפחתית / שמחה משפחתית\"",
                "note": "נישואין/ברית/זבד הבת/בר או בת מצווה של קרוב מדרגה ראשונה או "
                        "אחיין. זמני הנסיעה אינם נמנים.",
            },
            {
                "label": "יום הזיכרון — בן למשפחה שכולה",
                "days": "יום חופשה ביום הזיכרון",
                "approver": "מפקד",
                "account": "חופשה מיוחדת משפחתית",
                "clause": "הנספח — \"אירועי יום הזיכרון\"",
                "note": "שחרור מהיחידה בזמן המאפשר הגעה הביתה כשעתיים לפני תחילת הטקסים.",
            },
        ],
    },
    "personal": {
        "title": "חופשה מיוחדת אישית",
        "pick_label": "עילת החופשה האישית",
        "cases": [
            {
                "label": "מכסה כוללת (כלכלית/סוציאלית/טעמים אישיים)",
                "days": "30 ימים",
                "approver": "מפקד בדרגת רס\"ן לפחות",
                "account": "חופשה מיוחדת אישית",
                "clause": "סעיפים 13–15 והנספח — כותרת \"חופשה מיוחדת אישית\"",
                "note": "חלה על חופשה מיוחדת כלכלית / סוציאלית / טעמים אישיים אחרים "
                        "וללמידה לבחינות. תוספת של עד 15 ימים באישור מפקד יחידה/סא\"ל.",
            },
            {
                "label": "למידה לבחינה",
                "days": "4 ימים לכל בחינה",
                "approver": "אישור בכתב ממפקדו",
                "account": "יומיים ראשונים — חופשה שנתית; יומיים נוספים — מיוחדת אישית",
                "clause": "הנספח — \"חופשה לצורך למידה לבחינות\"",
                "note": "עד שתי בחינות בשנה. חל על בגרות, השלמות י\"ג/י\"ד, פסיכומטרי "
                        "ובחינות קבלה להשכלה גבוהה.",
            },
        ],
    },
}


def leave_categories() -> list[tuple[str, str]]:
    """[(key, title)…] for the category picker, in display order."""
    return [(k, v["title"]) for k, v in LEAVE_CATEGORIES.items()]


def leave_cases(category_key: str) -> list[dict]:
    """The case rows for a category (>=1)."""
    return LEAVE_CATEGORIES[category_key]["cases"]


def leave_pick_label(category_key: str) -> str | None:
    """Label for the second (case) picker, or None for single-case categories."""
    cat = LEAVE_CATEGORIES.get(category_key, {})
    return cat.get("pick_label") if len(cat.get("cases", [])) > 1 else None


def leave_result(category_key: str, case_index: int) -> dict:
    """A single leave case enriched with its full citation string."""
    case = LEAVE_CATEGORIES[category_key]["cases"][case_index]
    return {**case, "citation": cite(_DOC_LEAVE, case["clause"])}


# ─────────────────────────────────────────────────────────────────────────────
# Calculator B, part 1 — דמי קיום חודשיים (35.0201).
#
# GROUNDING: the order fixes NO shekel amount. Section 1 says the sum is set by
# the Chief of Staff with the Defense Minister's approval, built from components;
# it is CPI-updated every January. So we surface the STRUCTURE and say plainly
# that the order carries no flat figure — never invent one.
# ─────────────────────────────────────────────────────────────────────────────

SUBSISTENCE = {
    "headline": "הפקודה אינה נוקבת בסכום בשקלים.",
    "how_set": (
        "הרמטכ\"ל, באישור שר הביטחון, קובע את סכום דמי הקיום החודשיים לחייל "
        "בדרגת טוראי (סעיף 1). הסכום מתעדכן אחת לשנה, בחודש ינואר, לפי שיעור "
        "עליית מדד המחירים לצרכן."
    ),
    # The three components that make up the monthly subsistence (סעיף 1).
    "components": [
        "רכיב אחיד בעבור צריכה בסיסית (ביגוד תחתון, הגיינה ותקשורת)",
        "רכיב אחיד בעבור רווחת החייל",
        "רכיב משתנה בעבור רווחה והוקרה — תוספת פעילות, תוספת תפקיד ותוספת לחימה "
        "(מפורט בסעיף 8)",
    ],
    # Additional monthly payments on top of the subsistence (סעיף 2).
    "supplements": [
        "תוספת דרגה — לחייל שדרגתו גבוהה מטוראי",
        "תוספת ביגוד אזרחי — לחייל הרשאי ללבוש אזרחי במסגרת תפקידו",
        "דמי כלכלה — לחייל שאין ביחידתו סידורי כלכלה",
    ],
    "clause": "סעיפים 1–2 ו-8",
}


def subsistence_structure() -> dict:
    """The 35.0201 subsistence structure card, with its citation."""
    return {**SUBSISTENCE, "citation": cite(_DOC_SUBSIST, SUBSISTENCE["clause"])}


# ─────────────────────────────────────────────────────────────────────────────
# Calculator B, part 2 — תשלום למשפחת החייל (35.0210).
#
# GROUNDING: payments are PERCENTAGES of a "basic wage", which the order defines
# as 50% of the national average wage (updated by cost-of-living). So the order
# gives a real percentage table (surfaced verbatim) but NO shekel figure — the
# base tracks the average wage, and we say so instead of inventing shekels.
# ─────────────────────────────────────────────────────────────────────────────

# The order's "תשלום מלא" table (פרק א׳, הגדרת "תשלום מלא"): percent of the
# basic wage by recipient and number of minors. "-" columns mean the row has a
# single flat rate. `bands` maps a minors-band key to the stated percentage.
FAMILY_RECIPIENTS: dict[str, dict] = {
    "wife_mother": {
        "label": "אשת חייל שהיא אם לילד",
        "by_minors": False,
        "bands": {"flat": "100%"},
        "chapter": "פרק ב׳ סעיף 1",
        "note": "אשת חייל, אם לילד אחד לפחות שפרנסתו עליה, זכאית לתשלום המלא.",
    },
    "wife": {
        "label": "אשת חייל (שאינה אם לילד)",
        "by_minors": False,
        "bands": {"flat": "80%"},
        "chapter": "פרק ב׳",
        "note": "זכאות מותנית (מחוסרת הכנסה / לימודים / אי-כושר עבודה וכד'); "
                "אשת חייל הלומדת במוסד לימודים — 3/4 מהתשלום המלא.",
    },
    "parents_couple": {
        "label": "זוג הורים",
        "by_minors": True,
        "bands": {"upto3": "90%", "four": "95%", "five_plus": "100%"},
        "chapter": "פרק ג׳",
        "note": "זכאות להורי חייל רווק בתנאים (אב נטול השתכרות/אם בודדה, אין תומך, "
                "אין הכנסה מספקת).",
    },
    "single_parent": {
        "label": "הורה בודד",
        "by_minors": True,
        "bands": {"upto3": "80%", "four": "85%", "five_plus": "90%"},
        "chapter": "פרק ג׳",
        "note": "הורה בודד = אב בודד או אם בודדת, בתנאי הזכאות של פרק ג׳.",
    },
}

# Minor-band picker options (order matters), matching the table columns.
FAMILY_MINOR_BANDS: list[tuple[str, str]] = [
    ("upto3", "עד 3 קטינים"),
    ("four", "4 קטינים"),
    ("five_plus", "5 קטינים ויותר"),
]

# The "basic wage" the percentages apply to (פרק א׳, הגדרת "שכר בסיסי").
FAMILY_BASE_NOTE = (
    "האחוזים הם מתוך \"השכר הבסיסי\", המוגדר בפקודה כ-50% מהשכר הממוצע במשק "
    "(פרק א׳, הגדרת \"שכר בסיסי\"), ומתעדכן לפי הפיצוי/מדד. הפקודה אינה נוקבת "
    "בסכום בשקלים."
)
# Overall ceiling on all payments in respect of one soldier (פרק י״א, סעיף 1).
FAMILY_CEILING_NOTE = (
    "תקרה: סך כל התשלומים בשל חייל לא יעלה על 120% מהשכר הבסיסי "
    "(פרק י\"א, סעיף 1)."
)


def family_recipients() -> list[tuple[str, str]]:
    """[(key, label)…] for the recipient picker, in display order."""
    return [(k, v["label"]) for k, v in FAMILY_RECIPIENTS.items()]


def family_needs_minors(recipient_key: str) -> bool:
    """True when the recipient's rate depends on the number of minors."""
    return FAMILY_RECIPIENTS[recipient_key]["by_minors"]


def family_payment(recipient_key: str, minors_band: str | None = None) -> dict:
    """The stated percentage + eligibility context for a family recipient.

    `minors_band` (one of FAMILY_MINOR_BANDS keys) is required only when
    family_needs_minors() is True; otherwise the row's flat rate is returned.
    Returns the percentage as the order states it — never a shekel figure.
    """
    r = FAMILY_RECIPIENTS[recipient_key]
    if r["by_minors"]:
        band = minors_band or FAMILY_MINOR_BANDS[0][0]
        percent = r["bands"].get(band) or "—"
    else:
        percent = r["bands"]["flat"]
    return {
        "label": r["label"],
        "percent": percent,
        "note": r["note"],
        "base_note": FAMILY_BASE_NOTE,
        "ceiling_note": FAMILY_CEILING_NOTE,
        "citation": cite(
            _DOC_FAMILY, f'פרק א׳ (הגדרת "תשלום מלא") ו-{r["chapter"]}'
        ),
    }
