# -*- coding: utf-8 -*-
"""night.coverage_audit and the coverage gate in night.curate.check (27.09).

The three known cases, frozen as excerpts in tests/fixtures_coverage.json (taken from the corpus
on 27.09, before any search clause was written): PM-33.0309's arrest-only block misses the search
chapter; 33.0220's one-clause block misses clause 13א (a list item under a lead-in); 21.0113's
block already carries its search clause and must NOT be flagged. No network, no model.

    venv\\Scripts\\python.exe tests\\test_coverage_audit.py
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from night import coverage_audit as ca
from night.curate import check

FX = json.loads((Path(__file__).parent / "fixtures_coverage.json").read_text(encoding="utf-8"))
T0309 = "מעצר וחיפושים כללי"
T0220 = "משטר המחנה - חומרים משכרים"
T0113 = 'הגבלת שימוש בטלפון אישי (רט"ן) בצה"ל'
SEARCH_CLAUSE = {"number": "סמכות מפקד לחפש אצל חייל מפקודיו או במקום שבפיקודו",
                 "text": "אין האמור בסעיף זה פוגע בזכותו של מפקד לחפש אצל כל חייל מפקודיו או במקום שבפיקודו, "
                         "על מנת לוודא קיום כל פקודה חוקית. צו חיפוש לגבי מקום צבאי יוציאו בית-דין צבאי, "
                         "שופט חוקר וקצין שיפוט בכיר."}
C13A = {"number": "חיפוש לאכיפת האיסור על חומרים משכרים",
        "text": "מפקד היחידה יהיה רשאי לערוך חיפוש אצל חייל מפקודיו או במקום שבפיקודו, על מנת להבטיח "
                "קיום הוראות פקודה זו, בהתאם לסמכות הקבועה בס' 245(ד) לחוק השיפוט הצבאי. חומר משכר "
                "הוא משקה משכר, סם או כל חומר אחר (סעיפים 1, 13)."}


def _cl(block):
    return [{"number": c["number"], "text": c["text"], "digit_free": False} for c in block]


def test_content_min_is_sectprobes():
    src = (ROOT / "night" / "sectprobe.py").read_text(encoding="utf-8")
    assert f"CONTENT_MIN = {ca.CONTENT_MIN}" in src, "the audit must use sectprobe's calibrated threshold"


def test_title_terms_skip_series_names_parentheses_and_generic_words():
    assert ca.title_terms(T0220) == ["חומרים", "משכרים"]
    assert ca.title_terms(T0309) == ["מעצר", "וחיפושים"]
    assert "רט\"ן" not in ca.title_terms(T0113) and "הגבלת" not in ca.title_terms(T0113)


def test_33_0309_search_chapter_is_flagged():
    cl = _cl(FX["b0309"])
    assert ca.uncovered_title_terms(T0309, cl, FX["r0309"]) == ["וחיפושים"]
    rules = ca.uncovered_rules(FX["r0309"], cl)
    assert any("צו חיפוש" in u["text"] or "פקודת חיפוש" in u["text"] for u in rules), rules[:3]
    heads = ca.uncovered_headings(FX["r0309"], T0309, cl)
    assert "סמכויות מפקד" in heads and "הגדרת מקום צבאי" in heads, heads


def test_33_0309_a_search_clause_covers_the_title_term():
    cl = _cl(FX["b0309"] + [SEARCH_CLAUSE])
    assert ca.uncovered_title_terms(T0309, cl, FX["r0309"]) == []


def test_33_0220_list_item_under_a_lead_in_is_a_rule():
    units = ca.rule_units(FX["r0220"])
    item = [u for u in units if u["text"].startswith("לערוך חיפוש אצל חייל")]
    assert item and item[0]["normative"], "13א inherits „יהיה רשאי״ from its lead-in"
    rules = ca.uncovered_rules(FX["r0220"], _cl(FX["b0220"]))
    assert any(u["text"].startswith("לערוך חיפוש") for u in rules)
    rules = ca.uncovered_rules(FX["r0220"], _cl(FX["b0220"] + [C13A]))
    assert not any(u["text"].startswith("לערוך חיפוש") for u in rules), "a clause carrying 13א covers it"


def test_21_0113_search_clause_is_not_flagged():
    cl = _cl(FX["b0113"])
    rules = ca.uncovered_rules(FX["r0113"], cl)
    assert not any("חיפוש" in u["text"] for u in rules), rules
    assert ca.uncovered_title_terms(T0113, cl, FX["r0113"]) == []


def test_administrative_units_are_filtered():
    raw = "16. פקודה זו תופץ לכל היחידות. 17. פיקוח ובקרה על ביצוע פקודה זו יבוצעו אחת לשנה ויהיה רשאי המבקר לדרוש דיווח."
    assert all(u["admin"] for u in ca.rule_units(raw) if u["normative"])


def test_acronym_title_term_matches_as_a_string():
    cl = [{"number": "x", "text": 'העברת חיילי נח"ל למעמד שירות ללא תשלום מבוצעת על ידי פיקוד הנח"ל.', "digit_free": False}]
    assert 'נח"ל' not in ca.uncovered_title_terms('חיילי נח"ל וחיילי ישיבות הסדר', cl)
    assert 'נח"ל' in ca.uncovered_title_terms('חיילי נח"ל', [{"number": "x", "text": "חיילי ישיבות הסדר"}])


def test_gate_off_without_title_is_the_old_check():
    sec = {"clauses": FX["b0309"]}
    p0, w0 = check(sec, FX["r0309"])
    assert not any("title term" in x for x in p0 + w0) and not any("heading" in x for x in w0)


def test_gate_flags_an_uncovered_title_term_and_accepts_a_reasoned_omission():
    sec = {"clauses": FX["b0309"]}
    p, w = check(sec, FX["r0309"], title=T0309)
    assert any("title term 'וחיפושים'" in x for x in p), p
    assert any("chapter heading" in x for x in w), "headings are warnings, not problems"
    p, _ = check(sec, FX["r0309"], title=T0309, omitted={"וחיפושים": "פרק לשוטרים צבאיים, נכתב בגל נפרד"})
    assert not any("title term" in x for x in p)
    p, _ = check(sec, FX["r0309"], title=T0309, omitted={"וחיפושים": ""})
    assert any("has no reason" in x for x in p)


def test_ratchet_regressions_detect_a_lost_clause():
    base = {"X": {"title": [], "rules": 2, "headings": 1, "numbers": 0, "no_block": False}}
    same = {"X": {"title": [], "rules": 2, "headings": 1, "numbers": 0, "no_block": False}}
    worse = {"X": {"title": ["חיפושים"], "rules": 5, "headings": 1, "numbers": 0, "no_block": False}}
    assert ca.regressions(same, base) == []
    bad = ca.regressions(worse, base)
    assert any("title term" in b for b in bad) and any("rules 2 -> 5" in b for b in bad)


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as e:
                fails += 1
                print(f"FAIL {name}: {str(e)[:300]}")
    raise SystemExit(1 if fails else 0)
