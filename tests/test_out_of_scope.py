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


# 10.09.2026 — הסט האמיתי (adjudication_realstyle.json), שלא השתתף בכתיבת אף
# משפחה, חשף שתי דלתות שגויות; שתיהן NO_SUCH_RULE מבורר, ושתיהן חייבות להישאר
# בלי דלת ספציפית. q00158 נוספה כשומר: הרחבה שנשקלה לתיקון (`מעביר אות[יוה]`)
# הייתה שולחת אותה אל קצין השלישות — „מעביר אותו ליום חפצי" הוא שינוי מעמד
# רפואי, לא הצבה.
MEASURED_REALSTYLE = {
    "rs016": ("אמר שאני לא יכול לדבר עם חייל מיחידה אחרת. זה בסדר?", None),
    # 10.09 this was pinned to None: the only door that reached it then was the
    # reserve-money one, and that was wrong. 12.09 pay_slip's pattern reaches
    # it and names the right desk (ת"ש ושלישות מול מרכז התשלומים).
    "rs065": ("פחתו לי שקל מהמשכורת, מי בודק את זה?", "pay_slip"),
    "q00158": ("לחייל שלי יש ניתוח מתוכנן ביום כ' ואחרי זה הוא יצטרך שבועות. "
               "איך אני מעביר אותו ליום חפצי?", None),
    # 11.09 — NO_SUCH_RULE מבורר, הבוררות כתבה „נקבע ביחידה" על כל אחת
    "rs044": ("כמה שעות מותר להיות בחוץ בערב?", "unit_routine"),
    "rs056": ("מותר לי לצאת בשביל דברים אישיים אם אחזור עד הערב?", "unit_routine"),
    "rs038": ("כמה זמן לפני הכניסה צריך להיות בחזרה?", "unit_routine"),
    "rs011": ("אם מחרתיים יש מחוב, מותר לי ללכת היום בערב?", "unit_routine"),
    "rs068": ("צריך לומר לשומר איפה אני הולך?", "unit_routine"),
    # unit-level in the adjudication too, and deliberately NOT caught: no
    # hours-or-movement signal in the phrasing, and a pattern cut to one
    # question is the mirror trap the module warns about
    "rs061": ("מי קובע מתי משדרים את ההתרעה בבוקר?", None),
    # ⚡ 19.09: a fault at 03:00 is reported, not researched — `fault_report`.
    "rs003": ("איך מדווחים שיש בעיה בתאורה בגדר בשעה 3 בלילה?", "fault_report"),
    # 12.09 — שלב 3.2: the silences with an address, all adjudicated
    "rs043": ("כמה כסף אני אמור לשלוח הביתה?", "family_support_pay"),
    "rs047": ("למה לא קיבלתי את התוספת של החודש הזה?", "pay_slip"),
    "rs027": ("האם אפשר לבקש השמטה מהמסדר הסיום?", "training_framework"),
    "rs022": ("אפשר לבקש כסף עבור ביגוד שקרע?", "quartermaster_issue"),
    "rs041": ("כמה ימים חופש חולים מגיע לי על שפעת?", "medical_scope"),
    "rs004": ("אם אני בבסיס איך אני מתקשר עם המשפחה?", "unit_routine"),
    # ⚡ 19.09 — these two got doors, and the note above them was the reason it
    # took so long: "the only patterns that reach them are cut to the one
    # question, the mirror trap". That is still the honest risk, and it is now
    # measured rather than feared — `night/doorgate.py` proves zero captures on
    # 388 answered questions, and reports that the held-out set of 51
    # adjudicated no-rule questions yields **zero** captures too. Zero captures
    # there is not safety, it is silence: no evidence these generalise beyond
    # the phrasing they were written on. What justifies shipping them anyway is
    # that both send the soldier somewhere the generic door does not — a school
    # and a pay desk — which is the whole criterion (night/DOORS_CRITERION.md).
    "rs049": ("מגיע לי כסף על שעות נוספות או לא?", "pay_entitlement"),
    "rs053": ("מותר לי להוציא את הבן שלי מבית ספר לביקור?", "civil_school"),
}

