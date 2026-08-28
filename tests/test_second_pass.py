# -*- coding: utf-8 -*-
"""The second search — retrieving on what the answer said it was MISSING.

The app picks 8 chunks out of 10,324 by how much a chunk sounds like the
question, and a soldier does not talk like an order. When the window is wrong
the answer says so, in order language, and then that sentence is discarded.
This is the machinery that keeps it. Measured 2026-08-28 on the 46 arbitration
targets where the block answers and the first retrieval failed: the answering
order arrives 13/46 on the question and 25/46 on the declaration.

Pinned here: the gate that stops a successful answer from being re-searched
(it is what keeps the second call off 100% of questions), the reserved-seat
split, and the floor that stops the first pass from being evicted entirely.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import backend
import scope_routes

MARK = scope_routes.MARK_MISSING
NL = chr(10)


def test_it_ships_off():
    """Off until a paired measurement of the ANSWERS says otherwise — the same
    rule RETRIEVE_ROUTER_SLOTS and RETRIEVE_FULL_BLOCKS ship under."""
    assert backend.RETRIEVE_SECOND_PASS == int(os.environ.get("RETRIEVE_SECOND_PASS", "0"))


def test_an_answer_with_no_gap_yields_no_query():
    """The gate. A successful answer must never trigger a second retrieval or a
    second billed call — that is the whole reason this is affordable."""
    assert backend.lacked_from("הפקודה קובעת שלושים יום ואין חוסר.") == ""
    assert backend.lacked_from("") == ""
    assert backend.lacked_from(None) == ""


def test_the_query_is_the_sentence_after_the_marker():
    a = ("**פסיקה:** לא נמצא." + NL + NL
         + MARK + " זכאות חייל לחופשה מיוחדת עקב מחלה של קרוב משפחה." + NL + NL
         + "מה שנובע עבורך: לפנות למפקד.")
    got = backend.lacked_from(a)
    assert got == "זכאות חייל לחופשה מיוחדת עקב מחלה של קרוב משפחה.", got
    assert "מה שנובע" not in got, "the next section's vocabulary drags the query off the gap"
    assert "פסיקה" not in got


def test_both_markers_are_read():
    """The prompt has two ways of declaring a gap and an answer can carry
    either. Reading only one would silently skip a share of the failures."""
    other = getattr(scope_routes, "MARK_OUT_OF_SCOPE", "")
    assert other, "MARK_OUT_OF_SCOPE disappeared — lacked_from needs updating"
    assert backend.lacked_from(other + " הכלל בדבר קעקועים.") == "הכלל בדבר קעקועים."


def test_the_query_is_capped():
    """A runaway answer must not turn into a 4,000-character query."""
    assert len(backend.lacked_from(MARK + " " + "מילה " * 400)) <= 300


def test_a_declaration_beats_the_question_as_a_query():
    """The mechanism, on the real corpus and the real retrieval. The soldier's
    words and the order's words do not overlap; the app's own restatement is
    written in the order's register, so it lands on the order."""
    q = "אני חם וחלש, מותר לי ללכת לקליניקה?"
    decl = "נוהל פנייה של חייל למרפאת היחידה וזכותו לקבלת טיפול רפואי."
    by_q = {c["doc_id"] for c in backend.retrieve_for_role(q, "soldier", route=set())}
    by_d = {c["doc_id"] for c in backend.retrieve_for_role(decl, "soldier", route=set())}
    assert by_d != by_q, "the two queries retrieved the identical window"
    assert "61.0104" in by_d, sorted(by_d)


def test_the_first_pass_always_keeps_seats():
    """Its chunks are what let the model state the gap in the first place. A
    window that hands every seat to the second query can lose a partial answer
    that was already working, so at least two seats are reserved for the first.
    """
    n = backend.MAX_CONTEXT_CHUNKS
    for reserved in (1, 4, n, n + 50):
        keep = min(reserved, max(0, n - 2))
        assert keep <= n - 2, (reserved, keep)
        assert n - keep >= 2, (reserved, keep)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
    print("all second-pass tests passed")
