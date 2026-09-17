# -*- coding: utf-8 -*-
"""night.review — the flag that stops the grader from eating gains (שלב 6).
Verbatim openings from the 12.09 arms; flags never change a grade."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import backend
from night import report_goal as G
from night import review as R

RS023_CLEAN3 = ('**פסיקה:** אסור בתנאים — הערת חייל משנתו באופן הפוגע בשינה הסדורה מותרת רק '
                'מטעם מוכר. **מקור:** PM-33.0213, "שעות השינה של חיילים בצה"ל"')
RS023_BASE = ('**פסיקה:** אסור בתנאים — הדלקת אור ב-05:00 הפוגעת בשינת האחרים אסורה. '
              '**מקור:** PM-33.0213, סעיף')
RS035_CLEAN3 = "המידע לא קיים בפקודות שסופקו. **טרם במאגר:** הכללים לגבי שחרור חייל חולה."
RS063_REFUSAL = "**פסיקה:** המידע לא קיים בפקודות שסופקו — לגבי תפיסת טלפון בזמן אישי."


def _row(qid, answer, answered, parts=("a", "b")):
    return {"id": qid, "answer": answer, "q": qid,
            "grade": {"level": "full", "parts": list(parts), "answered_parts": answered}}


def test_the_ruling_words_match_the_chip_and_the_guard():
    assert R.REFUSAL_OPENERS == backend._REFUSAL_OPENERS
    assert R.opening_ruling(RS023_CLEAN3).startswith("אסור בתנאים")
    assert R.opening_ruling(RS063_REFUSAL) is None, "a labelled refusal is a refusal"
    assert R.opening_ruling(RS035_CLEAN3) is None
    assert R.opening_ruling("**תשובה:** תעודת ההערכה נמסרת בטקס.") == "תעודת ההערכה נמסרת בטקס."
    # a declared gap in a ruling's clothes is not a ruling (rs001, rs047)
    assert R.opening_ruling("**תשובה:** הפקודות אינן נוקבות במשך זמן מוגדר להפקת התעודה.") is None
    assert R.opening_ruling("**פסיקה:** אין בפקודות כלל שקובע זאת.") is None


def test_rule_a_flags_a_ruling_that_scored_short_and_nothing_else():
    flags = R.flag_rows([_row("rs023", RS023_CLEAN3, 0), _row("rs035", RS035_CLEAN3, 0),
                         _row("ok", RS023_BASE, 2)])
    assert set(flags) == {"rs023"}, flags
    assert flags["rs023"][0].startswith("ruling-scored-short")


def test_rule_b_needs_a_full_base_with_the_same_orders():
    base = [_row("rs023", RS023_BASE, 2), _row("other", "**פסיקה:** מותר. **מקור:** 31.0513", 2),
            _row("rs037", "**תשובה:** נמסרת בטקס. **מקור:** 30.0117", 1, parts=("a",))]
    new = [_row("rs023", RS023_CLEAN3, 0), _row("other", "**פסיקה:** מותר. **מקור:** 61.0104", 0),
           _row("rs037", "**תשובה:** הפקודות אינן נוקבות במספר ימים, אך נמסרת בטקס. **מקור:** 30.0117", 0, parts=("a",))]
    flags = R.flag_rows(new, base)
    assert any(r.startswith("same-substance-loss") for r in flags["rs023"]), flags
    assert all(not r.startswith("same-substance-loss") for r in flags["other"]), "different orders — not the same substance"
    assert flags["rs037"] == [f"same-substance-loss: base full, both cite ['30.0117']"], "rule B needs no ruling — the shared citation is the signal"


def test_cited_orders_reads_every_id_shape():
    assert R.cited_orders("PM-33.0213, 61.0104, HKA-31-08-01, 33-05-01, CHOK-SHIPUT-1955") == \
        {"PM-33.0213", "61.0104", "HKA-31-08-01", "33-05-01", "CHOK-SHIPUT-1955"}


def test_a_review_overrides_only_its_rows_and_never_exceeds_the_parts():
    rows = [_row("rs023", RS023_CLEAN3, 0), _row("rs035", RS035_CLEAN3, 0)]
    out = R.apply_review(rows, {"rs023": {"answered_parts": 5, "note": "same ruling as base"}})
    assert out[0]["grade"]["answered_parts"] == 2 and out[0]["grade"]["reviewed"] is True
    assert out[1]["grade"]["answered_parts"] == 0 and "reviewed" not in out[1]["grade"]
    assert rows[0]["grade"]["answered_parts"] == 0, "the graded row is never mutated"


def test_report_goal_shows_strict_as_graded_and_as_reviewed():
    rows = [_row("rs023", RS023_CLEAN3, 0), _row("x", RS023_BASE, 2)]
    d = G.tally(rows, {})
    assert d["strict"] == 2
    d2 = G.tally(R.apply_review(rows, {"rs023": {"answered_parts": 2}}), {})
    assert d2["strict"] == 4


def test_a_missing_review_file_is_empty():
    assert R.load_review("no-such-arm-ever") == {}


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
    print("all review tests passed")