# pilot-150 rows (2026-08-25 adjudication) that 12.09 gave a door
MEASURED_PILOT150 = {
    "q00071": ("אמרו לי שחייב להשלים קורס רכיבה אבל אני לא רוצה. יש מישהו שאני יכול "
               "לבקש ממנו להוציא אותי מזה?", "training_framework"),
    "q00028": ("כמה זמן מינימום צריך להישאר ביחידה שלך לפני שמעבירים אותך לשום מקום?",
               "placement_transfer"),
    "q00062": ("קרה לי סיטואציה שחייל שלי מחזיק במסמכים שלו אצלו כי הוא עובר יחידה - "
               "זה בסדר? למי צריך לחזור?", "placement_transfer"),
}

# Answered questions that sit next to the 12.09 patterns: each names the door
# it must NOT get. An order answers every one of them (adjudicated or graded),
# so a door there is a wrong door.
NEAR_MISSES = {
    "q00200": ("שלוש שנים שלא קיבלתי תוספת סיום כמו שאמרו לי בגיוס, זה מגיע לי או לא?", "pay_slip"),
    "q00318": ("הצעתי לכאבים בשיניים והשלחו אותי לרופא. האם זה יוצא מהמשכורת שלי או הצבא משלם?", "pay_slip"),
    "q00139": ("המפקד שלי אמר שאני לא זכאי לימי מחלה בחודשיים הסיום שלי. זה לא נכון בטח?", "medical_scope"),
    "q00122": ("בן שלי התאונן וצריך להוציא אותו מבית הספר. האם זה נחשב חירום מספיק כדי לצאת?", "family_support_pay"),
    "q00102": ("מה כוללת חבילת השחרור? ביטוח, כסף, קורסים?", "training_framework"),
    "dress": ("מותר ללבוש ביגוד אזרחי בדרך הביתה מהבסיס?", "quartermaster_issue"),
}

