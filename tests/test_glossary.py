# -*- coding: utf-8 -*-
"""storage.glossary — soldier vocabulary appended to the retrieval query.

Pins the contract that makes it safe: whole tokens only (with Hebrew prefixes
stripped), the question itself is never altered, a query with no glossary term
is returned byte-for-byte, and the flag is off unless RETRIEVE_GLOSSARY=1."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storage import glossary as G


def test_no_term_returns_query_unchanged():
    q = "כמה ימי חופשה שנתית מגיעים לי בשירות חובה?"
    assert G.expansions(q) == [] or all(e for e in G.expansions(q))
    # 'חופשה' is not an entry; 'חופש' is, but only as a whole token
    assert G.expand("מה קורה עם החופשה שלי בתרגיל") == "מה קורה עם החופשה שלי בתרגיל"


def test_prefixed_and_quoted_forms_match():
    ex = G.expansions('תוך כמה זמן אני אמור לקבל תור לקב"ן')
    assert ex and "בריאות הנפש" in ex[0]
    # the same with a Hebrew gershayim and a ב prefix
    assert G.expansions("בקב״ן") == G.expansions('קב"ן')


def test_original_question_is_kept_whole():
    q = 'תוך כמה זמן אני אמור לקבל תור לקב"ן'
    out = G.expand(q)
    assert out.startswith(q + " ")
    assert 'קב"ן' in out


def test_two_word_entry_matches_as_phrase():
    ex = G.expansions("קיבלתי צו 8 ואני צריך לדחות אותו")
    assert any("צו קריאה" in e for e in ex)
    # '8' alone must not fire
    assert G.expansions("יש לי 8 ימי חופשה") == []


def test_generic_words_are_not_entries():
    # words that appear in almost every question must never expand — they
    # would bias every retrieval toward one order
    for w in ("בסיס", "משפט", "שמירה", "מיון", "רגילה", "מיוחדת", "תג", "יחידה", "מפקד"):
        assert w not in G.GLOSSARY, w


def test_flag_off_by_default_in_code():
    import os
    if os.environ.get("RETRIEVE_GLOSSARY") is None:
        assert G.RETRIEVE_GLOSSARY is False


# ── the 2026-09-18 batch (night/head100/GLOSSARY_CRITERION.md) ──────────────

def test_multiword_entry_accepts_hebrew_prefixes():
    # a reservist writes "בצו 8" at least as often as "צו 8"; before 18.09 only
    # the bare form fired, while single tokens already had their prefixes stripped
    assert G.expansions("הקפיצו אותי בצו 8 ויש לי בעיה בבית") == G.expansions("קיבלתי צו 8")
    assert G.expansions("פינו אותו לחדר מיון בלילה") == G.expansions("חדר מיון")
    # the prefix rule must not reach into another word or a bare number
    assert G.expansions("יש לי 8 ימי חופשה") == []
    assert G.expansions("חרצו 8 חריצים בקיר") == []


def test_only_the_punitive_weekend_phrasings_expand():
    ex = G.expansions('המ"פ הוריד לי שבת בלי לשמוע אותי')
    assert "מניעת חופשה" in ex
    # "סוגר שבת" is also the routine rotation — pointing it at the
    # leave-prevention order would mislead a question about a normal weekend on base
    assert "מניעת חופשה" not in G.expansions("אני סוגר שבת השבוע, מגיע לי יום חופש במקום?")


def test_phone_slang_expands_to_the_neutral_word_only():
    # a phone question may be about restrictions (21.0113) or about compensation
    # for a broken one (35.0223): the expansion must not choose the order
    assert G.expansions("נשבר לי הפלאפון באימון") == ["טלפון"]


def test_distress_euphemisms_reach_the_distress_order_vocabulary():
    for q in ("חבר מהמחלקה אמר לי שנמאס לו מהחיים, מה עושים?",
              "הוא אמר שהוא לא רוצה לחיות יותר"):
        ex = G.expansions(q)
        assert ex and "אובדני" in ex[0], q


def test_noun_forms_of_regila_expand_but_the_adjective_does_not():
    assert G.expansions("רוצה לטוס לחול ברגילה") == ["חופשה שנתית"]
    assert G.expansions("כמה ימי רגילה מגיע לי בשנה?") == ["חופשה שנתית"]
    assert G.expansions("זו פעילות רגילה של היחידה") == []


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
    print("all glossary tests passed")
