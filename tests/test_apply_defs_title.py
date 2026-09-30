# -*- coding: utf-8 -*-
"""A replace-sections def can keep the block's existing title — no model, no API.

The section title is part of every clause chunk's embedded text, so rebuilding it
("עיקרי הפקודה — …") re-embeds clauses whose text did not change and moves their
ranking. On 29.09 the in-place correction of HKA-31-08-01's live errors changed only
five clauses, and the new title alone dropped an order out of the ruler window
(q00109). `section_title` keeps the old title; without it the title is built as before.

    venv\\Scripts\\python.exe tests\\test_apply_defs_title.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from night.recurate.apply_defs import section_from  # noqa: E402

CLAUSES = [{"number": "שאלה", "text": "תשובה"}]


def test_replace_sections_builds_the_prefixed_title_by_default():
    sec, drop, digit_free = section_from({"mode": "replace-sections", "title": "קובץ ההוראות",
                                          "replaces_sections": ["key-facts"], "clauses": CLAUSES})
    assert sec["title"] == "עיקרי הפקודה — קובץ ההוראות"
    assert sec["id"] == "key-facts" and drop == ["key-facts"] and not digit_free


def test_replace_sections_keeps_an_explicit_section_title():
    old = 'קובץ הוראות הקריאה לשירות מילואים (הוראת קבע אכ"א 31-08-01)'
    sec, drop, _ = section_from({"mode": "replace-sections", "title": "קובץ ההוראות", "section_title": old,
                                 "replaces_sections": ["key-facts"], "clauses": CLAUSES})
    assert sec["title"] == old and sec["clauses"] == CLAUSES and drop == ["key-facts"]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
    print("all apply-defs title tests passed")
