# -*- coding: utf-8 -*-
"""Two holes the wave-1 content review found in night.curate's gates.

31.0519 shipped a single-clause block whose user-facing text ended in
`(סעיפים 1, 7, 9).}]}}, 8]}], "` — raw JSON debris inside the field a soldier
reads. It passed both gates: the comma-list citation form is invisible to
cited_numbers (which only reads "X–Y" ranges and "סעיף X"), and JSON debris
carries no Hebrew letters, so the vocabulary gate never sees it.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from night.curate import check, cited_numbers

CLEAN = ("אגף הנוער והנח\"ל קובע אילו חיילים חברי הגרעין ישרתו כמדריכים "
         "בתנועות נוער, ושלישות הפיקוד מוודאת את המכסה")

# 20 numbered clauses so is_numbered() is true and the citation gate is armed;
# the clean claim's own vocabulary sits in the source, as a faithful paraphrase's
# would, so only the gates under test can reject it
RAW = " ".join(f"{i} . סעיף בדבר הדרכה בתנועות נוער וחברי גרעין {i}"
               for i in range(1, 21)) + " " + CLEAN


def _section(text):
    return {"id": "key-facts", "title": "t",
            "clauses": [{"number": "מי קובע", "text": text}]}


def test_comma_list_citations_are_read():
    assert cited_numbers("… (סעיפים 1, 7, 9)") == {1, 7, 9}
    assert cited_numbers("… (סעיפים 3, 4 ו-12)") == {3, 4, 12}
    # the forms that already worked keep working
    assert cited_numbers("(סעיפים 44–45)") == {44, 45}
    assert cited_numbers("(סעיף 43)") == {43}


def test_comma_list_citation_beyond_source_is_a_problem():
    problems, _ = check(_section(CLEAN + " (סעיפים 1, 7, 99)."), RAW)
    assert any("99" in p for p in problems), problems


def test_json_debris_in_text_is_a_problem():
    debris = CLEAN + ' (סעיפים 1, 7, 9).}]}}, 8]}], "'
    problems, _ = check(_section(debris), RAW)
    assert any("debris" in p for p in problems), problems


def test_clean_clause_still_passes():
    problems, _ = check(_section(CLEAN + " (סעיפים 1, 7, 9)."), RAW)
    assert problems == [], problems


if __name__ == "__main__":
    # Plain-assert runner, matching every other suite in tests/ (see
    # test_scope_routes.py: without it the file imports, runs nothing, exits 0).
    failures = 0
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            try:
                _fn()
                print("PASS", _name)
            except AssertionError as _exc:
                failures += 1
                print("FAIL", _name, "-",
                      str(_exc).encode("ascii", "replace").decode())
    sys.exit(1 if failures else 0)
