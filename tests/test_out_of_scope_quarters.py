# -*- coding: utf-8 -*-
r"""out_of_scope: living-quarters questions are not posting questions.

Found 2026-08-26 by running the existing table against a set that did not
build it — the 45 mini-pilot-150 questions the adjudication proved no order
answers. The table covered 8 of them, and one of the eight was routed WRONG:

    "אני במתח כבד מהחדר שלי, אפשר להעביר אותי לחדר אחר או צריך להיות סיבה
     ממש חמורה?"   ->  placement_transfer  ->  "קצין השלישות ... בקשה לשינוי
     שיבוץ (פ״מ 31.0308)"

A soldier in distress over his ROOM was sent to the officer who changes his
POSTING. `להעביר\s+אות` cannot tell the two apart, exactly as `קורס` alone once
sent a discharge-package question to the course commander (the failure the
module's own header records). This is the module's iron rule earning its keep a
second time: a table tested only against the set that wrote it confirms itself.

The door these questions do have is verified in the corpus, not invented.
פ"מ 35.0822 (הטיפול בפרט ביחידה) sets the route for anything the unit decides:

    "לכל חייל אפשרות לפנות בבקשה בעל פה למפקדו הישיר. אם התשובה אינה משביעת
     רצונו או עברו 3 ימי פעילות ללא תשובה, רשאי לפנות בכתב למפקד הממונה על
     המפקד הישיר. בכל יחידה תוצב תיבת פניות"

so the strip can point somewhere real without deciding anything normative.

Evidence (adjudicated NO_SUCH_RULE, each with a written explanation):
q00165 room transfer, q00162 a roommate's noise, q00073 quarters on hardship.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import out_of_scope as OS

ROOM = "אני במתח כבד מהחדר שלי, אפשר להעביר אותי לחדר אחר או צריך להיות סיבה ממש חמורה?"
NOISE = ("אחד מהחברים שלי בחדר כל הזמן משמיע מוזיקה רמה על הטלפון בלי אוזניות. "
         "מי אמור לעשות משהו בזה?")
POSTING = "יכול להיות שמעבירים אותי ליחידה אחרת בלי שאיתי את הדעה, מה זה אומר?"
# ⚠ Held-out control, found the same way one turn later: running the NEW family
# against the 150 questions that did not write it caught a THEFT report, because
# a bare `מהחדר` matches "מהחדר של חייל אחר". Suspected theft is regulated (the
# duty to report, חש"צ) and is not a quarters-assignment matter, so the family
# must not claim it. The pattern now asks for the asker's OWN room.
THEFT = "ראיתי שאחד החיילים לוקח דברים מהחדר של חייל אחר. מה אני אמור לעשות?"


def test_room_transfer_is_not_sent_to_the_postings_officer():
    assert OS.family_of(ROOM) != "placement_transfer", (
        "a room question routed to קצין השלישות — the posting door, not the "
        "quarters one"
    )


def test_quarters_questions_get_the_unit_welfare_door():
    for q in (ROOM, NOISE):
        dest = OS.destination_for(q)
        assert dest is not None, f"no door at all for: {q[:40]}"
        assert "שלישות" not in dest["where"], f"posting door leaked into: {q[:40]}"


def test_theft_in_someone_elses_room_is_not_a_quarters_question():
    assert OS.family_of(THEFT) != "quarters_welfare", (
        "a theft report matched the quarters family — `מהחדר` must require the "
        "asker's own room, not any room"
    )


def test_real_posting_questions_still_reach_the_postings_officer():
    """The guard must be narrow: it may not cost the family its own cases."""
    assert OS.family_of(POSTING) == "placement_transfer"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
    print("all quarters-door tests passed")