# Questions an ORDER answers that sound like unit routine. Each is a measured
# false positive of the 11.09 prototype or its nearest neighbour; the family
# must stay silent on every one of them — a "your unit settles this" door on a
# question פ"מ 33.0213 or פ"מ 35.0402 answers is worse than no door.
UNIT_ROUTINE_NEGATIVES = {
    "real013": "האם למפקד שלי מותר להעיר אותי בשלוש בבוקר לבצע משימה של 20 דקות ולחזור לישון?",
    "real014": "מותר למפקד שלי להעיר אותי ב2 בלילה לעשות מסדר?",
    "q00115": "מצב חירום בבית - הורה חולה ודחוף, כמה זמן אני יכול להיות בחוץ בלי לחזור למחנה?",
    "rs059": "מי אני צריך לבדוק איתו לפני שאני יוצא?",
    "q00008": "אני משתחרר בעוד חודש, מה אני צריך לבקש מהמחלקה האדמיניסטרטיבית לפני שאני יוצא?",
    "rs045": "כמה שעות שינה מגיעות לי בלילה כשיש שמירה?",
    # the guard is anchored: a leave word ANYWHERE in the question silences
    # the family, not only before the match
    "guard": "לפני החופשה כמה שעות מותר להיות בחוץ בערב?",
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

# ⚡ 19.09: the last resort split in two (see night/DOORS_CRITERION.md). The
# confident half kept the name and the daily-routine topics; the honest half,
# `not_in_our_orders`, took everything else and asserts nothing about where the
# rule lives. `None` in the tables above still means exactly what it meant —
# **no SPECIFIC family claims this question** — so it is now satisfied by
# either half. What the assertion still forbids is the thing this file exists
# to catch: an unclaimed question being handed a specific, invented address.
LAST_RESORTS = ("unit_level_default", "not_in_our_orders")


def test_every_measured_question_lands_where_the_table_says():
    for qid, (question, expected) in {**MEASURED, **MEASURED_NEWSRC,
                                      **MEASURED_REALSTYLE, **MEASURED_PILOT150}.items():
        got = OS.family_of(question)
        if expected is None:
            assert got in LAST_RESORTS, f"{qid}: got {got!r}, expected a catch-all"
        else:
            assert got == expected, f"{qid}: got {got!r}, expected {expected!r}"


def test_a_family_returns_a_door_and_a_reason():
    d = OS.destination_for(MEASURED["q00005"][0])
    assert d and d["label"] and d["where"] and d["why"], d
    # q00023 has no specific family and now reaches the last resort, which
    # points at a procedure and names no office — asserted in full in
    # tests/test_out_of_scope_default.py.
    fallback = OS.destination_for(MEASURED["q00023"][0])
    assert fallback and OS.family_of(MEASURED["q00023"][0]) in LAST_RESORTS


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
        assert OS.family_of(q) in LAST_RESORTS, f"{q} -> {OS.family_of(q)}"


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


def test_curated_kol_zchut_links_cover_the_measured_families():
    """30.08.2026: three links curated from the 606-title pull of their
    צבא-וביטחון category, via the MediaWiki API Kol Zchut themselves pointed
    at when they declined the NC waiver (titles only, zero content). Each page
    was verified live (HTTP 200) on the day it was added. Families whose
    questions no single page serves stay linkless on purpose."""
    by_name = {name: d for name, _, d in OS._FAMILIES}
    for family, page in [
        ("lone_soldier_aid", "מענקי_מזון_לחיילים_בודדים"),
        ("reserve_pay", "תשלום_עבור_שירות_מילואים"),
        ("family_distress", 'תשמ"ש'),
        ("family_support_pay", 'תשמ"ש'),
        ("unit_routine", "פנייה_לנציב_קבילות_החיילים"),
        ("unit_level_default", "פנייה_לנציב_קבילות_החיילים"),
    ]:
        link = by_name[family]["link"]
        assert link is not None, family
        assert page in link[1], (family, link[1])
    # pay_slip and medical_scope are exactly the FOI wave-4 gaps: Kol Zchut
    # has no page for a serving soldier's pay slip or routine medical care,
    # and a wrong-audience page must not be linked in their place.
    assert by_name["pay_slip"]["link"] is None
    assert by_name["medical_scope"]["link"] is None


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
        if name in LAST_RESORTS:
            assert not ids, "a catch-all is not derived from a sample"
            continue
        assert ids, name
        for qid in ids:
            assert qid not in OS._UNMATCHED, f"{qid} is claimed by {name} and unmatched"


def test_the_new_doors_stay_off_their_answered_neighbours():
    for qid, (q, door) in NEAR_MISSES.items():
        assert OS.family_of(q) != door, (qid, OS.family_of(q))


def test_family_support_pay_cites_the_family_payments_order():
    d = OS.destination_for(MEASURED_REALSTYLE["rs043"][0])
    assert d and "35.0210" in d["where"] and "35.0210" in d["why"]
    assert d["link"] and 'תשמ"ש' in d["link"][1]


def test_unit_routine_stays_silent_where_an_order_answers():
    for qid, q in UNIT_ROUTINE_NEGATIVES.items():
        assert OS.family_of(q) != "unit_routine", (qid, q)


def test_unit_routine_never_claims_a_question_the_arms_answered():
    """דיוק לפני כיסוי, על הנתונים השמורים: כל שאלה שדורגה כנענתה (חלק אחד
    לפחות) בזרועות שבריפו — ולא בוררה NO_SUCH_RULE — המשפחה שותקת עליה.
    11.09: 0 תפיסות על 256 כאלה; הבדיקה מחזיקה את זה כשהדפוס יורחב."""
    import json
    out = Path(__file__).resolve().parents[1] / "night" / "out"
    no_rule = set()
    for name in ("adjudication_pilot150.json", "adjudication_realstyle.json"):
        p = out / name
        if p.exists():
            no_rule |= {r["question"].strip() for r in json.loads(p.read_text(encoding="utf-8"))
                        if r.get("verdict") == "NO_SUCH_RULE"}
    checked = 0
    for name in ("grades_grade-second4.jsonl", "grades_pilot150.jsonl", "grades_real24.jsonl"):
        p = out / name
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            q = (r.get("clean_q") or r.get("q") or "").strip()
            if not q or q in no_rule or not int((r.get("grade") or {}).get("answered_parts") or 0):
                continue
            checked += 1
            assert OS.family_of(q) != "unit_routine", (r.get("id"), q[:80])
    assert checked >= 80, f"only {checked} answered questions on disk — the guard has nothing to hold"


def test_unit_routine_cites_the_orders_that_delegate_and_rules_nothing():
    d = OS.destination_for(MEASURED_REALSTYLE["rs044"][0])
    assert d and d["label"].startswith("נקבע ביחידה שלך")
    for cite in ("33.0401", "33.0202", "61.0104", "פקודות הקבע של היחידה"):
        assert cite in d["why"], cite
    assert "35.0822" in d["where"] and "33.0336" in d["where"]


def test_unmatched_ids_reach_no_specific_family():
    """The guard that matters: no topic family may claim them by accident."""
    for qid in OS._UNMATCHED:
        if qid in MEASURED:
            assert OS.family_of(MEASURED[qid][0]) in LAST_RESORTS, qid


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
    print("all out-of-scope referral tests passed")
