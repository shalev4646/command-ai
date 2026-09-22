# -*- coding: utf-8 -*-
"""The homonym class (storage/glossary.HOMONYMS) and its two consumers —
RETRIEVE_HOMONYMS (retrieval expansions) and ANSWER_TERM_NOTE (one line of
term clarification in the user turn). Both ship OFF and OFF is byte-identical;
the table is grounded in orders that use each sense (night/HOMONYMS_CRITERION.md).

    venv\\Scripts\\python.exe tests\\test_homonyms.py
"""
import os
import sys
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import backend
from storage import glossary as G

PRE_SLEEP = "מותר למפקד לקחת לנו את שעת הת\"ש בגלל מסדר?"
TASH_OFFICER = "איך פונים למשק\"ית הת\"ש כשיש בעיה כלכלית בבית?"
TASH_BARE = "מה זה ת\"ש?"
SICK_DAY = "הרופא נתן לי יום ב', מותר לי לצאת מהבסיס?"
MONDAY = "ביום ב' בשבוע יש מסדר בוקר?"
PLATOON = "המ\"מ שלי צועק עליי מול כולם, זה מותר?"
MILLIMETRE = "מותר שרשרת בקוטר 3 מ\"מ עם המדים?"
FAMILY_PAY = "מגיע לי תשמ\"ש אם ההורים שלי לא עובדים?"
PLAIN = "כמה ימי חופשה מגיעים לי בשנה?"


@contextmanager
def _flags(homonyms: bool, note: int):
    old = (G.RETRIEVE_HOMONYMS, backend.ANSWER_TERM_NOTE)
    G.RETRIEVE_HOMONYMS, backend.ANSWER_TERM_NOTE = homonyms, note
    try:
        yield
    finally:
        G.RETRIEVE_HOMONYMS, backend.ANSWER_TERM_NOTE = old


def test_both_ship_off():
    assert G.RETRIEVE_HOMONYMS == (os.environ.get("RETRIEVE_HOMONYMS", "0") == "1")
    assert backend.ANSWER_TERM_NOTE == int(os.environ.get("ANSWER_TERM_NOTE", "0"))


def test_off_the_expansions_are_untouched():
    with _flags(False, 0):
        for q in (PRE_SLEEP, SICK_DAY, PLATOON, FAMILY_PAY):
            assert not any("טרום השינה" in e or "מפקד מחלקה" in e or "תשלום משפחתי" in e
                           or "כושר עבודה" in e for e in G.expansions(q)), q


def test_the_context_picks_the_sense():
    with _flags(True, 0):
        ex = " ".join(G.expansions(PRE_SLEEP))
        assert "טרום השינה" in ex, ex
        ex = " ".join(G.expansions(TASH_OFFICER))
        assert "טרום השינה" not in ex, "the welfare NCO context must not pull the sleep order in"
        assert "כושר עבודה" in " ".join(G.expansions(SICK_DAY))
        assert "כושר עבודה" not in " ".join(G.expansions(MONDAY))
        assert "מפקד מחלקה" in " ".join(G.expansions(PLATOON))
        assert "מפקד מחלקה" not in " ".join(G.expansions(MILLIMETRE)), "a digit before מ\"מ is a length"
        assert "תשלום משפחתי" in " ".join(G.expansions(FAMILY_PAY))
        assert G.expansions(PLAIN) == [], "no homonym, nothing added"


def test_no_cue_means_every_sense_is_active():
    """A real ambiguity is passed on as one, never guessed."""
    senses = dict(G.homonym_senses(TASH_BARE))['ת"ש']
    assert len(senses) == 2, senses
    with _flags(True, 0):
        assert "טרום השינה" in " ".join(G.expansions(TASH_BARE))


def test_the_note_names_one_sense_or_lists_them():
    assert G.term_note(PRE_SLEEP) == 'ת"ש = שעת טרום השינה (ט"ש)'
    assert G.term_note(TASH_OFFICER).startswith('ת"ש = תנאי שירות')
    bare = G.term_note(TASH_BARE)
    assert " או " in bare and bare.endswith("לפי ההקשר"), bare
    assert G.term_note(PLAIN) == ""


def test_the_user_turn_is_byte_identical_off_and_carries_the_note_on():
    ctx = "[35.0402 | חופשות | סעיף 1]\nחייל זכאי ל-18 ימי חופשה."
    with _flags(False, 0):
        off = backend._compose_user_content(PRE_SLEEP, ctx, None)
        assert off == f"{PRE_SLEEP}\n\n{backend._CONTEXT_HEADER}\n{ctx}"
        off_p = backend._compose_user_content(PRE_SLEEP, ctx, ["חייל בודד"])
        assert "(הבהרת מונחים" not in off_p
    with _flags(False, 1):
        on = backend._compose_user_content(PRE_SLEEP, ctx, None)
        assert on.startswith(f"{PRE_SLEEP}\n\n(הבהרת מונחים: ת\"ש = שעת טרום השינה (ט\"ש))\n\n{backend._CONTEXT_HEADER}"), on
        assert backend._compose_user_content(PLAIN, ctx, None) == f"{PLAIN}\n\n{backend._CONTEXT_HEADER}\n{ctx}", \
            "no ambiguous term, no note — byte-identical even when on"
        on_p = backend._compose_user_content(PRE_SLEEP, ctx, ["חייל בודד"])
        assert "(הבהרת מונחים" in on_p and "(פרטי השואל: חייל בודד" in on_p
        assert on_p.index("(הבהרת מונחים") < on_p.index("(פרטי השואל"), "the note sits under the question"


def test_every_sense_is_grounded_in_the_corpus_docstring_rule():
    """Each entry has a pattern, and each sense a label; senses whose
    expansion is empty are note-only (the orders use the term themselves)."""
    for h in G.HOMONYMS:
        assert h["term"] and h["pattern"] and h["senses"], h["term"]
        for s in h["senses"]:
            assert s["label"], h["term"]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
    print("all homonym tests passed")
