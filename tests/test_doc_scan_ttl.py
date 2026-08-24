# -*- coding: utf-8 -*-
"""backend.DOC_SCAN_TTL_SEC — how long a freshness scan stays trusted.

`load_documents` caches the parsed docs but re-earned the cache KEY on every
call: glob + stat over all 294 files, several times per question. The TTL
buys the key instead of the parse.

⚠ הלקח מ-`perf/hyde-prefetch`: חמש בדיקות עברו שם על no-op כי דגל-הפרודקשן
כבוי בקוד ואיש לא הדליק אותו. כאן כל בדיקה שמצפה להתנהגות מדליקה את הדגל
במפורש, ובדיקה אחת בודקת דווקא שהכבוי זהה למה שהיה לפני השינוי.
"""
import sys
import time
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import backend


@contextmanager
def _ttl(seconds: float):
    """הדגל, ומטמון נקי — כדי שבדיקה לא תירש את המצב של קודמתה."""
    old_ttl, old_cache, old_at = (backend.DOC_SCAN_TTL_SEC, backend._docs_cache,
                                  backend._docs_scanned_at)
    backend.DOC_SCAN_TTL_SEC = seconds
    backend._docs_cache = None
    backend._docs_scanned_at = 0.0
    try:
        yield
    finally:
        backend.DOC_SCAN_TTL_SEC = old_ttl
        backend._docs_cache = old_cache
        backend._docs_scanned_at = old_at


@contextmanager
def _count_scans():
    """סופר קריאות-דיסק אמיתיות: כל סריקה עוברת ב-Path.glob."""
    calls = []
    real = Path.glob

    def counting(self, pattern):
        calls.append(pattern)
        return real(self, pattern)

    Path.glob = counting
    try:
        yield calls
    finally:
        Path.glob = real


def test_the_flag_off_scans_every_call_exactly_as_before():
    with _ttl(0), _count_scans() as calls:
        backend.load_documents()
        backend.load_documents()
        backend.load_documents()
    assert len(calls) == 3, calls


def test_within_the_ttl_the_scan_is_skipped():
    with _ttl(30), _count_scans() as calls:
        first = backend.load_documents()
        second = backend.load_documents()
        third = backend.load_documents()
    assert len(calls) == 1, f"expected one scan, got {len(calls)}"
    assert second is first and third is first, "must return the same object"


def test_after_the_ttl_expires_it_scans_again():
    with _ttl(0.05), _count_scans() as calls:
        backend.load_documents()
        time.sleep(0.08)
        backend.load_documents()
    assert len(calls) == 2, calls


def test_the_documents_are_the_same_with_the_flag_on_or_off():
    """התאוצה לא מרשה לשנות את מה שמוגש — אותם מסמכים, אותו סדר."""
    with _ttl(0):
        off = backend.load_documents()
    with _ttl(30):
        on = backend.load_documents()
    assert len(on) == len(off), (len(on), len(off))
    assert [d.get("document_id") for d in on] == [d.get("document_id") for d in off]


def test_a_new_file_within_the_ttl_is_deliberately_not_seen():
    """המחיר, מתועד ולא מוסתר: בתוך החלון הקורפוס קפוא. זה מקובל בפרודקשן —
    `json_store` משתנה שם רק בפריסה, שמתחילה תהליך חדש — ולכן ברירת-המחדל
    בקוד היא 0, שבה צינור-הלילה הכותב לחנות תוך כדי רואה כל שינוי מיד."""
    with _ttl(30), _count_scans() as calls:
        backend.load_documents()
        backend.load_documents()
    assert len(calls) == 1, "a second scan would defeat the purpose"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
    print("all doc-scan TTL tests passed")
