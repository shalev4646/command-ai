# -*- coding: utf-8 -*-
"""RETRIEVE_QUOTELESS (storage/glossary): every quoted key of the glossary and
the homonym table also matches its quote-less spelling (לתשמש for תשמ"ש) under
one rule — three letters or more, not a common word, not a word the orders
write unquoted. Ships OFF and OFF is byte-identical; the derived set is the
hand-reviewed list of night/QUOTELESS_CRITERION.md and is locked here.

    venv\\Scripts\\python.exe tests\\test_quoteless.py
"""
import os
import sys
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from storage import glossary as G

FAMILY_PAY_BARE = "מגיע לי לתשמש אם ההורים שלי לא עובדים?"
FAMILY_PAY_QUOTED = 'מגיע לי לתשמ"ש אם ההורים שלי לא עובדים?'
NEAR_HOME_BARE = "איך מבקשים קלב אם אמא שלי חולה?"
UNCOUNTED_BARE = "מה זה תבן ומתי המחבוש שלי לא נספר בשירות?"
TWO_LETTERS = "מה זה שג ומי עומד שם?"
EXCLUDED = "הוא קפץ מהגדר ליד השקם"
PLAIN = "כמה ימי חופשה מגיעים לי בשנה?"

REVIEWED = {"תשמש": 'תשמ"ש', "תבן": 'תב"ן', "קלב": 'קל"ב'}


@contextmanager
def _flags(quoteless: bool, homonyms: bool):
    old = (G.RETRIEVE_QUOTELESS, G.RETRIEVE_HOMONYMS)
    G.RETRIEVE_QUOTELESS, G.RETRIEVE_HOMONYMS = quoteless, homonyms
    try:
        yield
    finally:
        G.RETRIEVE_QUOTELESS, G.RETRIEVE_HOMONYMS = old


def test_ships_off():
    assert G.RETRIEVE_QUOTELESS == (os.environ.get("RETRIEVE_QUOTELESS", "0") == "1")


def test_the_derived_set_is_exactly_the_reviewed_list():
    """A new quoted entry in either table changes this set — and needs a new
    review (night/QUOTELESS_CRITERION.md) before it is allowed to."""
    assert G.quoteless_forms() == REVIEWED, G.quoteless_forms()
    assert G._QUOTELESS_GLOSSARY == {}, "every quoted glossary key already has a hand-written twin, or is excluded"
    assert set(G._QUOTELESS_HOMONYMS) == set(REVIEWED.values())
    for bad in ("קפץ", "שקם", "תש", "שג", "מם", "יום ב"):
        assert bad not in G.quoteless_forms(), bad


def test_off_is_byte_identical_whatever_the_homonym_flag_says():
    for homonyms in (False, True):
        with _flags(False, homonyms):
            for q in (FAMILY_PAY_BARE, NEAR_HOME_BARE, UNCOUNTED_BARE):
                assert "תשלום משפחתי" not in " ".join(G.expansions(q)), q
                assert "שיבוץ קרוב לבית" not in " ".join(G.expansions(q)), q
                assert G.homonym_senses(q) == [], q
                assert G.term_note(q) == "", q
            assert G.expand(FAMILY_PAY_BARE) == FAMILY_PAY_BARE
            assert G.expand(UNCOUNTED_BARE) == UNCOUNTED_BARE
            # the hand-written glossary twin keeps working on its own
            assert "קרוב לבית העברה" in " ".join(G.expansions(NEAR_HOME_BARE))


def test_on_the_bare_spelling_matches_the_quoted_one():
    with _flags(True, True):
        assert G.expansions(FAMILY_PAY_BARE) == G.expansions(FAMILY_PAY_QUOTED)
        assert "תשלום משפחתי" in " ".join(G.expansions(FAMILY_PAY_BARE))
        ex = " ".join(G.expansions(NEAR_HOME_BARE))
        assert "קרוב לבית העברה" in ex and "שיבוץ קרוב לבית" in ex, ex
        senses = dict(G.homonym_senses(UNCOUNTED_BARE))
        assert list(senses) == ['תב"ן'] and len(senses['תב"ן']) == 1, senses
        assert G.expansions(UNCOUNTED_BARE) == [], 'both תב"ן senses are note-only'
        assert G.term_note(UNCOUNTED_BARE).startswith('תב"ן = '), G.term_note(UNCOUNTED_BARE)
        assert G.term_note(FAMILY_PAY_BARE) == G.term_note(FAMILY_PAY_QUOTED)
        assert G.expansions(PLAIN) == []


def test_the_homonym_forms_reach_retrieval_only_through_their_own_flag():
    with _flags(True, False):
        assert G.expansions(FAMILY_PAY_BARE) == [], "RETRIEVE_HOMONYMS off => no homonym expansion, bare or quoted"
        assert G.expansions(FAMILY_PAY_QUOTED) == []
        # the note side is the same as for the quoted form: not gated here
        assert G.term_note(FAMILY_PAY_BARE) == G.term_note(FAMILY_PAY_QUOTED) != ""


def test_two_letter_and_excluded_forms_never_fire():
    with _flags(True, True):
        assert G.homonym_senses(TWO_LETTERS) == []
        assert "שער המחנה" not in " ".join(G.expansions(TWO_LETTERS))
        ex = " ".join(G.expansions(EXCLUDED))
        assert "פניות הציבור" not in ex and "קנטינה" not in ex, ex


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
