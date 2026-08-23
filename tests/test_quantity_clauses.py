# -*- coding: utf-8 -*-
"""backend.extend_with_quantity_clauses — the ceiling clause for a how-much
question.

⚠ הלקח מ-`perf/hyde-prefetch`: חמש בדיקות עברו שם על no-op מפני ש-`RETRIEVE_HYDE`
כבוי בקוד ואיש לא הדליק אותו. כל בדיקה כאן שמצפה להתנהגות **מדליקה את הדגל
במפורש** דרך `_with_flag`, ובדיקה אחת בודקת דווקא שהכבוי הוא no-op מוחלט.
"""
import sys
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import backend


@contextmanager
def _with_flag(n: int, docs: list[dict] | None = None):
    """הדגל ומאגר-המסמכים, מוחזרים למקום גם כשבדיקה נופלת."""
    old_flag, old_docs = backend.RETRIEVE_QUANTITY_CLAUSES, backend._docs_for_role
    backend.RETRIEVE_QUANTITY_CLAUSES = n
    if docs is not None:
        backend._docs_for_role = lambda role: docs
    try:
        yield
    finally:
        backend.RETRIEVE_QUANTITY_CLAUSES = old_flag
        backend._docs_for_role = old_docs


# פקודה סינתטית בצורת המאגר: בלוק key-facts שבו סעיף אחד נושא סכומים ואחד לא.
DOC = {
    "document_id": "99.0001", "title": "פקודת בדיקה",
    "sections": [{"id": "key-facts", "title": "עיקרי הפקודה", "clauses": [
        {"number": "למה נועדה הקרן", "text": "הקרן מיועדת לרווחת הפרט."},
        {"number": "כמה מותר להוציא",
         "text": "שי לחייל מאושפז - עד 15 אחוזים משכר טוראי. מתנה לחייל "
                 "משתחרר - עד 30 אחוזים משכר טוראי."},
        {"number": "מי מאשר", "text": "כל הוצאה טעונה אישור מראש של המפקד."},
    ]}],
}
OTHER = {
    "document_id": "99.0002", "title": "פקודה שאינה בחלון",
    "sections": [{"id": "key-facts", "title": "עיקרי הפקודה", "clauses": [
        {"number": "תקרה אחרת", "text": "עד 90 ימים לכל היותר."}]}],
}
WINDOW = [{"doc_id": "99.0001", "section": "key-facts",
           "clause": "למה נועדה הקרן", "text": "פקודת בדיקה — הקרן מיועדת לרווחת הפרט.",
           "score": 0.4}]
ASKS = "מה המקסימום שאני יכול להוציא על חייל?"


def test_the_flag_off_is_a_total_no_op():
    with _with_flag(0, [DOC]):
        out = backend.extend_with_quantity_clauses(list(WINDOW), ASKS, "commander")
    assert out == WINDOW, out


def test_a_question_that_does_not_ask_how_much_gets_nothing():
    with _with_flag(3, [DOC]):
        for q in ("מותר להכניס נרגילה לבסיס?",
                  "כמה שיותר מהר אני רוצה לצאת הביתה, מה עושים?",
                  "איך מגישים בקשה לשינוי שיבוץ?"):
            out = backend.extend_with_quantity_clauses(list(WINDOW), q, "commander")
            assert out == WINDOW, q


def test_the_amount_clause_of_an_order_in_the_window_is_appended():
    with _with_flag(3, [DOC]):
        out = backend.extend_with_quantity_clauses(list(WINDOW), ASKS, "commander")
    assert len(out) == 2, out
    assert "15 אחוזים" in out[1]["text"], out[1]["text"]
    assert out[1]["clause"] == "כמה מותר להוציא"


def test_a_clause_with_no_amount_stays_out():
    with _with_flag(9, [DOC]):
        out = backend.extend_with_quantity_clauses(list(WINDOW), ASKS, "commander")
    assert all("טעונה אישור מראש" not in c["text"] for c in out), out


def test_only_orders_that_already_earned_a_seat_are_read():
    """The extension buys the clause the ranking missed — not an order the
    ranking rejected. 99.0002 has a ceiling and no seat; it must stay out."""
    with _with_flag(9, [DOC, OTHER]):
        out = backend.extend_with_quantity_clauses(list(WINDOW), ASKS, "commander")
    assert all(c["doc_id"] != "99.0002" for c in out), out


def test_the_cap_is_the_flag():
    many = dict(DOC, sections=[{"id": "key-facts", "title": "עיקרי הפקודה", "clauses": [
        {"number": f"תקרה {i}", "text": f"עד {10 + i} ימים."} for i in range(5)]}])
    with _with_flag(2, [many]):
        out = backend.extend_with_quantity_clauses(list(WINDOW), ASKS, "commander")
    assert len(out) == len(WINDOW) + 2, len(out)


def test_the_window_is_never_reordered():
    """Every extension in backend.py relies on this: the question's own ranking
    is what the gate measures, and an append must not touch it."""
    window = WINDOW + [{"doc_id": "99.0001", "section": "chunk1", "clause": "1",
                        "text": "טקסט אחר", "score": 0.3}]
    with _with_flag(3, [DOC]):
        out = backend.extend_with_quantity_clauses(list(window), ASKS, "commander")
    assert out[:len(window)] == window, out[:len(window)]


def test_the_demand_pattern_reads_a_demand_and_not_a_topic():
    """הדפוס מכוון להיות מסנן-מקדים זול ולא הכרעה. „מקסימום" בשימוש תיאורי
    („מקסימום מאמץ") כן ידליק אותו — וזה מקובל, כי השער השני חוסם: שום דבר
    אינו מוצמד אלא אם פקודה שכבר בחלון נושאת סעיף עם סכום. מה שחייב להישאר
    שקט הוא מה שנצפה בפועל: „כמה" בשימוש שאינו כמותי."""
    fires = ("מה הענישה המקסימלית על אי ציות לפקודה?",
             "כמה ימים של מחבוש אפשר לתת?",
             "מה התקרה להוצאה מקרן היחידה?",
             "עד כמה זמן מותר להחזיק אותי?")
    quiet = ("כמה שיותר מהר אני רוצה לצאת הביתה, מה עושים?",
             "מה קורה אם איחרתי?",
             "כמה אנשים היו במסדר?")
    for q in fires:
        assert backend._QUANTITY_DEMAND.search(q), q
    for q in quiet:
        assert not backend._QUANTITY_DEMAND.search(q), q


def test_an_amount_needs_a_unit_next_to_the_number():
    """A bare digit is a clause number, an order number or a date."""
    assert backend._AMOUNT.search("עד 15 אחוזים משכר טוראי")
    assert backend._AMOUNT.search("לא יעלה על 30 יום")
    assert not backend._AMOUNT.search('פ"מ 35.0115 סעיף 7')
    assert not backend._AMOUNT.search("תוקף סעיף 56 מה-1 ביולי")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
    print("all quantity-clause tests passed")
