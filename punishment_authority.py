# -*- coding: utf-8 -*-
"""בודק סמכות עונש משמעתי — הכוונה כללית מתוך פ"מ 33.0302 ("דין משמעתי").

This module is a *deterministic, zero-token* lookup of the maximum punishments
each type of judging officer (קצין שיפוט) is authorised to impose in a
disciplinary hearing (דין משמעתי), together with the appeal (ערר) path — all
taken VERBATIM from order PM-33.0302 and cited to its own clauses / annex.

It is quasi-legal guidance (הכוונה כללית), NOT legal advice: it surfaces the
order's own caps with citations, it never declares a specific punishment
"illegal". The binding text is always the order itself.

Grounding — every number below was cross-checked three ways and matches
exactly (see the checker script + rendered PDF page in the build report):
  * storage/json_store/דין-משמעתי.json  → raw_text Annex F table (נספח ו').
  * the same JSON's curated `sections` ("annex-vav-1" / "annex-vav-2").
  * the source PDF פמ-דין-משמעתי-330302-לאתר-הפקודות.pdf, printed page 76
    (טבלת סמכויות ענישה) — eyeballed cell-by-cell.

IMPORTANT — the caps are keyed to the officer's RANK, because that is how the
order tabulates them (נספח ו' has one column per rank, NOT one column per the
קש"ז/קש"ב label). The order's *jurisdiction* categories are only three —
קצין שיפוט זוטר (קש"ז), קצין שיפוט בכיר (קש"ב) and קצין שיפוט בכיר ממונה
(קש"ב ממונה), all defined in סעיף 25 — but a single "קש"ב" spans four rank
rows with DIFFERENT caps (סרן מ"פ / רס"ן / סא"ל / אל"ם), so collapsing the
dropdown to three options would over- or under-state the numbers. We therefore
expose the five rank rows and tag each with its קש"ז/קש"ב classification.

If PM-33.0302 is re-ingested and its Annex F changes, REBUILD this module from
the new raw_text / PDF — do not trust these literals blindly.
"""

DOC_ID = "PM-33.0302"
DOC_TITLE = "דין משמעתי"

# ── Officer types = the five rank rows of Annex F, table 1 ──────────────────
# key -> UI label. The label carries BOTH the order's jurisdiction class
# (קש"ז / קש"ב, per סעיף 25) AND the rank, so the caps stay exactly citable.
# Order matches the order's own table columns (junior -> senior).
_OFFICER_LABELS: list[tuple[str, str]] = [
    ("zoter_segen_seren",          'קצין שיפוט זוטר (קש"ז) — דרגת סגן עד סרן'),
    ("bakhir_seren_mefaked_pluga", 'קצין שיפוט בכיר (קש"ב) — סרן מפקד פלוגה ביחידה לוחמת'),
    ("bakhir_rasan",               'קצין שיפוט בכיר (קש"ב) — דרגת רס"ן'),
    ("bakhir_saal",                'קצין שיפוט בכיר (קש"ב) — דרגת סא"ל'),
    ("bakhir_alam",                'קצין שיפוט בכיר (קש"ב) — דרגת אל"ם ומעלה'),
]
_KEYS = [k for k, _ in _OFFICER_LABELS]

# ── Annex F, table 1 (טבלת סמכויות ענישה) — VERBATIM ────────────────────────
# One row per punishment; five values, one per officer key in _KEYS order
# (סגן-סרן, סרן-מ"פ, רס"ן, סא"ל, אל"ם ומעלה). This mirrors the PDF grid on
# page 76 so a reviewer can check it column-by-column against the image.
# "מוסמך" = authorised, no numeric cap; "לא מוסמך" = NOT authorised (kept on
# purpose — "you may not impose X" is exactly the grounding a soldier needs).
_CLAUSE_T1 = 'נספח ו\' (טבלה 1)'
_CLAUSE_T2 = 'נספח ו\' (טבלה 2)'

