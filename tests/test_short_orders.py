# -*- coding: utf-8 -*-
"""INGEST_SHORT_ORDERS (ingestion.pdf_to_json.gate_text): the sparse-text gate keeps
rejecting scans, and — only when the flag is on — accepts a genuinely short order:
at most two pages, letters at least 80% Hebrew, no cancellation stub. Off is
byte-identical to the old gate.

    venv\\Scripts\\python.exe tests\\test_short_orders.py
"""
import os
import sys
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from ingestion import pdf_to_json as P

# a real one-page order, the shape of 36.0314 (about 560 characters)
SHORT_ORDER = (
    'בלמ"ס פקודות מטכ"ל, 1 אפריל 78. שירות קבע - תוספת דריכות מבצעית. כללי. '
    "1. חייל בשירות קבע בכל דירוגי השכר זכאי לתוספת דריכות מבצעית בהתאם לדרגת שכרו. "
    "סכומי התוספת נקבעים, מזמן לזמן, בהתאם למדיניות השכר ולשיעורי תוספת היוקר, בתיאום עם היועץ הכספי לרמטכ\"ל. "
    "2. לתוספת דריכות מבצעית לא יהיה זכאי חייל בשירות קבע, המקבל במשכורתו אחת מתוספות השכר הר\"מ: "
    "א. תוספת פעילות רמה א', ב' או ג'. ב. תוספת טיסה. ג. תוספת צלילה. "
    "3. התוספת תשולם מדי חודש בחודשו יחד עם המשכורת, ותיכלל בבסיס לחישוב הפיצויים."
)
# 32.0104 — a cancellation stub, one page
STUB = ('בלמ"ס פקודות מטכ"ל, 20 אוקטובר 87. חוות דעת תקופתית על חוגרים (טופס 863). '
        'הפקודה בוטלה בחוזר תיקונים 186, ותכניה יוטמעו בפ"מ 32.0101, את הפקודה המקורית ניתן למצוא בארכיון אתר הפקודות.')
# a scan with a partial text layer: mostly Latin noise, little Hebrew
SCAN_NOISE = "abcd efgh ijkl mnop qrst uvwx yz " * 12 + "פקודות מטכ\"ל"
FULL_PAGE = "טקסט של פקודה מלאה. " * 60          # ~1,200 chars, passes the old gate on its own


@contextmanager
def _flag(on: bool):
    old = P.INGEST_SHORT_ORDERS
    P.INGEST_SHORT_ORDERS = on
    try:
        yield
    finally:
        P.INGEST_SHORT_ORDERS = old


def _rejected(pages) -> bool:
    try:
        P.gate_text(pages)
        return False
    except ValueError:
        return True


def test_ships_off():
    assert P.INGEST_SHORT_ORDERS == (os.environ.get("INGEST_SHORT_ORDERS", "0") == "1")


def test_off_is_the_old_gate():
    with _flag(False):
        assert _rejected([SHORT_ORDER]), "off: a short order is still sparse"
        assert _rejected([STUB])
        assert _rejected([SCAN_NOISE])
        assert P.gate_text([FULL_PAGE]) == FULL_PAGE
        assert P.gate_text([FULL_PAGE, FULL_PAGE]) == FULL_PAGE + "\n\n" + FULL_PAGE


def test_on_accepts_a_real_short_order_only():
    with _flag(True):
        assert P.gate_text([SHORT_ORDER]) == SHORT_ORDER
        assert P.gate_text([SHORT_ORDER, SHORT_ORDER]) == SHORT_ORDER + "\n\n" + SHORT_ORDER, "two pages still allowed"
        assert _rejected([STUB]), "a cancellation stub stays out"
        assert _rejected([SCAN_NOISE]), "a partial text layer is not a short order"
        assert _rejected([SHORT_ORDER, SHORT_ORDER, SHORT_ORDER]), "three sparse pages is a scan, not a short order"
        assert P.gate_text([FULL_PAGE]) == FULL_PAGE


def test_the_hebrew_ratio_and_the_marker():
    assert P._hebrew_ratio(SHORT_ORDER) > 0.95
    assert P._hebrew_ratio(SCAN_NOISE) < 0.2
    assert P._CANCELLED.search(STUB)
    assert not P._CANCELLED.search(SHORT_ORDER)
    # a sub-clause that was cancelled inside a live order is not a stub
    assert not P._CANCELLED.search("תוקף סעיף משנה ו' בוטל. 3. מדור יחת\"שים 5 - באר שבע.")


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
