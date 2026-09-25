# -*- coding: utf-8 -*-
"""The family-payments door (out_of_scope.family_support_pay) owns the acronym
תשמ"ש — quoted, bare and with prefixes — since 26.09: in a live question
(„איך מגישים בקשה לתשמש?") the strip showed the right address (מדור ת"ש) with
the lone-soldier family's reasoning (food grants), because the acronym sat in
lone_soldier_aid and that family is checked first. The lone-soldier door keeps
its own questions; the verb תשמשו is not the acronym.

    venv\\Scripts\\python.exe tests\\test_door_family_payments.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import out_of_scope as OS

FAMILY_PAY = [
    "איך מגישים בקשה לתשמש?",                       # the live question, no gershayim
    'איך מגישים בקשה לתשמ"ש?',
    'מי מאשר תשמ"ש וכמה זמן זה לוקח?',             # the gap the answer declared
    "מגיע לי תשמש אם ההורים שלי לא עובדים?",
    'בתשמ״ש מקבלים כל חודש?',                       # Hebrew gershayim, prefix
    "כמה כסף אני אמור לשלוח הביתה?",                # rs043 — the family's own evidence
]
LONE_SOLDIER = [
    "אשכול שלי אמר שיש לי זכות לסל מזון, איפה אני מוציא את זה?",   # q00005 — its evidence
    "אני חייל בודד, מגיע לי סיוע כלכלי?",
    "איפה מקבלים תווי מזון?",
]
NOT_THE_ACRONYM = ["תשמשו בזהירות בנשק", "המילה תשמשת לא קיימת", "השתמשתי בנשק"]


def test_the_acronym_routes_to_the_family_payments_door():
    for q in FAMILY_PAY:
        assert OS.family_of(q) == "family_support_pay", (q, OS.family_of(q))


def test_the_door_names_the_order_the_channel_and_the_open_question():
    d = OS.destination_for("איך מגישים בקשה לתשמש?")
    assert d and "35.0210" in d["where"] and "35.0210" in d["why"]
    assert "טופס 60" in d["where"] and 'ת"ש' in d["where"]
    assert "מי מאשר" in d["why"], "the reasoning must speak to the declared gap, not to food grants"
    assert "מזון" not in d["why"] and "בודד" not in d["why"]
    assert d["link"] and 'תשמ"ש' in d["link"][1]


def test_the_lone_soldier_door_keeps_its_own_questions():
    for q in LONE_SOLDIER:
        assert OS.family_of(q) == "lone_soldier_aid", (q, OS.family_of(q))
    assert "מזון" in OS.destination_for(LONE_SOLDIER[0])["link"][1]


def test_the_verb_is_not_the_acronym():
    for q in NOT_THE_ACRONYM:
        assert OS.family_of(q) != "family_support_pay", (q, OS.family_of(q))


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as e:
                fails += 1
                print(f"FAIL {name}: {e}")
    raise SystemExit(1 if fails else 0)
