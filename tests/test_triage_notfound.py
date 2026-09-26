# -*- coding: utf-8 -*-
"""night.triage_notfound on dummy rows — no Sheet, no model, no network (27.09).

Selection keeps exactly the answers that refused or declared a gap; the verdict follows the window
and the corpus; the report goes to night/out only (gitignored — users' questions never enter git).

    venv\\Scripts\\python.exe tests\\test_triage_notfound.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from night import triage_notfound as tn
from scope_routes import MARK_MISSING

ROWS = [
    {"ts": "2026-09-27T10:00", "role": "soldier", "question": "מותר למפקד לחפש לי בתיק?", "refused": "TRUE",
     "answer_preview": "המידע לא קיים בפקודות שסופקו."},
    {"ts": "2026-09-27T10:01", "role": "soldier", "question": "כמה ימי חופשה מגיעים לי?", "refused": "FALSE",
     "answer_preview": "לפי פ\"מ 35.0402 מגיעים לך…"},
    {"ts": "2026-09-27T10:02", "role": "soldier", "question": "מי מאשר תשמ\"ש?", "refused": False,
     "answer_preview": f"לפי הפקודה… {MARK_MISSING} מי מאשר ותוך כמה זמן."},
    {"ts": "2026-09-27T10:03", "role": "soldier", "question": "", "refused": True, "answer_preview": ""},
]
DOCS = [
    {"document_id": "X-SEARCH", "title": "חיפושים",
     "raw_text": "12. מפקד רשאי לחפש אצל כל חייל מפקודיו או במקום שבפיקודו, על מנת לוודא קיום כל פקודה חוקית. "
                 "13. חיפוש בכליו של חייל ייערך בנוכחות עדים ככל האפשר.",
     "sections": [{"id": "key-facts", "clauses": [{"number": "מעצר", "text": "חייל שעצר אחר יעבירנו מייד לחדר משמר."}]}]},
    {"document_id": "Y-LEAVE", "title": "חופשות",
     "raw_text": "4. חייל בשירות חובה זכאי לחופשה שנתית של ימים בכל שנה, בתיאום עם מפקדו.",
     "sections": [{"id": "key-facts", "clauses": [{"number": "חופשה שנתית", "text": "חייל זכאי לחופשה שנתית בתיאום עם מפקדו."}]}]},
]


def test_select_keeps_refusals_and_declared_gaps_only():
    picked = tn.select(ROWS)
    assert [r["question"] for r in picked] == ["מותר למפקד לחפש לי בתיק?", "מי מאשר תשמ\"ש?"]
    assert picked[0]["gap"] == "refused" and picked[1]["gap"] == "marker"


def test_a_rare_word_in_an_unserved_order_is_a_possible_miss():
    idx = tn.build_index(DOCS)
    row = {"question": "מותר למפקד לחפש לי בתיק?", "role": "soldier", "gap": "refused"}
    res = tn.analyze(row, idx, window_fn=lambda q, r: ["Y-LEAVE"], near_fn=lambda q, r, s: [])
    assert res["verdict"] == "ייתכן פספוס", res
    assert res["lexical"] and res["lexical"][0]["doc"] == "X-SEARCH"


def test_the_same_order_served_is_answered_as_missing_not_a_miss():
    idx = tn.build_index(DOCS)
    row = {"question": "מותר למפקד לחפש לי בתיק?", "role": "soldier", "gap": "refused"}
    res = tn.analyze(row, idx, window_fn=lambda q, r: ["X-SEARCH"], near_fn=lambda q, r, s: [])
    assert res["verdict"] == "בחלון ונענה כחסר", res


def test_nothing_in_the_corpus_is_probably_really_missing():
    idx = tn.build_index(DOCS)
    row = {"question": "מה צבע הדגל של הצוללות?", "role": "soldier", "gap": "refused"}
    res = tn.analyze(row, idx, window_fn=lambda q, r: [], near_fn=lambda q, r, s: [])
    assert res["verdict"] == "כנראה באמת אין", res


def test_defective_spelling_bridges_the_soldiers_verb():
    assert tn._word_forms("וחיפשו") & tn._forms("מפקד רשאי לחפש אצל חייל")


def test_the_report_stays_out_of_git():
    gi = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "night/out/*" in gi
    assert tn.OUT == ROOT / "night" / "out"
    assert tn.misses_path().parent.name == "out"


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as e:
                fails += 1
                print(f"FAIL {name}: {str(e)[:400]}")
    raise SystemExit(1 if fails else 0)
