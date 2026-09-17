# -*- coding: utf-8 -*-
"""The second pass's clause finder (RETRIEVE_LACK_CLAUSES) and its regression
guard (RETRIEVE_SECOND_PASS_KEEP_RULING). Both ship OFF; these tests pin what
each does when turned on, and that OFF is byte-identical to before.

Why the finder exists: on the realstyle set (night/out/adjudication_realstyle.json)
11 of 45 zeros had the answering ORDER in the window and its answering CLAUSE
not. Scored free, the lack statement's character 4-grams rank that clause
first among the clauses of the orders already in the window in 7 of 13.

    venv\\Scripts\\python.exe tests\\test_lack_clauses.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import backend
from night import why_default as W

# a two-order window: the answering clause is in the SECOND order, and it is
# not the clause that order contributed to the window
DOC_A = {"document_id": "A.1", "title": "מסדר בוקר", "roles": ["soldier"],
         "sections": [{"id": "key-facts", "title": "עיקרי הפקודה", "clauses": [
             {"number": "מתי מתקיים מסדר בוקר", "text": "מסדר הבוקר יתקיים בכל יום בתחילת יום הפעילות ביחידה."},
         ]}]}
DOC_B = {"document_id": "B.2", "title": "טיפול רפואי בחייל", "roles": ["soldier"],
         "sections": [{"id": "key-facts", "title": "עיקרי הפקודה", "clauses": [
             {"number": "אני מחוץ ליחידה וזקוק לטיפול דחוף",
              "text": "חייל השוהה מחוץ ליחידתו וזקוק לטיפול רפואי דחוף יפנה למרפאה אזורית."},
             {"number": "איך פונים לטיפול רפואי ביחידה",
              "text": "חייל המעוניין בטיפול רפואי יפנה למפקדו, והמפקד יפנה אותו או יאשר לו לפנות לטיפול במרפאה בשעות פעילותה."},
         ]}]}
LACKED = "הזכות והנוהל לפניית חייל לקבלת טיפול רפואי בתוך יחידתו כשהוא חש ברע"


def _chunk(doc, i):
    s = doc["sections"][0]
    cl = s["clauses"][i]
    return {"doc_id": doc["document_id"], "title": doc["title"], "section": "key-facts",
            "clause": cl["number"], "text": f"{doc['title']} — {s['title']}\nסעיף {cl['number']}: {cl['text']}",
            "score": 0.5}


def _window():
    return [_chunk(DOC_A, 0), _chunk(DOC_B, 0)]


def _with(flag, fn):
    old_flag, old_docs = backend.RETRIEVE_LACK_CLAUSES, backend._docs_for_role
    backend.RETRIEVE_LACK_CLAUSES = flag
    backend._docs_for_role = lambda role: [DOC_A, DOC_B]
    try:
        return fn()
    finally:
        backend.RETRIEVE_LACK_CLAUSES, backend._docs_for_role = old_flag, old_docs


def test_off_is_a_no_op():
    win = _window()
    out = _with(0, lambda: backend.extend_with_lack_clauses(win, LACKED, "soldier"))
    assert out == win and out is win, "OFF must return the very same list"


def test_the_lack_statement_buys_the_answering_clause_of_an_order_already_seated():
    win = _window()
    out = _with(1, lambda: backend.extend_with_lack_clauses(win, LACKED, "soldier"))
    assert out[:2] == win, "appends only — the ranking is untouched"
    assert len(out) == 3, out
    assert out[2]["doc_id"] == "B.2" and out[2]["clause"] == "איך פונים לטיפול רפואי ביחידה", out[2]
    assert out[2]["score"] > 0


def test_a_clause_already_in_the_window_is_never_appended_twice():
    win = _window() + [_chunk(DOC_B, 1)]
    out = _with(3, lambda: backend.extend_with_lack_clauses(win, LACKED, "soldier"))
    keys = [(c["doc_id"], c["clause"]) for c in out]
    assert len(keys) == len(set(keys)), keys


def test_the_cap_is_the_flag_value():
    win = _window()
    out = _with(1, lambda: backend.extend_with_lack_clauses(win, LACKED, "soldier"))
    assert len(out) == 3
    out = _with(5, lambda: backend.extend_with_lack_clauses(win, LACKED, "soldier"))
    # A.1 has one clause (seated), B.2 has two (one seated) — at most one new
    # clause per order with any 4-gram overlap can be added
    assert 3 <= len(out) <= 4, len(out)


def test_it_reads_the_first_window_not_the_swapped_one():
    """After the reserved-seat swap the answering order may be gone from
    `chunks`; the finder must still see it through `first_window`."""
    first = _window()
    swapped = [_chunk(DOC_A, 0)]           # B.2 lost its seat
    out = _with(1, lambda: backend.extend_with_lack_clauses(swapped, LACKED, "soldier", first_window=first))
    assert any(c["doc_id"] == "B.2" for c in out), out


def test_an_empty_lack_statement_buys_nothing():
    win = _window()
    out = _with(2, lambda: backend.extend_with_lack_clauses(win, "", "soldier"))
    assert out == win


def test_the_grams_match_the_calibrated_instrument():
    """The finder is night/why_default's scorer moved into production; the
    normalisation must stay identical or the AUC it was chosen for is not its
    AUC."""
    for text in (LACKED, "סעיף 12: המפקד (או מי שהוסמך לכך) יפנה את החייל — ״טופס 102״",
                 "  רווחים   כפולים \n ושורות"):
        assert backend._lack_grams(text) == W.grams(text), text


def test_the_guard_reads_the_realstyle_regressions():
    """Verbatim openings from night/out/probe_second4.jsonl."""
    first_63 = ("**פסיקה:** אסור בשעת זמן אישי לא-מסווג — כפי שעולה מן הקטעים.\n\n"
                "**מקור:** 21.0113\n\n**טרם במאגר:** סמכות מפקד לתפוס טלפון בזמן אישי.")
    second_63 = "**פסיקה:** המידע לא קיים בפקודות שסופקו — לגבי תפיסת טלפון בזמן אישי."
    first_71 = "**פסיקה:** מותר בתנאים — עצם השתייה אינה אסורה כשלעצמה.\n\n**מקור:** פ\"מ 33.0220"
    second_71 = "**המידע לא קיים בפקודות שסופקו** לגבי העונש עצמו — אין בקטעים סעיף."
    second_09 = "המידע לא קיים בפקודות שסופקו — אין בקטעים כל כלל העוסק בכסף פרטי."
    assert backend.second_answer_regressed(first_63, second_63)
    assert backend.second_answer_regressed(first_71, second_71)
    assert backend.second_answer_regressed(first_71, second_09)
    # a labelled refusal is still a refusal
    assert backend.second_answer_regressed(first_71, "**תשובה:** המידע לא קיים בפקודות שסופקו.")


def test_the_guard_never_holds_a_refusal_or_a_real_second_answer():
    gapped_first = "**פסיקה:** לא נמצא\n\nבפקודות שסופקו אין מענה.\n\n**טרם במאגר:** זכאות."
    refusing_first = "**פסיקה:** המידע לא קיים בפקודות שסופקו — לגבי התוצאה של איחור."
    ruled_first = "**פסיקה:** זכאי בתנאים — לשבע שעות שינה רצופות."
    real_second = "**פסיקה:** זכאי\n**מקור:** פ\"מ 35.0402\n\nנמצא בחיפוש המורחב."
    refusal = "**פסיקה:** המידע לא קיים בפקודות שסופקו."
    assert not backend.second_answer_regressed(gapped_first, refusal), "a 'not found' first answer has nothing to keep"
    assert not backend.second_answer_regressed(refusing_first, refusal)
    assert not backend.second_answer_regressed(ruled_first, real_second), "a real second answer always wins"
    assert not backend.second_answer_regressed("", refusal)
    assert not backend.second_answer_regressed(ruled_first, "")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
    print("all lack-clause tests passed")
