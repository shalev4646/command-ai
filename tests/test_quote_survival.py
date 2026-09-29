# -*- coding: utf-8 -*-
"""night/quote_survival.py: a write that deletes the only chunk carrying a target's quote must
be counted, because every instrument silently drops such a target. No API.

    venv\\Scripts\\python.exe tests\\test_quote_survival.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from night import quote_survival as Q

DOC = {"document_id": "X", "title": "פקודה", "raw_text": "טקסט גולמי קצר",
       "sections": [{"id": "key-facts", "title": "עיקרי הפקודה — פקודה",
                     "clauses": [{"number": "א", "text": "אסור לפטר עובד בתקופת שירות מילואים"},
                                 {"number": "ב", "text": "מחייבת התראה של 60 ימים לפחות"}]}]}


def test_the_normalizer_is_the_instruments_one():
    from night import sectprobe
    for s in ('אין קריאה מעשרה ימים לפני „החתונה"', 'תספורת עם "מדרגה"  בשיער', "עם ’גרש‘ ו״גרשיים״"):
        assert Q.norm(s) == sectprobe._norm(s), s


def test_chunks_are_built_like_the_index():
    ch = Q.doc_chunks(DOC)
    assert any("טקסט גולמי קצר" in c for c in ch)
    assert "פקודה — עיקרי הפקודה — פקודה סעיף א: אסור לפטר עובד בתקופת שירות מילואים" in ch


def test_replace_drops_the_section_and_merge_keeps_it_first():
    rep = {"document_id": "X", "mode": "replace-sections", "title": "פקודה", "replaces_sections": ["key-facts"],
           "clauses": [{"number": "ג", "text": "חדש"}]}
    after = Q.after_write(DOC, rep)
    assert [c["number"] for s in after["sections"] for c in s["clauses"]] == ["ג"]
    blk = {"document_id": "X", "mode": "block-for-existing-order", "section_id": "key-facts",
           "section_title": "עיקרי הפקודה — פקודה", "clauses": [{"number": "א", "text": "כפול"},
                                                                {"number": "ג", "text": "חדש"}]}
    after = Q.after_write(DOC, blk)
    assert [c["number"] for s in after["sections"] for c in s["clauses"]] == ["א", "ב", "ג"]
    assert after["sections"][0]["title"] == "עיקרי הפקודה — פקודה"
    assert DOC["sections"][0]["clauses"][0]["text"].startswith("אסור"), "the live doc is not mutated"


def test_a_vanishing_quote_is_counted_and_held_is_flagged():
    before = Q.doc_chunks(DOC)
    rep = {"document_id": "X", "mode": "replace-sections", "title": "פקודה", "replaces_sections": ["key-facts"],
           "clauses": [{"number": "א", "text": "אסור לפטר עובד בתקופת שירות מילואים, מכל סיבה"}]}
    after = Q.doc_chunks(Q.after_write(DOC, rep))
    targets = [("night/head100/targets.json", {"id": "t1", "verified_quotes": ["מחייבת התראה של 60 ימים לפחות"]}, False),
               ("night/fresh_v3/questions.json", {"id": "f1", "verified_quotes": ["מחייבת התראה של 60 ימים לפחות"]}, True),
               ("night/head100/targets.json", {"id": "t2", "verified_quotes": ["אסור לפטר עובד בתקופת שירות מילואים",
                                                                              "מחייבת התראה של 60 ימים לפחות"]}, False),
               ("night/head100/targets.json", {"id": "t3", "verified_quotes": ["אסור לפטר עובד בתקופת שירות מילואים"]}, False)]
    dropped, damaged = Q.lost(before, after, targets)
    assert [(i, h) for _, i, h in dropped] == [("t1", False), ("f1", True)]
    assert [i for _, i, _ in damaged] == ["t2"], "one of two quotes gone is damage, not a drop"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
    print("all quote-survival tests passed")
