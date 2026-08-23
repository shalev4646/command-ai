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


def test_every_measured_question_lands_where_the_table_says():
    for qid, (question, expected) in MEASURED.items():
        assert OS.family_of(question) == expected, (
            f"{qid}: got {OS.family_of(question)!r}, expected {expected!r}")


def test_a_family_returns_a_door_and_a_reason():
    d = OS.destination_for(MEASURED["q00005"][0])
    assert d and d["label"] and d["where"] and d["why"], d
    assert OS.destination_for(MEASURED["q00023"][0]) is None


def test_no_door_is_better_than_a_wrong_door():
    """Silence is the default. A question with no family keyword must not be
    handed the nearest-looking destination."""
    for q in ("מותר להכניס נרגילה לבסיס?",
              "מה קורה אם איחרתי לתורנות במטבח?",
              ""):
        assert OS.destination_for(q) is None, q


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
    """Every family cites the measured questions it came from, and no family
    exists that nothing was ever observed for."""
    families = {name for name, _, _ in OS._FAMILIES}
    assert families == set(OS.EVIDENCE), (families ^ set(OS.EVIDENCE))
    for name, ids in OS.EVIDENCE.items():
        assert ids, name
        for qid in ids:
            assert qid not in OS._UNMATCHED, f"{qid} is claimed by {name} and unmatched"


def test_unmatched_ids_really_are_unmatched():
    for qid in OS._UNMATCHED:
        if qid in MEASURED:
            assert OS.family_of(MEASURED[qid][0]) is None, qid


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
    print("all out-of-scope referral tests passed")
