# -*- coding: utf-8 -*-
"""מדריך "קיבלתי צו" — מסלול דחיית/קיצור שירות מילואים, מעוגן בפ"מ 31.0603.

Pure, curated data + lookup helpers — ZERO LLM tokens, no Anthropic call ever
(the letter step in the UI reuses letters.py and is the ONLY paid action).

Curation notes (2026-08-02):
  * Content read from storage/json_store (31.0603 "דחיית או קיצור שירות
    מילואים — ולת\"ם") AND cross-checked against the source PDF
    pdf-ldf_law/פמ-310603-ולתם-דחיית-מילואים.pdf.
  * The PDF's embedded text layer is OCR-noisy on DIGITS (e.g. 72→92, the
    "6" glyph doubles as a sentence mark). Values spelled in WORDS in the
    order (שלושה חודשים, ארבעה חודשים, שבעה ימים, שישה ימים) are trusted;
    the one digit-only value (submission window) was verified externally:
    kolzchut.org.il "פניה לוועדה לתיאום מילואים (ולת\"ם)" — הגשה עד 30 יום
    לפני תחילת השירות, ערעור בתוך 7 ימים (fetched 02.08.2026, matches the
    order's word-spelled שבעה ימים).
  * Section numbers are OCR-unreliable too, so citations use the order's
    SECTION HEADINGS (נוהל הפניות לוועדה / סמכויות / עררים / הגבלת מספר
    הבקשות), which are stable text.
  * Routine PERSONAL requests are explicitly outside the committee's scope
    (פרק "כללי"): they go to the unit; in a declared emergency — ועדה
    לתיאום שירות מילואים אישי (פ"מ 31.0605, גם היא בקורפוס).
This file is intentionally hand-curated and NOT derived from the live vector
index, so a re-ingestion cannot silently change a cited fact.
"""

from __future__ import annotations

DOC = 'פ"מ 31.0603'
_DOC_PERSONAL = 'פ"מ 31.0605'

DISCLAIMER = (
    "הכוונה כללית בלבד, אינה ייעוץ ואינה מחליפה את הפקודה המחייבת. "
    "בכל סתירה — נוסח הפקודה הרשמי הוא הקובע."
)

# the UI's cause picker, in display order
CAUSES: list[tuple[str, str]] = [
    ("economic", "עבודה / עסק"),
    ("studies", "לימודים"),
    # "אישי / משפחתי", not "אישי־משפחתי": browsers may break after the maqaf,
    # and at ~77px per tile the word fractured mid-letter ("אישר/משפח/תי",
    # 2026-08-03 device-width audit). The slash matches "עבודה / עסק" and
    # gives a clean two-line wrap point.
    ("personal", "אישי / משפחתי"),
]

# the four timeline stages painted at the top of the dialog
TIMELINE: list[str] = ["קיבלת צו", "הגשה עד 30 יום לפני השמ\"פ", "דיון והחלטה", "ערעור עד 7 ימים"]

# The single most important practical fact — pinned, never inside a step.
# Anchors: "נוהל הפניות" (the unit executes a decision via a corrected call-up
# order) + "עררים" (a decision under appeal stands until the appeal authority
# rules otherwise) — i.e. until a NEW order reaches you, the original binds.
STANDING_WARNING = {
    "text": "הגשת בקשה או ערעור אינה מבטלת את הצו: כל עוד לא קיבלת צו מתוקן "
            "מהיחידה — הצו המקורי בתוקף וחובה להתייצב במועדו.",
    "cite": f"לפי {DOC}, פרקי נוהל הפניות לוועדה ועררים",
}

LETTER_KEY = "reserve_deferral"  # must exist in letters.LETTER_TYPES

_CITE_SCOPE = f"לפי {DOC}, פרק כללי"
_CITE_SUBMIT = f"לפי {DOC}, פרק נוהל הפניות לוועדה"
_CITE_POWERS = f"לפי {DOC}, פרק סמכויות"
_CITE_APPEAL = f"לפי {DOC}, פרק עררים"
_CITE_LIMIT = f"לפי {DOC}, פרק הגבלת מספר הבקשות"

# step 3 ("מה קורה אחרי ההגשה") and step 4 ("נדחיתי") are common to the two
# committee causes; assembled per cause below so every cause owns its 4 steps.
_AFTER = {
    "title": "מה קורה אחרי ההגשה",
    "lines": [
        "הבקשה נדונה בוועדה לתיאום מילואים (ולת\"ם) — יו\"ר, נציג משרד ממשלתי ונציג צה\"ל.",
        "סטודנט או עצמאי רשאים להתייצב לדיון בעצמם; שכיר מיוצג על ידי המעסיק או נציגו.",
        "ההחלטה ניתנת בתום הדיון ומועברת ליחידה, והיחידה מוציאה צו מתוקן לפני תחילת השמ\"פ.",
        "הוועדה מוסמכת לדחות את השירות, לקצרו, לפצלו — או לדחות את הבקשה.",
    ],
    "cite": _CITE_POWERS,
}
_APPEAL = {
    "title": "נדחיתי — מה עכשיו?",
    "lines": [
        "ניתן לערער על החלטת הוועדה בתוך שבעה ימים ממועד הדיון.",
        "הערעור נדון בוועדת ערר שממנה הרמטכ\"ל.",
        "עד להחלטה בערעור — החלטת הוועדה המקורית עומדת בתוקפה.",
    ],
    "cite": _CITE_APPEAL,
}

