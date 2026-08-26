# -*- coding: utf-8 -*-
r"""out_of_scope: the last-resort door, for everything no family names.

Added 2026-08-26 by the user's explicit decision, and it reverses a choice the
module made deliberately — so the reasoning belongs here in full.

WHAT THE MODULE DECIDED BEFORE. Nine of the seventeen arbitrated questions were
left with no destination on purpose, under the rule "אין רצועה עדיפה על דלת
שגויה". That rule was earned twice over: `קורס` alone once sent a discharge-
package question to the course commander, and on the very day this file was
written the existing table sent a soldier distressed about his ROOM to the
postings officer. Guessing a specific address is how the strip hurts.

WHY THE LAST RESORT IS NOT THAT MISTAKE. It names no specific address. It says
the one thing that is true of every question the app has just admitted no order
governs: what GHQ orders do not settle is settled in the unit — and the orders
themselves define how to ask. פ"מ 35.0822, verbatim:

    "לכל חייל אפשרות לפנות בבקשה בעל פה למפקדו הישיר. אם התשובה אינה משביעת
     רצונו או עברו 3 ימי פעילות ללא תשובה, רשאי לפנות בכתב למפקד הממונה על
     המפקד הישיר. בכל יחידה תוצב תיבת פניות"

and פ"מ 33.0336 carries the ombudsman route when the unit does not resolve it.
So this is the weakest possible claim, not a guess: a procedure, not an answer.

WHAT IT COSTS, HONESTLY. Measured the same day: of the zeros where the strip
fires, roughly a third are questions the corpus CAN answer and retrieval missed.
A generic door quiets those too, so the strip stops being a signal that
something is broken. That is the real price, and it is why `EVIDENCE` keeps the
specific families separate — a question that reaches the last resort is a
question no family recognised, and that count is worth watching.

MEASUREMENT NOTE. `_UNMATCHED` used to mean "returns None". It now means "no
SPECIFIC family claims it" — the guard that matters, since inventing a specific
address is the failure mode. `test_out_of_scope.py` was updated to assert
exactly that, not relaxed.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import out_of_scope as OS

# Adjudicated NO_SUCH_RULE, no family recognises them, and each is genuinely a
# unit-level matter — the day roster differing between bases, how long before a
# commander must tell his superior he is stepping out, whether an approval has
# to be in writing.
ROSTER_DAY = "חייל בשלי זה יום ה׳ אתמול וחברה שלו מבסיס אחר אמרה שבהם זה יום ד׳. איך אני יודע מה נכון?"
NOTICE = "כמה זמן יש לי בשביל לדווח לעליון שאני יוצא? שעה? מינימום?"
IN_WRITING = "האם אני צריך לקבל אישור בכתב על כל דבר או סתם בעל פה מהממונה מספיק?"
# Control: a question a specific family owns must never fall through to it.
ROOM = "אני במתח כבד מהחדר שלי, אפשר להעביר אותי לחדר אחר או צריך להיות סיבה ממש חמורה?"


def test_unrecognised_questions_still_get_a_next_step():
    for q in (ROSTER_DAY, NOTICE, IN_WRITING):
        dest = OS.destination_for(q)
        assert dest is not None, f"still ends in the air: {q[:45]}"
        assert "מפקד" in dest["where"], f"last resort must name the route: {dest['where'][:60]}"


def test_the_last_resort_names_no_specific_office():
    """It may point at a procedure; it may not invent an address."""
    dest = OS.destination_for(NOTICE)
    for invented in ("שלישות", "אפסנא", "ת\"ש", "ביטוח לאומי", "מרפאה"):
        assert invented not in dest["where"], (
            f"last resort named {invented!r} — that is a guess, not a procedure"
        )


def test_specific_families_still_win():
    assert OS.family_of(ROOM) == "quarters_welfare"


def test_it_really_is_last():
    assert OS._FAMILIES[-1][0] == "unit_level_default"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
    print("all last-resort door tests passed")
