# -*- coding: utf-8 -*-
"""out_of_scope: the referral strip for questions no order answers.

The questions below are verbatim from the 23.08.2026 measurement — the 17 that
`night/report_goal.py` credited as honestly unanswerable and that told the
soldier nothing about where to go. Pinning the real prose (not paraphrases I
would write to match my own regexes) is the whole point: a classifier tuned on
invented phrasing passes its tests and misses the soldier."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import out_of_scope as OS

# מזהה -> (השאלה כלשונה, המשפחה הצפויה או None)
MEASURED = {
    "q00005": ("אשכול שלי אמר שיש לי זכות לסל מזון, איפה אני מוציא את זה?",
               "lone_soldier_aid"),
    "q00024": ("אני מסיימת את הקורס בגדנית בעוד שבועיים אבל יחידתי החדשה צריכה "
               "אותי עכשיו, מה קורה?", "training_framework"),
    "q00026": ("יש לי חוק שעתות לימוד בשבוע בגדנית, אם הוצאו אותי למטלה במהלך "
               "זה הם חייבים להחזיר לי?", "training_framework"),
    "q00130": ('קיבלתי תלוש שכר ובו שורה שאני לא מבין - "הנחה מטעם', "pay_slip"),
    "q00105": ("יכול להיות שמעבירים אותי ליחידה אחרת בלי שאיתי את הדעה, מה זה "
               "חוקי?", "placement_transfer"),
    "q00192": ("אני קצינת משאבי אנוש בגדוד. החייל שלי קיבל הודעה שמעבירים אותו "
               "ליחידה אחרת בלי שאני ידעתי מראש. זה כשר?", "placement_transfer"),
    "q00085": ("קיבלתי משימה שלא בטוח שאני יכול לעשות אותה בחוקי. צריך לביצע או "
               "אני יכול להסכים?", "unlawful_order"),
    "q00011": ("מה עם החופשה שלא נטלתי בשנה הזו? האם אני יכול להמיר אותה לכסף "
               "כשאני משתחרר?", "leave_redemption"),
    # אלה נשארות בלי יעד בכוונה — ראו _UNMATCHED במודול
    "q00023": ("שלחתי לחייל הנחיה טלוגרם - אם הוא אומר שהוא לא ראה אותה, אני "
               "בבעיה?", None),
    "q00025": ("זה נכון שמפקדי כיתה חדשים מקבלים שנת התאקלמות או זה בדיוני?", None),
    "q00027": ("לא רשום לי כלום בחומה הלוח הכחול, פשוט הציבור אמרו בהפסקה שאני "
               "מתחילה באגף מחר, זה חוקי?", None),
    "q00090": ("חייל שלי זקוק לעזרה דחופה. האם אני יכול להשאיר את הפלוגה עם סגן "
               "וזה תקין?", None),
    "q00345": ("כמה ימי חופש אני צריך לתת לחייל שנפצע בתרגיל ונשלח לשיקום? יש לי "
               "הנחיה כזו?", None),
}


# מדידת 24.08 — 30 שאלות מילואים/משפט עיוורות, מזווגות. ⚠ ראיה חלשה יותר מ-
# MEASURED: אלה אפסים מדודים ולא לא-נענות שהבוררות אישרה. מוחזק בנפרד כדי
# שההבחנה תישאר גלויה למי שיקרא את זה בעוד חודש.
MEASURED_NEWSRC = {
    "q00234": ("התחלתי מילואים באפריל סיימתי בנובמבר. כמה זמן לוקח עד שהמשכורת "
               "מופיעה בחשבון", "reserve_pay"),
    "q00236": ("פיקדון מילואים — זה כסף שהם החזיקו מחשבוני או משהו שקיבלתי?",
               "reserve_pay"),
    "q00239": ("יש לי שני ילדים ואני היחיד שמחזיק. מגיע לי משהו נוסף בגלל זה "
               "בתקופת המילואים?", "reserve_pay"),
    "q00438": ("מילואים אחרון שלי היה לפני חודשים וההחזר הוצאות עדיין לא הגיע, "
               "כמה זמן זה אמור לקחת?", "reserve_pay"),
    "q00168": ("אני בצו 8 עכשיו ובא לי טיפול שיניים דחוף. האם זה בחינם או אני "
               "משלם?", "reserve_medical"),
    "q00173": ("בחור שלי במילואים. הוא אומר שהוא לא יכול להגיע לבדיקה בבי״ח "
               "בגלל משכנתא וכל החיים.", "reserve_medical"),
    "q00104": ("אחרי 8 חודשים מילואים אני צריך להחזיר ציוד אבל חלק מהדברים "
               "התבלו בשימוש נורמלי, אני אשם על זה?", "equipment_return"),
    "q00141": ("הגעתי להיום הראשון אחרי החזרה מצו 8 והם אומרים יש לי תפקיד חדש "
               "לחלוטין. הם יכולים ככה בלי שום הודעה מראש?", "placement_transfer"),
    # דלת-החופשות ולא דלת-הכסף, למרות „זכאי" ו„צו 8"
    "q00140": ("קראתי בפקודות שזכאי לחופשה בסוף צו 8 בהנחיה מיוחדת. איפה מבקשים "
               "את זה?", "leave_redemption"),
    # ⚠ שתי רגרסיות אמיתיות שנתפסו ב-24.08: „קורס" לבדו שלח שאלות-שחרור אל
    # מפקד-הקורס. שתיהן חייבות להישאר בלי דלת.
    "q00138": ("אחרי שעשיתי צו 8 נאמר לי שצריך להתנות שחרור בקורס מסדר ראשון. "
               "מה הקשר?", None),
    "q00143": ("הקמנדנט הוציא הודעה שמילואימנים בצו 8 חייבים לעבוד גם בימי "
               "שישי. זה חדש או זה תמיד היה?", None),
    "q00360": ("חייל מתחת לפקודתי מסרב לבצע משימה שקצה לו בנימוק שהוא עייף. "
               "מה אני יכול לעשות", None),
}


# ⚠ 26.08: `None` in the tables above used to mean "family_of returns None".
# The last-resort family (`unit_level_default`, added by the user's decision —
# see tests/test_out_of_scope_default.py) means every question now lands
# somewhere, so `None` was re-read as what it always actually guarded: **no
# SPECIFIC family claims this question**. Inventing a specific address is the
# failure mode this file exists to catch; falling through to a procedure is not.
# The assertion below is therefore stricter than a relaxed `!=`: it names the
# exact family that is allowed to catch an unclaimed question.
LAST_RESORT = "unit_level_default"


def test_every_measured_question_lands_where_the_table_says():
    for qid, (question, expected) in {**MEASURED, **MEASURED_NEWSRC}.items():
        got = OS.family_of(question)
        want = LAST_RESORT if expected is None else expected
        assert got == want, f"{qid}: got {got!r}, expected {want!r}"


def test_a_family_returns_a_door_and_a_reason():
    d = OS.destination_for(MEASURED["q00005"][0])
    assert d and d["label"] and d["where"] and d["why"], d
    # q00023 has no specific family and now reaches the last resort, which
    # points at a procedure and names no office — asserted in full in
    # tests/test_out_of_scope_default.py.
    fallback = OS.destination_for(MEASURED["q00023"][0])
    assert fallback and OS.family_of(MEASURED["q00023"][0]) == LAST_RESORT


def test_no_specific_door_is_better_than_a_wrong_one():
    """A question with no family keyword must not be handed the nearest-looking
    SPECIFIC destination. Since 26.08 it reaches the last resort instead, which
    names a procedure and no office — the distinction this test always guarded.
    Every one of these is in fact regulated somewhere, so in production the
    strip never fires on them at all: it is gated on the ANSWER having admitted
    no rule, and `destination_for` alone is only half that gate."""
    for q in ("מותר להכניס נרגילה לבסיס?",
              # ⚠ נתפס ב-24.08 כדלת שגויה: „תורנות" לבדו שלח שאלת-תוצאה
              # משמעתית אל השלישות. זו שאלה על עונש, לא על מי מנהל את הסידור.
              "מה קורה אם איחרתי לתורנות במטבח?",
              "כמה זמן נמשך מסדר בוקר?"):
        assert OS.family_of(q) == LAST_RESORT, f"{q} -> {OS.family_of(q)}"


def test_an_empty_question_gets_nothing_at_all():
    """The last resort matches any character; no character means no door."""
    assert OS.destination_for("") is None
    assert OS.destination_for("   ") is None


def test_the_table_refers_and_never_rules():
    """scope_routes' iron rule, enforced here too: 'this is handled at ___',
    never 'you are entitled to ___'. A normative claim with no cited order is
    exactly what this app does not do."""
    banned = ("מגיע לך", "אתה זכאי", "את זכאית", "מגיעים לך", "חובה עליהם")
    for _, _, dest in OS._FAMILIES:
        blob = f"{dest['label']} {dest['where']} {dest['why']}"
        for phrase in banned:
            assert phrase not in blob, (dest["label"], phrase)


def test_kol_zchut_is_an_information_link_and_never_the_authority():
    """The user's 23.08 decision: link, do not ingest — their CC-BY-NC-SA
    forbids commercial use and a link is not a derivative work. The authority
    named in `where` must stay a military or official body."""
    linked = [d for _, _, d in OS._FAMILIES if d["link"]]
    assert linked, "the food-grant family should carry the kol-zchut link"
    for d in linked:
        text, url = d["link"]
        assert url.startswith("https://www.kolzchut.org.il/"), url
        assert "כל-זכות" in text, text
        assert "כל-זכות" not in d["where"], (
            "kol-zchut is a place to read, not the door to knock on")


def test_evidence_and_families_stay_in_step():
    """Every TOPIC family cites the measured questions it came from, and no
    topic family exists that nothing was ever observed for.

    The last resort is exempt, and only it: it is not derived from a family of
    questions at all — it applies to whatever no family recognised, and its
    evidence is the order that defines the route (פ"מ 35.0822), not a sample.
    Keeping it in EVIDENCE with an empty list is deliberate, so the two
    structures still have to agree on the set of families."""
    families = {name for name, _, _ in OS._FAMILIES}
    assert families == set(OS.EVIDENCE), (families ^ set(OS.EVIDENCE))
    for name, ids in OS.EVIDENCE.items():
        if name == LAST_RESORT:
            assert not ids, "the last resort is not derived from a sample"
            continue
        assert ids, name
        for qid in ids:
            assert qid not in OS._UNMATCHED, f"{qid} is claimed by {name} and unmatched"


def test_unmatched_ids_reach_no_specific_family():
    """The guard that matters: no topic family may claim them by accident."""
    for qid in OS._UNMATCHED:
        if qid in MEASURED:
            assert OS.family_of(MEASURED[qid][0]) == LAST_RESORT, qid


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
    print("all out-of-scope referral tests passed")
