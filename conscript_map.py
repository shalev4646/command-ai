# -*- coding: utf-8 -*-
"""מפת הזכויות של חייל בשירות חובה — ציר שירות, לא טבלה.

ההבדל ממפות המילואים והקבע: לשירות חובה יש התחלה וסוף ידועים, ולכן השורות
נחלקות לשלושה פרקים לפי המיקום על הציר — מה מגיע עכשיו, מה ייפתח בקרוב, ומה
מחכה בשחרור.

שני כללי-על שקובעים את כל מה שלמטה:

1. **הקלט מחדד, לא פותח.** `benefit_rows()` מחזירה את כל השורות גם כשהפרופיל
   ריק לגמרי — הפרופיל רק מוסיף התאמה אישית לתת-השורה. אף שורה אינה נעולה
   מאחורי טופס; זו הסיבה שהמפה יכולה להחליף את מחשבון הזכאויות, שענה בלי
   לבקש כלום.
2. **תאריך השחרור הוא קלט ולא פלט.** אורך שירות החובה אינו מופיע באף פקודה
   בקורפוס — הוא נקבע בחוק שירות ביטחון, שונה בין מסלולים, וזז בחקיקה. לכן
   המודול לעולם אינו מסיק תאריך שחרור; הוא מקבל אותו מהחייל (שיודע אותו בעל
   פה) וגוזר ממנו אריתמטיקה בלבד.

Pure, curated data + pure functions — ZERO LLM tokens, no Anthropic call ever.
כל ערך נקרא מטקסט הפקודה ב-storage/json_store. ערכי החופשות, התשלומים
למשפחה ודמי הקיום מיובאים מ-`entitlements.py` במקום להשתכפל, כדי שלא ייווצרו
שני מקורות-אמת לאותו מספר.
"""

from __future__ import annotations

from datetime import date

import entitlements as _ent

# ── Standing caveat, shown under every result. Not a value. ──
DISCLAIMER = (
    "הכוונה כללית בלבד, אינה ייעוץ ואינה מחליפה את הפקודה המחייבת. "
    "בכל סתירה — נוסח הפקודה הרשמי הוא הקובע."
)

LAST_VERIFIED = "אוגוסט 2026"

# Short order labels reused in citations.
_D_LEAVE = 'פ"מ 35.0402'      # חופשות לחיילים המשרתים בשירות חובה
_D_SUBSIST = 'פ"מ 35.0201'    # דמי קיום
_D_FAMILY = 'פ"מ 35.0210'     # חוקת התשלומים למשפחות
_D_FOOD = 'פ"מ 56.0131'       # דמי כלכלה
_D_RENT = 'פ"מ 35.0307'       # השתתפות בשכ"ד — שירות חובה
_D_SINGLE = 'פ"מ 35.0808'     # חיילים בודדים
_D_GRANTS = 'פ"מ 35.0805'     # מענק נישואין ומענק לידה
_D_FUND = 'פ"מ 35.0803'       # קופה להלוואות ולמענקים
_D_ABSORB = "חוק קליטת חיילים משוחררים"   # מקור אזרחי — לא פקודת צבא

# ─────────────────────────────────────────────────────────────────────────────
# Service tracks.
#
# ⚠ שתי טקסונומיות שונות, ובכוונה לא ממופות זו לזו:
#   * הכסף (מענק ופיקדון) מדורג לוחם / תומך לחימה / עורפי.
#   * חופשת השחרור מדורגת לפי תעודת לוחם (7 / 10 / 14) — קטגוריה אחרת.
# כפיית מיפוי בין השתיים הייתה מייצרת התאמה אישית שגויה, ולכן שורת חופשת
# השחרור נשארת כללית ומציגה את המבנה המלא. עדיף שורה כללית נכונה.
# ─────────────────────────────────────────────────────────────────────────────

TRACKS: dict[str, dict] = {
    "lohem":  {"label": "לוחם",         "grant": 684.99, "deposit": 990.63},
    "tomekh": {"label": "תומך לחימה",   "grant": 570.42, "deposit": 825.52},
    "oref":   {"label": "עורפי",        "grant": 455.84, "deposit": 660.42},
}
TRACK_ORDER = ["lohem", "tomekh", "oref"]

# The absorption-law figures are index-linked and carry their own as-of date.
# Numeric form on purpose: the row template renders it as "נכון ל-{asof}",
# and a month name there reads as "נכון ל-מאי 2026".
RATES_ASOF = "05.2026"

