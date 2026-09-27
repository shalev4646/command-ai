# -*- coding: utf-8 -*-
"""The support gate in night.curate (support(), and check(title=...)) — 27.09.

As proposed in night/SUPPORT_AUDIT.md and decided by the manager: a fraction, rank or acronym the
order does not have ⇒ problem; a number outside its supporting passage ⇒ problem when the block's
digits are readable, warning when they are not; weak support ⇒ warning only; a page-read number
(a def's numbers_seen) is not counted. No network, no model; synthetic orders.

    venv\\Scripts\\python.exe tests\\test_support_gate.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from night.curate import check, support

CORPS = ["הנדסה קרבית", "תותחנים ושריון", "מודיעין שדה", "תקשוב ואלקטרוניקה", "לוגיסטיקה ותחזוקה",
         "רפואה צבאית", "משטרה צבאית", "חינוך ונוער", "הגנה אווירית", "ספנות ונמלים"]
RAW = ("28. לא נכנס החייל להתגורר בדיור הצבאי תוך 60 יום ממועד החלטת הוועדה לזכאותו, תפקע זכאותו, והוא יחויב "
       "בתשלום שכר דירה ותשלום 1/30 של משכורתו. 29. ראש עת\"ש קבע באכ\"א-פרט רשאי, במקרים חריגים, לאשר כניסה "
       "במועד מאוחר. 30. קצין בדרגת סרן או קָ ָא\"ּב (קצין אקדמאי בכיר) רשאי למנוע חופשה. "
       + " ".join(f"{40 + i}. לוחם במערך {c} זכאי ל-{11 + i} ימי השתלמות מקצועית בכל שנת עבודה." for i, c in enumerate(CORPS))
       + " 60. בחישוב הוותק יובאו בחשבון 45 ימי חופשה.")
GOOD = [f"לוחם במערך {c} זכאי ל-{11 + i} ימי השתלמות מקצועית בכל שנת עבודה." for i, c in enumerate(CORPS[:6])]


def _sec(*texts):
    return {"id": "key-facts", "clauses": [{"number": f"c{i}", "text": t} for i, t in enumerate(texts)]}


def test_clean_block_passes():
    p, _ = support(_sec(*GOOD, "לא נכנס החייל להתגורר בדיור הצבאי תוך 60 יום ממועד החלטת הוועדה, תפקע זכאותו."), RAW)
    assert p == [], p


def test_a_fraction_the_order_lacks_is_a_problem():
    p, _ = support(_sec(*GOOD, "לא נכנס החייל לדיור הצבאי, תפקע זכאותו ויחויב בתשלום שליש מן המשכורת."), RAW)
    assert any("שליש" in x for x in p), p


def test_an_acronym_the_order_lacks_is_a_problem_and_vowel_points_do_not_hide_one():
    p, _ = support(_sec(*GOOD, 'קצין בדרגת סרן או קא"ב (קצין אקדמאי בכיר) רשאי למנוע חופשה.'), RAW)
    assert p == [], p
    p, _ = support(_sec(*GOOD, 'במקרים חריגים רשאי רמ"ח פרט באכ"א לאשר כניסה במועד מאוחר.'), RAW)
    assert any('רמ"ח' in x for x in p), p


def test_a_number_outside_its_passage_is_a_problem_unless_read_on_the_page():
    bad = _sec(*GOOD, "לא נכנס החייל להתגורר בדיור הצבאי תוך 45 יום ממועד החלטת הוועדה, תפקע זכאותו.")
    p, _ = support(bad, RAW)
    assert any("45" in x for x in p), p
    p, _ = support(bad, RAW, seen={"c6": ["45"]})
    assert not any("45" in x for x in p), p


def test_scrambled_digits_make_it_a_warning():
    scrambled = RAW.replace("60 יום", "06 יום").replace(" 45 ימי", " 54 ימי")
    for i in range(11, 21):
        scrambled = scrambled.replace(f"ל-{i} ימי", f"ל-{str(i)[::-1]} ימי")
    p, w = support(_sec(*GOOD, "לא נכנס החייל להתגורר בדיור הצבאי תוך 60 יום ממועד החלטת הוועדה, תפקע זכאותו."), scrambled)
    assert not any("outside the passage" in x for x in p), p
    assert any("raw digits unreadable" in x for x in w), w



def test_an_order_reference_read_on_the_page_is_not_counted():
    # night.numbers skips „פ"מ 21.0101", the support gate counts it — numbers_seen must reach both
    from night.recurate.apply_defs import seen_numbers
    scrambled = RAW.replace("60 יום", "06 יום").replace(" 45 ימי", " 54 ימי")
    for i in range(11, 21):
        scrambled = scrambled.replace(f"ל-{i} ימי", f"ל-{str(i)[::-1]} ימי")
    text = 'לא נכנס החייל להתגורר בדיור הצבאי תוך 60 יום ממועד החלטת הוועדה, כאמור בפ"מ 21.0101.'
    sec = _sec(*GOOD, text)
    _, w = support(sec, scrambled)
    assert any("21.0101" in x for x in w), "not read on the page ⇒ still a warning"
    seen = seen_numbers({"clauses": [{"number": "c6", "text": text, "numbers_seen": ["60", "21.0101"]}]})
    _, w = support(sec, scrambled, seen=seen)
    assert not any("21.0101" in x for x in w), (seen, w)

def test_weak_support_is_only_a_warning():
    p, w = support(_sec(*GOOD, "הדגל יונף בכל יום לפני שעת ההשכמה ויורד לפני השקיעה בכל יחידה."), RAW)
    assert p == [] and any("no supporting passage" in x for x in w), (p, w)


def test_check_runs_the_gate_only_for_a_new_block():
    sec = _sec(*GOOD, "לא נכנס החייל לדיור הצבאי, תפקע זכאותו ויחויב בתשלום שליש מן המשכורת.")
    p0, _ = check(sec, RAW)
    assert not any("שליש" in x for x in p0), "without title: the old check"
    p1, _ = check(sec, RAW, title="דיור צבאי")
    assert any("שליש" in x for x in p1), p1


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as e:
                fails += 1
                print(f"FAIL {name}: {str(e)[:400]}")
    raise SystemExit(1 if fails else 0)
