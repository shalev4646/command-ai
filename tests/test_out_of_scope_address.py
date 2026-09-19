# -*- coding: utf-8 -*-
r"""out_of_scope: the three doors whose ADDRESS is genuinely different.

Written 19.09.2026 against `night/DOORS_CRITERION.md`, which was written first.

WHY SO FEW. The worklist in `STATE.md` named eleven ruler questions that reach
only the catch-all, and called closing them a +15-part lever. Before writing a
line of regex I compared the two doors a soldier can actually land on:

    out_of_scope._FAMILIES["unit_routine"]["where"]
    out_of_scope._FAMILIES["unit_level_default"]["where"]   ->  IDENTICAL

`label` and `why` differ; the address does not. So for eight of the eleven, a
new "verified family" would move `report_goal.served` by fifteen parts and send
the soldier to the very same counter. The user's decision on 19.09 was to build
only the doors that change the address, and to fix the catch-all's false claim.

THE THREE. Each names a counter the generic door does not:
  civil_school     rs053 — a child's school is a civilian matter. The unit does
                   not rule on it, and "ask your שליש" is not merely useless
                   here, it is the wrong building.
  fault_report     rs003 — a fault in the perimeter at 03:00 is reported, not
                   researched. Asking to see the standing orders at three in the
                   morning is the wrong verb.
  pay_entitlement  rs049 — overtime. פ"מ 35.0201 puts the pay scale with the
                   Chief of Staff, so "your unit decides" is false, and the
                   desk that answers is the pay chain, not the base.

THE CATCH-ALL SPLIT. The generic door asserts "לא במטכ\"ל — בפקודות הקבע של
היחידה". For a wake-up time that is provably true and the door quotes the
orders that say so (33.0401, 21.0113, 61.0104). For a salary amount it is
false: 35.0201 has the Chief of Staff setting it, and we simply do not hold the
scale. A door that states a false locating claim is a wrong door by this
project's own rule, even though `served` cannot see it. So the catch-all splits
in two, and BOTH halves stay catch-alls in `night/report_goal.py` — the split
must not buy a single part.

Controls below guard every seam that matters: the seven genuinely unit-level
ruler questions must not be captured by the new families, and the three new
families must never fire on a question the orders do answer (that gate runs
over the whole adjudicated corpus in `night/doorgate.py`, not here).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import out_of_scope as OS

# The three targets, verbatim from the frozen ruler.
SCHOOL = "מותר לי להוציא את הבן שלי מבית ספר לביקור?"
FAULT = "איך מדווחים שיש בעיה בתאורה בגדר בשעה 3 בלילה?"
OVERTIME = "מגיע לי כסף על שעות נוספות או לא?"

# Genuinely unit-level — these must NOT be captured by the new families.
WAKEUP = "מי קובע מתי משדרים את ההתרעה בבוקר?"
LATE = "איחרתי ליום שלישי, מה המינימום שצריך להגיד למפקד?"
OTHER_UNIT = "אמר שאני לא יכול לדבר עם חייל מיחידה אחרת. זה בסדר?"
TWO_BOSSES = "הקפטן אומר אני ככה וסגן אומר אחרת, למי אשמע בלילה?"
REPORT_ERROR = "טעיתי בדוח, צריך לומר למישהו או פשוט תיקנתי?"
UNIT_LEVEL = (WAKEUP, LATE, OTHER_UNIT, TWO_BOSSES, REPORT_ERROR)

# Seams against families that already exist.
PAY_SLIP = "יש לי ניכוי בתלוש ואני לא מבין מי בודק את זה"
FAMILY_MONEY = "כמה כסף אני אמור לשלוח הביתה כל חודש?"
LONE_FOOD = "אני חייל בודד, איך מקבלים תווי מזון?"

# A topic where the catch-all's locating claim is false: the scale is GS-level.
SALARY = "כמה משכורת אמורה להיות לחייל בסדיר?"

# ⚡ Caught by `night/doorgate.py` on the first run, 19.09 — both were captures
# on questions the orders DO answer, i.e. wrong doors by this project's rule.
# The first prototype of each family was too wide by exactly one signal:
#   q00331 — a sick child and a closed daycare. It carries "הילד שלי" and "גן",
#     but the question is about the SOLDIER's half-day and his replacement, not
#     about the school. The fix: the family now requires a removal verb.
#   q00247 — overtime in a CIVILIAN job, disputed with a clerk. It carries
#     "שעות נוספות" and nothing else the old pattern needed. The fix: the
#     family now requires the soldier to be asking about his own entitlement.
SICK_CHILD_REPLACEMENT = ("הילד שלי חולה והגן סוגר אותו וואחד מתפקידי כאן "
                          "יחליף אותי בחצי היום? אני לא יודע מה לעשות")
CIVILIAN_OVERTIME = ("הפקיד אמר שהעסקתי בשעות נוספות לא תחשב כי זה הוצו לי "
                     "בעל כורחי. אבל אני רוצה לדעת מה הכללים")

_UNIT_CLAIM = "פקודות הקבע של היחידה"

# ⚡ 20.09 — the one widening the split earned, and the trap inside it.
# FINDING (the other session): rs036 asks how long a phone may be held during
# duty, and פ"מ 21.0113 — already cited in this module's evidence — hands the
# רט"ן arrangements to „פק\"ל שגרת המחנה". So the CONFIDENT wording is true for
# it, and the split was handing it the vague one. Fixable with a citation, not
# a feeling, which is the bar.
# TRAP (measured before accepting): the bare word „טלפון" pulls in four more
# questions that are NOT camp routine — confiscation as punishment and
# photography on base are discipline and information security, decided above
# the unit. Adding the bare word would have manufactured exactly the false
# locating claim the split exists to delete. Same signature as the two doorgate
# caught: one signal too few.
# ⇒ the phone term is admitted only WITH a duty signal beside it.
# ⚠ and „דיוט" may never be added alone: rs058 („לא ללבוש דיוט בעיר") carries
# it as uniform, not duty, and a bare term would drag the showcase question for
# the honest door straight back into the false one.
PHONE_ON_DUTY = "כמה זמן אמורים להחזיק את הטלפון בידיים בעת דיוטי?"
PHONE_PUNISHMENT = "מותר להחרים לי את הטלפון כעונש?"
PHONE_TAKEN = "המפקד לקח לי את הפלאפון, זה חוקי?"
PHONE_PHOTO = "צילמתי בטלפון בתוך הבסיס, מה הדין?"
DRESS_IN_TOWN = "מותר למפקד להגיד לי לא ללבוש דיוט בעיר?"


def test_a_phone_during_duty_is_camp_routine():
    """פ"מ 21.0113 delegates the רט"ן arrangements to the unit's standing
    orders, so here the confident wording is the true one."""
    assert OS.family_of(PHONE_ON_DUTY) == "unit_level_default"
    assert _UNIT_CLAIM in OS.destination_for(PHONE_ON_DUTY)["where"]


def test_a_phone_as_punishment_or_evidence_is_not_camp_routine():
    """Discipline and information security are decided above the unit. Telling
    a soldier his base decides these is the false claim the split deletes."""
    for q in (PHONE_PUNISHMENT, PHONE_TAKEN, PHONE_PHOTO):
        assert OS.family_of(q) == "not_in_our_orders", f"{q} -> {OS.family_of(q)}"
        assert _UNIT_CLAIM not in OS.destination_for(q)["where"], q


def test_dress_in_town_stays_on_the_honest_door():
    """rs058 carries „דיוט" as uniform, not duty. It is the sharpest example of
    the split working — the rule is a GS order we do not hold — and no widening
    may drag it back to „your unit decides"."""
    assert OS.family_of(DRESS_IN_TOWN) == "not_in_our_orders"