_ROWS: list[tuple[str, str, list[str]]] = [
    # (punishment, clause, [zoter, seren-mp, rasan, saal, alam])
    ("התראה", _CLAUSE_T1,
        ["מוסמך", "מוסמך", "מוסמך", "מוסמך", "מוסמך"]),
    ("ריתוק (למחנה או לאונייה)", _CLAUSE_T1,
        ["עד 7 ימים", "עד 21 ימים", "עד 21 ימים", "עד 28 ימים", "עד 35 ימים"]),
    ("נזיפה", _CLAUSE_T1,
        ["מוסמך", "מוסמך", "מוסמך", "מוסמך", "מוסמך"]),
    ("נזיפה חמורה", _CLAUSE_T1,
        ["לא מוסמך", "לא מוסמך", "מוסמך", "מוסמך", "מוסמך"]),
    ("קנס — חיילי חובה", _CLAUSE_T1,
        ["עד 1/6 שכר טוראי", "עד 1/6 שכר טוראי", "עד 1/3 שכר טוראי",
         "עד 1/3 שכר טוראי", "עד 1/3 שכר טוראי"]),
    ("קנס — חיילי קבע ומילואים", _CLAUSE_T1,
        ["משכורת טוראי אחת", "משכורת טוראי אחת", "שתי משכורות טוראי",
         "שתי משכורות טוראי", "שתי משכורות טוראי"]),
    # clause 131 lets a fine be imposed for absence/loss-of-contact offences,
    # within the Annex F limits — hence the extra prose anchor on this row.
    ("קנס — חיילי מילואים (עבירות היעדר מן השירות שלא ברשות ואיבוד קשר)",
        'סעיף 131 · ' + _CLAUSE_T1,
        ["ארבע משכורות טוראי", "ארבע משכורות טוראי", "ארבע משכורות טוראי",
         "ארבע משכורות טוראי", "ארבע משכורות טוראי"]),
    ("מחבוש — עבירת היעדר מן השירות שלא ברשות עד 24 שעות", _CLAUSE_T1,
        ["לא מוסמך", "עד יומיים", "עד ארבעה ימים", "עד שישה ימים", "עד שמונה ימים"]),
    ("מחבוש — עבירת הופעה ולבוש (הרשעה שנייה ואילך)", _CLAUSE_T1,
        ["לא מוסמך", "עד יומיים", "עד ארבעה ימים", "עד שישה ימים", "עד שמונה ימים"]),
    ("מחבוש — כל עבירה אחרת שקצין השיפוט מוסמך לדון בה", _CLAUSE_T1,
        ["לא מוסמך", "עד חמישה ימים", "עד עשרה ימים", "עד 20 יום", "עד 30 יום"]),
    # clause 130 lets a military-driving-licence suspension be imposed for a
    # traffic offence, within the Annex F limits.
    ("פסילת רישיון נהיגה צבאי (עבירות תנועה)", 'סעיף 130 · ' + _CLAUSE_T1,
        ["לא מוסמך", "לא מוסמך", 'עד 3 חודשים — רק אם מונה לשיפוט בידי סא"ל',
         "עד 3 חודשים", "עד 3 חודשים"]),
    ("הורדה בדרגה (עד סמ\"ר; לא בהרשעה ראשונה בעבירת הופעה ולבוש)", _CLAUSE_T1,
        ["לא מוסמך", "לא מוסמך", "לא מוסמך", "הורדה בדרגה אחת", "הורדה בדרגה אחת"]),
]

# Rank-specific footnotes that a single cell can't hold cleanly.
_OFFICER_NOTES: dict[str, str] = {
    "bakhir_alam": 'הדרגה היחידה שמוסמכת להטיל מחבוש על נידון שהוא קצין או '
                   'נגד בכיר — מחבוש כזה יוטל רק בפני קצין שיפוט בדרגת אל"ם '
                   'לפחות (נספח ו\', טבלה 1).',
}


