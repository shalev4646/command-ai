# -*- coding: utf-8 -*-
r"""curate.clause_numbers_in_raw: a marker whose period touches the next word.

Found 2026-08-27 while trying to deepen הק"א 33-05-01 (הופעה ולבוש). The block
is 108 words against a 32,870-character source, and its title promises "שיער,
תספורת, זקן ותכשיטים" while the block covers none of the four — the clearest
content gap in the corpus. The deepening was REJECTED:

    clause 'אילו תכשיטים מותר לי לענוד כחייל (זכר)': cites [106], absent from raw_text

Clause 106 is in the source. The gate could not see it, and 96, 98, 99, 107 and
108 in the same order it saw fine. The difference is one character:

    96  -> "96\n. \n:חייל"     period, then a space      -> matched
    107 -> "107\n.  \n חיי"    period, then spaces       -> matched
    106 -> "106\n  .חייל"      period, then ח            -> MISSED

`(?<!\d)(\d{1,3})\s*\.\s` demands whitespace after the period, and the RTL
extraction sometimes leaves the period glued to the word that follows. So the
model cited correctly, the gate called it invention, and the order stayed
uncurated at full price — the same failure `is_numbered` was written to prevent
one level up, surviving at the level below it.

Widening the tail to "whitespace OR a Hebrew letter" keeps the shape the gate
actually checks (a number, then a period, at a clause boundary) and cannot
match a measurement like "3 ס"מ" — there the period is absent entirely.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from night.curate import clause_numbers_in_raw

GLUED = "עדיים - תכשיטים:\n\n106\n  .חייל ,שמינו זכר הלובש מדים, אינו רשאי לענוד"
SPACED = "תספורת וגילוח:\n\n96\n. \n:חייל שמינו זכר"
MEASUREMENT = 'שרשרת אחת בלבד לצוואר עשויה מתכת, שקוטרה3 מ"מ, לכל היותר'
DECIMAL = "לפי סעיף 2א ובכפוף ל-1.5 ס\"מ ברוחב"


def test_a_marker_whose_period_touches_the_next_word_is_still_a_marker():
    assert 106 in clause_numbers_in_raw(GLUED)


def test_the_ordinary_spaced_marker_still_matches():
    assert 96 in clause_numbers_in_raw(SPACED)


def test_a_measurement_is_not_a_clause_marker():
    """The widening must not turn dimensions into citable clauses."""
    assert not clause_numbers_in_raw(MEASUREMENT)


def test_a_decimal_is_not_two_markers():
    assert 5 not in clause_numbers_in_raw(DECIMAL)


def test_the_real_order_exposes_all_of_its_jewellery_clauses():
    import json, glob
    for f in glob.glob(str(ROOT / "storage" / "json_store" / "*.json")):
        d = json.load(open(f, encoding="utf-8"))
        if d["document_id"] == "33-05-01":
            found = clause_numbers_in_raw(d["raw_text"])
            missing = {96, 98, 99, 106, 107, 108} - found
            assert not missing, f"clause markers still invisible: {sorted(missing)}"
            return
    raise AssertionError("33-05-01 not in the corpus")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
    print("all clause-marker tests passed")
