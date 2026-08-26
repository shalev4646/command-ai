# -*- coding: utf-8 -*-
r"""out_of_scope: clinical and health-scope questions, for conscripts too.

`reserve_medical` requires a reserves context in the question itself
(`(?=[\s\S]*(?:מילואים|מילואימניק|צו\s*8))`), which is right for its own door —
a reservist's route runs through his unit doctor and the medical committee. But
it leaves every conscript's medical question with no door at all.

The mini-pilot 150 adjudication (2026-08-26) turned up five, each written up as
NO_SUCH_RULE or NOT_IN_CORPUS with a reason:

    q00148  "יש לי פצע קטן שלא מתחזק טוב, צריך להחליף קומפלקס..."  — clinical
    q00068  "כמה זמן בערך מחכים לתוצאות בדיקת הדם שעשו לי אתמול?"  — clinical
    q00037  glasses          q00038  free medicines   — scope of the benefit
    q00170  "איפה פותחים תיק רפואי ומי צריך להחתים?"                — record-keeping

None is a gap an order could fill: GHQ orders do not say how to dress a wound,
how long a lab takes, or which optical aids are covered. פ"מ 61.0104 says who
decides instead, in its own words — the treating medical authority runs the
care ("בהתאם להוראת קצין רפואה ולהוראות חיל הרפואה"), the soldier away from his
unit goes to the regional clinic ("חייל השוהה מחוץ ליחידתו וזקוק לטיפול רפואי
דחוף יפנה למרפאה אזורית"), and the SCOPE of the entitlement lives in הוראות
הקרפ"ר, which the order names and which is not a GHQ order at all.

So the door is a referral to a real address the corpus itself points at, and it
decides nothing normative — the module's rule.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import out_of_scope as OS

WOUND = "יש לי פצע קטן שלא מתחזק טוב, צריך להחליף קומפלקס או יש משהו אחר שאני יכול לעשות בעצמי?"
LAB = "כמה זמן בערך מחכים לתוצאות בדיקת הדם שעשו לי אתמול?"
GLASSES = "יש לי בעיית בחזון בפתאום וצריך משקפיים חדשים. זה הצה\"ל משלם או אני חייב לעשות את זה בעצמי?"
# Control: a reservist's medical question must keep its own, narrower door.
RESERVE = "אני במילואים וצריך לגשת לרופא היחידה בגלל כאבים בגב, איך זה עובד?"
# Control: discipline is regulated — the strip must not turn it into a clinic
# referral just because the word "בדיקה" appears.
DISCIPLINE = "קצין השיפוט הטיל עליי מחבוש, אפשר לערער על זה?"


def test_conscript_clinical_questions_get_a_medical_door():
    for q in (WOUND, LAB, GLASSES):
        dest = OS.destination_for(q)
        assert dest is not None, f"no door for a conscript medical question: {q[:45]}"
        assert "רפוא" in dest["where"] or "מרפא" in dest["where"], (
            f"door is not a medical one: {dest['where'][:60]}"
        )


def test_reserve_medical_keeps_its_own_door():
    assert OS.family_of(RESERVE) == "reserve_medical"


def test_discipline_is_not_a_medical_question():
    assert OS.family_of(DISCIPLINE) != "medical_scope"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
    print("all medical-door tests passed")
