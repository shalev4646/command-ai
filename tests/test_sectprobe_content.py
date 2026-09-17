# -*- coding: utf-8 -*-
"""The content-aware section hit of night.sectprobe — no model, no API.

sectprobe's original hit rule is verbatim: a served text must contain a full
verified quote or a 6-word run of one. Curated clauses paraphrase the order in
soldier language, so a block that carries the rule was counted as "section not
served" — the 16.09 attribution found the rule present in 73 of 83 curated
blocks while the verbatim rule saw 12 of 82 served. These tests pin the
content rule that closes that blind spot: the share of a quote's content words
that a served text carries.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from night.sectprobe import CONTENT_MIN, content_hit, content_overlap

# 33-05-01, jewelry: the raw quote and the curated clause that paraphrases it
QUOTE = "חייל הלובש מדים אינו רשאי לענוד עדיים פרט לטבעת נישואין ושרשרת אחת בלבד לצוואר"
PARAPHRASE = ("אילו תכשיטים מותר לענוד עם מדים: חייל במדים רשאי לענוד טבעת נישואין "
              "ושרשרת אחת בלבד לצוואר, ושום עדיים אחרים")
# a sibling clause of the same order — same order, wrong rule
SIBLING = "כובע מצחייה: מתי חובה לחבוש את הכובע ומתי מותר להסיר אותו בשטח המחנה"


def test_paraphrasing_clause_scores_above_threshold():
    assert content_overlap([QUOTE], PARAPHRASE) >= CONTENT_MIN


def test_sibling_clause_scores_below_threshold():
    assert content_overlap([QUOTE], SIBLING) < CONTENT_MIN


def test_identical_text_scores_one():
    assert content_overlap([QUOTE], QUOTE) == 1.0


def test_quote_without_content_words_scores_zero():
    # stopwords and two-letter tokens carry no content; no division by zero
    assert content_overlap(["של על את זה"], PARAPHRASE) == 0.0


def test_best_quote_wins_not_the_union():
    # a target with two quotes is served when EITHER rule is carried
    other = "משך השירות הסדיר של חייל שאינו יוצא צבא ייקבע בצו של שר הביטחון"
    assert content_overlap([other, QUOTE], PARAPHRASE) == content_overlap([QUOTE], PARAPHRASE)


def test_hit_follows_threshold():
    assert content_hit([QUOTE], PARAPHRASE)
    assert not content_hit([QUOTE], SIBLING)


def test_threshold_is_the_calibrated_value():
    # calibrated on five hand-verified cases (16.09): carriers scored
    # 0.36–0.52, non-carriers 0.13 and 0.20
    assert CONTENT_MIN == 0.3


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
    print("all sectprobe content tests passed")
