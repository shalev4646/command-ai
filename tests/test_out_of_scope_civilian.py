# -*- coding: utf-8 -*-
r"""out_of_scope: bodies that are not the army, and the quartermaster's counter.

Two more families from the mini-pilot 150 adjudication (2026-08-26), both
NOT_IN_CORPUS with a written reason, and both left doorless by the existing
table for the same reason `reserve_medical` failed conscripts: the patterns
that would catch them all sit behind a reserves lookahead.

CIVILIAN BODIES. `reserve_pay` already names ביטוח לאומי, but only inside its
reserves branch, so a discharged soldier asking where to fix his national-
insurance status (q00013) and a commander asking why a monthly supplement did
not arrive (q00128) match nothing. The corpus itself names the body — פ"מ
35.0206: "התגמול משולם ע"י המוסד לביטוח לאומי" — so pointing there is a
referral to an address the orders themselves use, not a guess.

QUARTERMASTER ISSUE. `equipment_return` fires on losing, damaging or returning
kit. It does not fire on drawing a replacement (q00066, worn-out socks) or on a
registration mismatch for kit already lent (q00185). The adjudication traced
both to the אט"ל issuing instructions and to פ"מ 52.0301 — neither in the
corpus, and 52.xx is not even published on the orders site — so the honest
answer is the unit counter that holds those instructions.

Controls below guard the seam: a reservist's pay question must keep its own
three-door answer, and returning kit must stay with equipment_return.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import out_of_scope as OS

NI_DISCHARGE = "אחרי שחרור, איפה אני הולך כדי לתקן דברים בביטוח הממלכתי או משהו ככה?"
NI_SUPPLEMENT = "למה לא קיבלתי את דמי ההשלמה שלי החודש? הכל היה תקין בתיקייה שלי"
SOCKS = "אני צריך להחליף גרביים כי שלי שלמו. אני יכול פשוט ללכת למחסן או צריך אישור?"
UNREGISTERED = "השאלתי ציוד מהחניכייה ועכשיו הם אומרים שזה לא רשום, מה עושים?"
# Controls
RESERVE_PAY = "אני במילואים, מתי מגיע לי התגמול ומי משלם אותו?"
RETURN_KIT = "אני משתחרר ואני צריך להחזיר ציוד, לאן הולכים?"


def test_civilian_body_questions_get_a_civilian_door():
    for q in (NI_DISCHARGE, NI_SUPPLEMENT):
        dest = OS.destination_for(q)
        assert dest is not None, f"no door for a civilian-body question: {q[:45]}"
        assert "לאומי" in dest["where"], f"door does not name the body: {dest['where'][:60]}"


def test_drawing_and_registering_kit_reaches_the_quartermaster():
    for q in (SOCKS, UNREGISTERED):
        dest = OS.destination_for(q)
        assert dest is not None, f"no door for a quartermaster question: {q[:45]}"
        assert "אפסנא" in dest["where"], f"door is not the counter: {dest['where'][:60]}"


def test_reserve_pay_keeps_its_three_door_answer():
    assert OS.family_of(RESERVE_PAY) == "reserve_pay"


def test_returning_kit_stays_with_equipment_return():
    assert OS.family_of(RETURN_KIT) == "equipment_return"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
    print("all civilian/quartermaster door tests passed")
