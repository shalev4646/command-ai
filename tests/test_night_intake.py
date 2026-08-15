# -*- coding: utf-8 -*-
"""Locks `_number_from_filename` against the two ways it has already failed.

The flat names (500405x.pdf) carry the whole number in one digit run; the
dashed Hebrew names (פמ-33-0113-...) split it across two. The first version
grabbed the first 5-7 digits anywhere in the string, which read פמ-33-0113 as
order "011.1202" — a false alarm that buried the real reversed-header hits
check_ids exists to surface.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from night.intake import _number_from_filename


def test_flat_six_digit_names():
    assert _number_from_filename("500405x.pdf") == "50.0405"
    assert _number_from_filename("310214x.pdf") == "31.0214"
    assert _number_from_filename("380101.pdf") == "38.0101"


def test_flat_five_digit_name_keeps_short_family():
    assert _number_from_filename("30105x.pdf") == "3.0105"


def test_dashed_hebrew_names_join_the_two_runs():
    assert _number_from_filename("פמ-33-0113-בחירות-לכנסת.pdf") == "33.0113"
    assert _number_from_filename(
        "פמ-580301-הובלת-מטען-חורג-נוסח-עדכני-פקודה-מבוטלת.pdf") == "58.0301"


def test_title_digit_fragments_do_not_parse():
    # runs like ["7", "12"] are Hebrew-title fragments, not an order number —
    # returning anything here is what buried the real alarms
    assert _number_from_filename("פקודה-מספר-7-בנושא-12.pdf") is None
    assert _number_from_filename("פקודה-כללית.pdf") is None


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