# Minimum service for the discharge grant (חוק קליטת חיילים משוחררים).
# ⚠ אינו הסף של מענק שחרורין משירות קבע (24 חודשים, פ"מ 31.0517) — מסלול אחר.
GRANT_MIN_MONTHS = 12

SECTION_ORDER = ["now", "soon", "discharge"]
SECTION_LABELS = {
    "now": "מה מגיע לי עכשיו",
    "soon": "מה ייפתח בקרוב",
    "discharge": "לקראת שחרור",
}


# ─────────────────────────────────────────────────────────────────────────────
# Timeline arithmetic — pure functions over dates the SOLDIER supplied.
# Nothing here asserts how long mandatory service is.
# ─────────────────────────────────────────────────────────────────────────────

def months_between(start: date | None, end: date | None) -> int | None:
    """Whole months from `start` to `end`, never negative. None if unknown."""
    if not start or not end or end < start:
        return None
    months = (end.year - start.year) * 12 + (end.month - start.month)
    if end.day < start.day:
        months -= 1
    return max(0, months)


def days_between(start: date | None, end: date | None) -> int | None:
    """Whole days from `start` to `end`, never negative. None if unknown."""
    if not start or not end:
        return None
    return max(0, (end - start).days)


def add_months(start: date | None, months: int) -> date | None:
    """`start` shifted forward by whole months, clamped to month length."""
    if not start:
        return None
    total = start.month - 1 + months
    year = start.year + total // 12
    month = total % 12 + 1
    last = [31, 29 if (year % 4 == 0 and (year % 100 != 0 or year % 400 == 0))
            else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1]
    return date(year, month, min(start.day, last))


def service_status(enlist: date | None, discharge: date | None,
                   today: date | None = None) -> dict:
    """Where the soldier stands on the axis, from dates they supplied.

    Every field is None when its inputs are missing — the caller renders the
    general form instead. `released` is True once the discharge date passed.
    """
    today = today or date.today()
    # A discharge before enlistment is not a soldier we can place on an axis —
    # a typo in one field must not produce a confident-looking wrong date.
    # Both dates are dropped and every row falls back to its general form.
    if enlist and discharge and discharge < enlist:
        enlist = discharge = None
    # Service stops accruing at discharge: counting to today would tell a
    # soldier released three years ago that he has served 67 months.
    served_to = min(today, discharge) if discharge else today
    served = months_between(enlist, served_to)
    return {
        "months_served": served,
        "days_left": days_between(today, discharge),
        "total_months": months_between(enlist, discharge),
        "released": bool(discharge and discharge < today),
        "not_started": bool(enlist and enlist > today),
        "grant_date": add_months(enlist, GRANT_MIN_MONTHS),
        "grant_eligible": served is not None and served >= GRANT_MIN_MONTHS,
    }


def money_estimate(track: str | None, months: int | None) -> dict | None:
    """Grant + deposit estimate: months of service × the per-month rate.

    Arithmetic on the soldier's own dates and track — not a legal claim. The
    rates are index-linked, hence `asof`.
    """
    if not track or track not in TRACKS or not months:
        return None
    t = TRACKS[track]
    return {
        "track_label": t["label"],
        "months": months,
        "grant": round(t["grant"] * months),
        "deposit": round(t["deposit"] * months),
        "asof": RATES_ASOF,
    }


def _fmt(d: date | None) -> str:
    return f"{d.day}.{d.month}.{d.year}" if d else ""


# ─────────────────────────────────────────────────────────────────────────────
# The rows.
#
# Each row: section / title / sub / how[] / cite / ask, plus optional tag,
# civil, link, asof — the same contract `_mil_details_row` already renders.
# `ask` is the "שאל על זה" question: every card is a funnel INTO the chat,
# which is the product. A row without `ask` is a bug (the test enforces it).
#
# ⚠ אין קישורים מומצאים: `link` מושמט בכל שורה שאין לה URL ציבורי מאומת.
# ⚠ ערך שהפקודה אינה נוקבת בו במספר מוצג כמבנה ולא כסכום.
# ─────────────────────────────────────────────────────────────────────────────

def _leave_days(category: str, index: int = 0) -> str:
    """A verified day-count straight from entitlements.py — single source."""
    return _ent.LEAVE_CATEGORIES[category]["cases"][index]["days"]