def test_school_question_reaches_a_civil_school_door():
    assert OS.family_of(SCHOOL) == "civil_school"
    dest = OS.destination_for(SCHOOL)
    assert "בית הספר" in dest["where"], dest["where"][:80]


def test_fault_at_night_reaches_a_reporting_route_not_a_reading_route():
    assert OS.family_of(FAULT) == "fault_report"
    dest = OS.destination_for(FAULT)
    assert "תורן" in dest["where"], dest["where"][:80]


def test_overtime_reaches_the_pay_chain():
    assert OS.family_of(OVERTIME) == "pay_entitlement"
    dest = OS.destination_for(OVERTIME)
    assert "שכר" in dest["where"] or "תשלומים" in dest["where"], dest["where"][:80]


def test_the_three_new_doors_never_claim_the_unit_decides():
    """The whole point of the three: a different address, not a new heading."""
    for q in (SCHOOL, FAULT, OVERTIME):
        dest = OS.destination_for(q)
        assert _UNIT_CLAIM not in dest["where"], f"{q[:30]} -> same old address"


def test_unit_level_questions_are_not_captured_by_the_new_families():
    new = {"civil_school", "fault_report", "pay_entitlement"}
    for q in UNIT_LEVEL:
        assert OS.family_of(q) not in new, f"new family swallowed {q[:40]}"


def test_a_sick_child_and_a_closed_daycare_is_not_a_school_question():
    """The soldier is asking about his own half-day, not about the school."""
    assert OS.family_of(SICK_CHILD_REPLACEMENT) != "civil_school"


def test_overtime_in_a_civilian_job_is_not_an_army_pay_question():
    """A dispute with a civilian clerk is not the army's pay desk."""
    assert OS.family_of(CIVILIAN_OVERTIME) != "pay_entitlement"


def test_existing_money_families_keep_their_questions():
    assert OS.family_of(PAY_SLIP) == "pay_slip"
    assert OS.family_of(FAMILY_MONEY) == "family_support_pay"
    assert OS.family_of(LONE_FOOD) == "lone_soldier_aid"


def test_routine_question_keeps_the_unit_standing_orders_claim():
    """Where the claim is provable, the door keeps saying it."""
    dest = OS.destination_for(WAKEUP)
    assert _UNIT_CLAIM in dest["where"], dest["where"][:80]


def test_a_gs_level_topic_is_not_told_that_its_unit_decides():
    """35.0201 puts the pay scale with the Chief of Staff. Saying the base sets
    it is a false locating claim — a wrong door, even if `served` cannot see."""
    fam = OS.family_of(SALARY)
    assert fam == "not_in_our_orders", fam
    dest = OS.destination_for(SALARY)
    assert _UNIT_CLAIM not in dest["where"], dest["where"][:80]


def test_both_catch_alls_are_still_catch_alls_for_the_metric():
    """The split must not buy a single part of `served`."""
    from night import report_goal
    assert "unit_level_default" in report_goal.DEFAULT_FAMILIES
    assert "not_in_our_orders" in report_goal.DEFAULT_FAMILIES


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
    print("all address-door tests passed")