_GUIDES: dict[str, dict] = {
    "economic": {
        "steps": [
            {
                "title": "האם הבקשה שלך בסמכות הוועדה?",
                "lines": [
                    "הוועדה דנה בשמ\"פ של שישה ימים ומעלה (בקשות קצרות יותר — מול היחידה).",
                    "עילה משקית: עצמאי המבקש לתאם את מועד המילואים, או שכיר שהמעסיק מבקש עבורו.",
                    "מותרת עד בקשה אחת שנענתה בחיוב בשנה.",
                ],
                "cite": _CITE_LIMIT,
            },
            {
                "title": "איך מגישים את הבקשה",
                "lines": [
                    "מגישים עד 30 יום לפני מועד תחילת השמ\"פ — בקשה מאוחרת נדונה רק באישור חריג.",
                    "טופס הבקשה (נספח א' לפקודה) מוגש דרך אתר המילואים או משרד הקישור ביחידה.",
                    "עצמאי מצרף תעודת עוסק מורשה; שכיר — הבקשה מנומקת וחתומה בידי המעסיק.",
                    "הסמכות: דחייה של עד שלושה חודשים לבקשה משקית, קיצור או פיצול של השמ\"פ.",
                ],
                "cite": _CITE_SUBMIT,
            },
            _AFTER,
            _APPEAL,
        ],
        "note": None,
    },
    "studies": {
        "steps": [
            {
                "title": "האם הבקשה שלך בסמכות הוועדה?",
                "lines": [
                    "הוועדה דנה בשמ\"פ של שישה ימים ומעלה (בקשות קצרות יותר — מול היחידה).",
                    "עילת לימודים: תואר ראשון ושני, הנדסאים וטכנאים, מכינות, ובחינות הסמכה "
                    "חד־שנתיות (עריכת דין, חשבונאות ועוד).",
                    "מותרות עד שתי בקשות שנענו בחיוב בשנה אקדמית.",
                ],
                "cite": _CITE_LIMIT,
            },
            {
                "title": "איך מגישים את הבקשה",
                "lines": [
                    "מגישים עד 30 יום לפני מועד תחילת השמ\"פ — בקשה מאוחרת נדונה רק באישור חריג.",
                    "טופס הבקשה (נספח א' לפקודה) מוגש דרך אתר המילואים או משרד הקישור ביחידה.",
                    "מצרפים אישור לימודים, ולבחינות — את לוח הבחינות בתקופת השמ\"פ.",
                    "הסמכות: דחייה של עד ארבעה חודשים לבקשת סטודנט, קיצור או פיצול של השמ\"פ.",
                ],
                "cite": _CITE_SUBMIT,
            },
            _AFTER,
            _APPEAL,
        ],
        "note": None,
    },
    "personal": {
        "steps": [
            {
                "title": "חשוב: זה לא מסלול הוולת\"ם",
                "lines": [
                    "בקשות מסיבות אישיות או רפואיות בעת שגרה אינן נדונות בוולת\"ם.",
                    "בשגרה — פונים למשרד הקישור או למפקד היחידה, שמוסמכים להקל במועדי השירות.",
                ],
                "cite": _CITE_SCOPE,
            },
            {
                "title": "איך מגישים את הבקשה",
                "lines": [
                    "בשגרה: פנייה מנומקת ליחידה, בצירוף מסמכים רפואיים או משפחתיים רלוונטיים.",
                    "בשעת חירום מוכרז: פונים לוועדה לתיאום שירות מילואים אישי (ולת\"ם פרט) "
                    "דרך אתר המילואים.",
                ],
                "cite": f"לפי {_DOC_PERSONAL}",
            },
            {
                "title": "מה קורה אחרי ההגשה",
                "lines": [
                    "ביחידה: ההחלטה בסמכות גורמי כוח האדם והמפקד, לפי נסיבות הבקשה.",
                    "בוועדת פרט בחירום: הבקשה נדונה בוועדה, וההחלטה מחייבת צו מתוקן מהיחידה.",
                ],
                "cite": f"לפי {_DOC_PERSONAL}",
            },
            {
                "title": "נדחיתי — מה עכשיו?",
                "lines": [
                    "על החלטת ועדת פרט ניתן לערער בהתאם למסלול הקבוע בפקודתה.",
                    "בכל מקרה — עד קבלת צו מתוקן, הצו המקורי בתוקף.",
                ],
                "cite": f"לפי {_DOC_PERSONAL}",
            },
        ],
        "note": "המסלול האישי שונה: בשגרה הוא עובר דרך היחידה ולא דרך הוולת\"ם.",
    },
}


def guide_for(cause_key: str) -> dict:
    """The 4-step guide for `cause_key` — {"steps": [...], "note": str | None}."""
    return _GUIDES[cause_key]