def benefit_rows(profile: dict | None = None,
                 today: date | None = None) -> list[dict]:
    """Every row, always. `profile` only sharpens the sub-lines.

    profile keys (all optional): enlist, discharge (date), track (TRACKS key),
    single, married, far_from_home (bool).
    """
    p = profile or {}
    st = service_status(p.get("enlist"), p.get("discharge"), today)
    single = bool(p.get("single"))
    married = bool(p.get("married"))
    est = money_estimate(p.get("track"), st["total_months"])

    rows: list[dict] = []

    # ── מה מגיע לי עכשיו ──────────────────────────────────────────────────
    rows.append({
        "section": "now",
        "title": "חופשה שנתית",
        "sub": f"{_leave_days('annual')} בשנה",
        "how": [
            f"המכסה השנתית היא {_leave_days('annual')}, לאישור המפקד.",
            "ימי מפקד נוספים: " + _leave_days("command_days") + ".",
        ],
        "cite": _ent.cite(_D_LEAVE, 'סעיף 11 והנספח — שורת "חופשה שנתית"'),
        "ask": "כמה ימי חופשה שנתית מגיעים לי ואיך מבקשים?",
    })

    rows.append({
        "section": "now",
        "title": "חופשת שחרור",
        "sub": "7 / 10 / 14 ימים — לפי תעודת לוחם",
        "how": [
            "7 ימים ללא תעודת לוחם.",
            "10 ימים לזכאי תעודת לוחם.",
            "14 ימים לזכאי תעודת לוחם חוד.",
            "המדרג תלוי בתעודת לוחם ולא במסלול השירות — לכן השורה כללית.",
        ],
        "cite": _ent.cite(_D_LEAVE, 'הנספח — שורת "חופשת שחרור"'),
        "ask": "כמה ימי חופשת שחרור מגיעים לי?",
    })

    rows.append({
        "section": "now",
        "title": "חופשה מיוחדת — משפחתית ואישית",
        "sub": f"מכסה {_leave_days('family')} משפחתית · {_leave_days('personal')} אישית",
        "how": [
            f"מכסה משפחתית כוללת: {_leave_days('family')} (נישואין, לידה, אבל ועוד).",
            f"מכסה אישית כוללת: {_leave_days('personal')} — כלכלית, סוציאלית או טעמים אישיים.",
            "האישור בדרגת רס\"ן לפחות.",
        ],
        "cite": _ent.cite(_D_LEAVE, "סעיפים 13–15 והנספח"),
        "ask": "מה המכסה של חופשה מיוחדת בשירות חובה?",
    })

    rows.append({
        "section": "now",
        "title": "דמי קיום",
        "sub": "מבנה הזכאות — הסכום נקבע בנפרד",
        "how": [
            "הפקודה אינה נוקבת בסכום: הוא נקבע בידי הרמטכ\"ל באישור שר הביטחון "
            "ומתעדכן לפי המדד.",
            "לכן מוצג כאן המבנה בלבד ולא סכום בשקלים.",
        ],
        "cite": _ent.cite(_D_SUBSIST, "סעיף 1"),
        "ask": "ממה מורכבים דמי הקיום שאני מקבל?",
    })

    food_sub = "כשאין הסדר הזנה, בתפקיד, או לחייל בודד"
    rows.append({
        "section": "now",
        "title": "דמי כלכלה",
        "sub": food_sub + (" · רלוונטי לך כחייל בודד" if single else ""),
        "how": [
            "זכאות כשאין ביחידה סידורי מטבח או הסדר הזנה, או כשהתפקיד מוציא "
            "אותך מהיחידה.",
            "חייל בודד או זכאי סיוע — גם בחופשת מחלה בבית מעל שלושה ימים, "
            "ובחופשה מיוחדת מטעמים כלכליים או סוציאליים.",
            "עד 20 ימים בחודש; עד 24 ימים אם נדרשת לעבוד שישה ימים בשבוע.",
            "השיעור נקבע מעת לעת בידי היועץ הכספי לרמטכ\"ל — לכן אין כאן סכום.",
        ],
        "cite": _ent.cite(_D_FOOD, "סעיפים 1, 2, 4"),
        "ask": "מתי חייל בשירות חובה זכאי לדמי כלכלה?",
    })

    rent_sub = ("מגיע לך אם אתה צד בחוזה שכירות" if (single or married)
                else "לחייל בודד או לחייל נשוי")
    rows.append({
        "section": "now",
        "title": "השתתפות בשכר דירה",
        "sub": rent_sub,
        "how": [
            "הזכאות היא לחייל שהוכר כחייל בודד לצורך סיוע בשכ\"ד, או לחייל נשוי, "
            "והוא צד בחוזה השכירות.",
            "אין זכאות אם הדירה או החדר נשכרו מקרוב משפחה מדרגה ראשונה.",
            "הבקשה בטופס 833-1 דרך קצינת ת\"ש או רכזת תנאי השירות, בשני העתקים "
            "ובצירוף חוזה שכירות חתום.",
            "בדירה משותפת — החלק היחסי בלבד.",
        ],
        "cite": _ent.cite(_D_RENT, "סעיפים 5, 8, 11, 12"),
        "ask": "מגיע לי סיוע בשכר דירה ואיך מגישים בקשה?",
    })

    rows.append({
        "section": "now",
        "title": "הכרה כחייל בודד",
        "sub": ("מוכר — ההכרה בתוקף עד תום השירות" if single
                else "מי נחשב, ומה נפתח עם ההכרה"),
        "tag": "ת\"ש",
        "how": [
            "חייל בודד מובהק — מי שהתייתם משני הוריו, או שהוריו מתגוררים בחו\"ל "
            "דרך קבע.",
            "ההכרה נעשית בידי קצין ת\"ש יחידתי או מרחבי, ותקפה עד תום שירות "
            "החובה (למעט מי שהוריו בשליחות).",
            "מי שאינו מקיים קשר עם הוריו — הכרה מסיבות חריגות דרך ועדת החריגים.",
            "חובה לדווח לסגל הת\"ש על שינוי בנסיבות בתוך 14 ימים.",
            "עם ההכרה נפתחות הטבת דיור, דמי כלכלה וחופשת ביקור בחו\"ל.",
        ],
        "cite": _ent.cite(_D_SINGLE, "סעיפים 1–3, 6"),
        "ask": "אני עומד בתנאים של חייל בודד ואיך מקבלים הכרה?",
    })

    if single:
        rows.append({
            "section": "now",
            "title": "חופשת ביקור קרוב בחו\"ל",
            "sub": "30 יום בשנת שירות — לחייל בודד",
            "how": [
                "30 יום בשנת שירות לביקור קרוב מדרגה ראשונה המתגורר בחו\"ל.",
                "נוסף על מכסת החופשה המיוחדת — לא על חשבונה.",
                "ניתן לפצל בתוך שנת השירות, אך לא לצבור משנה לשנה.",
            ],
            "cite": _ent.cite(_D_SINGLE, "סעיף הטבות לחייל בודד"),
            "ask": "מגיעה לי חופשה לבקר את המשפחה בחו\"ל?",
        })

    rows.append({
        "section": "now",
        "title": "מענק נישואין ומענק לידה",
        "sub": "בשיעור שכר טוראי בשירות חובה",
        "how": [
            "מענק נישואין לחייל הנישא; לחיילת — לאחר שנה אחת של שירות לפחות.",
            "מענק לידה או אימוץ, מוכפל במספר הילדים. שני בני זוג בשירות חובה — "
            "מענק אחד בלבד.",
            "גובה המענק הוא שיעור שכר טוראי בשירות חובה — הפקודה אינה נוקבת "
            "בסכום.",
            "הדיווח לשליש היחידה בצירוף תעודה, ומשם בטופס 19.",
        ],
        "cite": _ent.cite(_D_GRANTS, "סעיפים 1–5"),
        "ask": "מה מגיע לי אם אני מתחתן בזמן השירות?",
    })

    rows.append({
        "section": "now",
        "title": "קופה להלוואות ולמענקים",
        "sub": "דרך קצין ת\"ש — למצוקה כלכלית",
        "tag": "ת\"ש",
        "how": [
            "קצין ת\"ש פיקודי או חילי מוסמך לאשר הלוואה או מענק לאחר בדיקה.",
            "הלוואה — בשיעור שכר טוראי; מענק — מחציתו. בסמכות קצין הת\"ש להגדיל "
            "עד משכורת טוראי במענק ושתי משכורות בהלוואה.",
            "מעבר לכך — אכ\"א מחלקת פרט, ובמקרים קשים ועדת חריגים.",
            "הלוואה תאושר רק אם יתרת השירות מאפשרת להחזירה עד תום השירות.",
        ],
        "cite": _ent.cite(_D_FUND, "סעיפים 2, 5, 6, 7"),
        "ask": "מה התנאים לקבלת הלוואה או מענק מקצין ת\"ש?",
    })

    rows.append({
        "section": "now",
        "title": "תשלומים למשפחה",
        "sub": "כשהמשפחה נזקקת לתמיכה",
        "how": [
            "התשלום מחושב כאחוז מהשכר הבסיסי, לפי זהות מקבל התשלום ומספר "
            "הילדים הקטינים.",
            "השכר הבסיסי הוא 50% מהשכר הממוצע במשק, עם תקרה של 120%.",
            "הבקשה מוגשת דרך קצינת ת\"ש ביחידה.",
        ],
        "cite": _ent.cite(_D_FAMILY, "טבלת האחוזים והשכר הבסיסי"),
        "ask": "המשפחה שלי זקוקה לתמיכה — מה מגיע לה?",
    })

    # ── מענק השחרור: הפרק נקבע לפי המיקום על הציר ─────────────────────────
    grant_soon = st["grant_eligible"] is False and st["grant_date"] is not None
    grant_sub = "12 חודשי שירות לפחות"
    if grant_soon:
        grant_sub = f"נפתח ב-{_fmt(st['grant_date'])}"
    elif est:
        grant_sub = f"הערכה: כ-{est['grant']:,} ₪"

    grant_how = [
        "משולם אוטומטית לחשבון הבנק בתוך 20 עד 60 ימים מסיום השירות, בלי בקשה.",
        "חייל בודד מקבל מקדמה בתוך 14 ימים.",
        f"הזכאות: {GRANT_MIN_MONTHS} חודשי שירות לפחות, או שחרור מטעמי בריאות.",
        "תעריף לכל חודש שירות: " + " · ".join(
            f"{TRACKS[k]['label']} {TRACKS[k]['grant']:.2f} ₪" for k in TRACK_ORDER),
        "המענק פטור ממס. זהו חוק אזרחי ולא פקודת צבא.",
    ]
    if est:
        grant_how.insert(0, f"לפי {est['months']} חודשי שירות במסלול "
                            f"{est['track_label']} — הערכה של כ-{est['grant']:,} ₪.")
    rows.append({
        "section": "soon" if grant_soon else "discharge",
        "title": "מענק שחרור",
        "sub": grant_sub,
        "civil": True,
        "how": grant_how,
        "cite": f"לפי {_D_ABSORB}",
        "asof": RATES_ASOF,
        "ask": "כמה מענק שחרור מגיע לי ומתי הוא משולם?",
    })

    dep_how = [
        "זמין מהיום ה-14 שאחרי השחרור.",
        "תעריף לכל חודש שירות: " + " · ".join(
            f"{TRACKS[k]['label']} {TRACKS[k]['deposit']:.2f} ₪" for k in TRACK_ORDER),
        "בחמש השנים הראשונות מותר לשימוש רק ללימודים, הכשרה מקצועית, הקמת עסק, "
        "לימודי נהיגה, נישואין או רכישת דירה או קרקע.",
        "בתום חמש שנים היתרה עוברת אוטומטית לחשבון הבנק לשימוש חופשי — הכסף "
        "אינו אובד.",
        "הפיקדון פטור ממס.",
    ]
    if est:
        dep_how.insert(0, f"לפי {est['months']} חודשי שירות במסלול "
                          f"{est['track_label']} — הערכה של כ-{est['deposit']:,} ₪.")
    rows.append({
        "section": "discharge",
        "title": "פיקדון אישי",
        "sub": (f"הערכה: כ-{est['deposit']:,} ₪" if est
                else "נצבר לכל חודש שירות"),
        "civil": True,
        "how": dep_how,
        "cite": f"לפי {_D_ABSORB}",
        "asof": RATES_ASOF,
        "ask": "על מה מותר לי להשתמש בפיקדון האישי?",
    })

    return rows


def rows_by_section(profile: dict | None = None,
                    today: date | None = None) -> list[tuple[str, list[dict]]]:
    """`benefit_rows` grouped into the three chapters, in axis order.
    Empty chapters are dropped."""
    rows = benefit_rows(profile, today)
    out = []
    for sec in SECTION_ORDER:
        sec_rows = [r for r in rows if r["section"] == sec]
        if sec_rows:
            out.append((sec, sec_rows))
    return out