def _build_officer_types() -> dict[str, dict]:
    """Transpose the verbatim Annex F rows into a per-officer dict. Building
    from ONE authoritative copy of each number (the PDF-ordered `_ROWS`) avoids
    the transcription drift a hand-written per-officer literal invites."""
    out: dict[str, dict] = {}
    for col, (key, label) in enumerate(_OFFICER_LABELS):
        out[key] = {
            "label": label,
            "caps": [
                {"punishment": punishment, "max": values[col], "clause": clause}
                for punishment, clause, values in _ROWS
            ],
            "note": _OFFICER_NOTES.get(key),
        }
    return out


# The public curated table: {officer_key: {"label", "caps": [...], "note"}}.
OFFICER_TYPES: dict[str, dict] = _build_officer_types()

# ── Appeal path (ערר) — סעיפים 127, 223, 226 ────────────────────────────────
APPEAL: dict[str, str] = {
    "text": 'לחייל שנשפט בדין משמעתי יש זכות להגיש ערר על הפסק. הערר מוגש '
            'בתוך שלושה ימים מיום מתן הפסק (או בתוך 15 ימים אם הדיון נערך '
            'על-פי הוראת פרקליט), ונדון בפני קצין שיפוט בכיר ממונה (קש"ב '
            'ממונה). עם גזירת העונש חייב קצין השיפוט להודיע לנידון על זכות '
            'הערר, על המועד להגשתו ועל הגורם שאליו יש לפנות.',
    "clause": "סעיפים 127, 223(א), 226",
}

# ── Cross-cutting caveats that apply regardless of the officer's rank ────────
GENERAL_NOTES: list[dict[str, str]] = [
    {"text": "עונש אחד בלבד לכל עבירה — קצין השיפוט מטיל עונש אחד על כל עבירה "
             "שבה הרשיע. חריגים: פסילת רישיון בעבירת תנועה וקנס בעבירת היעדר "
             "יכולים להתווסף לעונש אחר.",
     "clause": "סעיפים 129–131"},
    {"text": "מחבוש בעבירת הופעה ולבוש בהרשעה ראשונה — אף קצין שיפוט, בכל דרגה, "
             "אינו מוסמך להטיל מחבוש.",
     "clause": _CLAUSE_T1},
    {"text": "מחבוש מצטבר (צבירת שני עונשי מחבוש או יותר לריצוי בפועל) כפוף "
             "לתקרות נפרדות: סרן מ\"פ עד 10 ימים, רס\"ן עד 15, סא\"ל עד 30, "
             "אל\"ם ומעלה עד 45; קצין שיפוט זוטר (סגן/סרן) — לא מוסמך.",
     "clause": _CLAUSE_T2},
    {"text": "קנס שלא ניתן לגבותו בניכוי — קצין השיפוט רשאי להטיל, במעמד גזירת "
             "העונש, מחבוש חלופי למקרה שהקנס לא ישולם.",
     "clause": "עיקרי הפקודה"},
]

# One disclaimer string, reused by the UI. Conservative by design: never
# "the punishment is illegal", only "it may be worth checking / an appeal".
DISCLAIMER: str = (
    'המידע כאן הוא הכוונה כללית מתוך פ"מ 33.0302 ("דין משמעתי") ואינו ייעוץ '
    'משפטי; הנוסח המחייב הוא הפקודה עצמה. אם נגזר עליך עונש שחורג מהתקרות '
    'שמוצגות כאן — ייתכן שכדאי לברר את הדבר או לשקול הגשת ערר. אין באמור כאן '
    'קביעה שעונש כלשהו "אינו חוקי".'
)


def officer_options() -> list[tuple[str, str]]:
    """(key, label) pairs in the order's own junior→senior order — feeds the
    UI selectbox directly."""
    return list(_OFFICER_LABELS)


def authority_for(officer_key: str) -> dict | None:
    """The full authority record for one officer type, or None if the key is
    unknown. Pure dict lookup — zero LLM tokens, deterministic."""
    return OFFICER_TYPES.get(officer_key)
