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


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
    print("all glossary tests passed")
