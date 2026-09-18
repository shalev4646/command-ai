# -*- coding: utf-8 -*-
"""The gates of night.digitfix — the positional digit repair. No PDF, no API.

A wrong number in the right place is worse than a known-bad one, so every
gate here is a refusal: a reading whose digit-run structure differs from the
text layer is dropped, a word that cannot be located at a word boundary
refuses its whole page, and non-digit characters are never touched.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from night.digitfix import (digit_runs, find_word, merge_readings, patch_page_text,
                            rebuild_raw_text, runs_compatible, substitute)


def test_two_reads_must_agree_on_the_outcome():
    words = [{"id": "a", "text": "1.8.38"}, {"id": "b", "text": "8-"}, {"id": "c", "text": "19"},
             {"id": "d", "text": "38"}]
    first = {"a": "-1.3.83", "b": "?", "c": ".12", "d": "83"}
    second = {"a": "-1.3.831", "b": "-3", "c": "12", "d": "83"}
    merged, disagree = merge_readings(words, first, second)
    assert merged["c"] == ".12" and merged["d"] == "83"      # same outcome, different surface form
    assert merged["a"] == "?" and merged["b"] == "?"         # clipped glyph / uncertain: left alone
    assert disagree == 2
    # a word only one read saw keeps that read; a word neither saw stays absent
    merged, _ = merge_readings([{"id": "e", "text": "7"}, {"id": "f", "text": "7"}], {"e": "1"}, {})
    assert merged == {"e": "1"}


def test_run_structure_gate():
    assert runs_compatible("1.1.38", "1.1.83")          # same skeleton, reversed year
    assert runs_compatible("19", "12")                   # the non-injective clause number
    assert not runs_compatible("0116116", "61.0110")     # one run vs two: refused
    assert not runs_compatible("9155", "2011x5")         # extra run
    assert not runs_compatible("38", "1983")             # length differs
    assert digit_runs("בסך1 שקל") == ["1"]


def test_substitute_keeps_non_digits_and_refuses_shape_changes():
    assert substitute("1.1.38", "1.1.83") == ("1.1.83", "changed")
    assert substitute("בסך1", "1") == ("בסך1", "same")          # Hebrew letters kept, digits equal
    assert substitute("בסך1", "בסן7") == ("בסך7", "changed")    # misread letters ignored, digits taken
    assert substitute("19", "12") == ("12", "changed")
    assert substitute("0116116", "61.0110") == ("0116116", "refused")
    assert substitute("38", None) == ("38", "unread")
    assert substitute("38", "?") == ("38", "unread")
    assert substitute("38", "") == ("38", "unread")


def test_find_word_respects_boundaries():
    text = "סעיף 11 ואז 1 בסוף"
    assert find_word(text, "1", 0) == text.index(" 1 ") + 1      # not the "1" inside "11"
    assert find_word(text, "11", 0) == text.index("11")
    assert find_word(text, "7", 0) == -1
    assert find_word("1 x 1", "1", 1) == 4                          # sequential search from a cursor
    glued = "פקודות מטכ\"ל , 1 \n 'אוק38\n"                        # words splits 'אוק | 38, text glues them
    assert find_word(glued, "38", 0) == glued.index("38")
    dated = "מיום 1.1.38 ועד 38 ימים"
    assert find_word(dated, "38", 0) == dated.index(" 38 ") + 1     # not inside the date
    assert find_word("סה\"כ 35.0314 ושוב", "1", 0) == -1             # not inside a longer number


def test_patch_page_text_substitutes_in_order():
    page = " בלמ\"ס \n 35.0314\n פקודות מטכ\"ל , 1 \n 'אוק38\n 19\n. על אף האמור\n 18\n. חייל"
    words = [{"id": "p1w0", "text": "35.0314"}, {"id": "p1w1", "text": "1"},
             {"id": "p1w2", "text": "'אוק38"}, {"id": "p1w3", "text": "19"},
             {"id": "p1w4", "text": "18"}]
    readings = {"p1w0": "35.0314", "p1w1": "1", "p1w2": "83", "p1w3": "12", "p1w4": "13"}
    new, stats = patch_page_text(page, words, readings)
    assert new == page.replace("'אוק38", "'אוק83").replace("19\n", "12\n").replace("18\n", "13\n")
    assert stats["changed"] == 3 and stats["same"] == 2 and stats["refused"] == 0
    assert ("p1w3", "19", "12") in stats["changes"]


def test_patch_page_text_refuses_a_page_it_cannot_align():
    page = "סעיף 12 בלבד"
    words = [{"id": "p0w0", "text": "12"}, {"id": "p0w1", "text": "77"}]   # 77 is not on the page
    new, stats = patch_page_text(page, words, {"p0w0": "13", "p0w1": "78"})
    assert new is None and "p0w1" in stats["error"]


def test_partial_readings_leave_unread_words_alone():
    page = "א 19 ב 18 ג"
    words = [{"id": "w0", "text": "19"}, {"id": "w1", "text": "18"}]
    new, stats = patch_page_text(page, words, {"w0": "12"})
    assert new == "א 12 ב 18 ג" and stats["unread"] == 1


def test_year_gate_ignores_order_numbers():
    from night.digitfix import years_gate_text
    assert "2023" not in years_gate_text("לפי פ\"מ 33.2023 סעיף 4")       # corrupted order number, not a year
    assert "1986" in years_gate_text("נכנס לתוקף ב-1986 לפי 33.0203")   # a real year survives


def test_rebuild_matches_ingestion_join():
    assert rebuild_raw_text(["a", "  ", "b\n"]) == "a\n\nb\n"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok {name}")
