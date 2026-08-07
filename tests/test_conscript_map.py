# -*- coding: utf-8 -*-
"""Structural tests for conscript_map.py — plain-assert script (no pytest in venv).

Run: venv\\Scripts\\python.exe tests\\test_conscript_map.py
Prints only ASCII (cp1252 console pitfall)."""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import conscript_map as cm

TODAY = date(2026, 8, 6)

# Orders whose OCR digits are known-unreliable: a figure must never be quoted
# from one of these (2026-08-06 spec, integrity rule 1).
CORRUPT_DOCS = ["3.0502", "36.0511", "36.0527", "31.0521",
                "33.0309", "33.1104", "33.0145"]


def test_meta():
    assert cm.LAST_VERIFIED.strip() and cm.DISCLAIMER.strip()
    assert list(cm.SECTION_LABELS) == cm.SECTION_ORDER
    assert set(cm.TRACK_ORDER) == set(cm.TRACKS)


def test_map_renders_with_no_input_at_all():
    """The spec's load-bearing rule: the input SHARPENS, it does not OPEN.

    This is what lets the map replace the entitlements calculator, which
    answered without asking anything. A regression here turns a five-second
    question back into a form.
    """
    rows = cm.benefit_rows()
    assert len(rows) >= 10, "empty profile must still render the whole map"
    for r in rows:
        assert r["sub"].strip(), r["title"]
    sections = {r["section"] for r in rows}
    assert "now" in sections and "discharge" in sections


def test_every_row_is_grounded_and_asks():
    for prof in ({}, {"enlist": date(2024, 11, 3), "discharge": date(2027, 5, 2),
                      "track": "lohem", "single": True}):
        for r in cm.benefit_rows(prof, TODAY):
            assert r["section"] in cm.SECTION_ORDER, r["title"]
            assert r["title"].strip() and r["cite"].strip(), r["title"]
            assert isinstance(r["how"], list) and r["how"], r["title"]
            assert all(h.strip() for h in r["how"]), r["title"]
            # spec 3.1 — a card is a funnel into the chat; a row without a
            # question is a dead end
            assert r.get("ask", "").strip(), "row has no ask: " + r["title"]
            # integrity rule 2 — no invented links
            assert r.get("link") is None or r["link"].startswith("https://"), r["title"]


def test_no_digits_quoted_from_ocr_corrupt_orders():
    blob = " ".join(
        r["cite"] + " " + " ".join(r["how"]) for r in cm.benefit_rows()
    )
    for doc in CORRUPT_DOCS:
        assert doc not in blob, "map cites an OCR-corrupt order: " + doc


def test_months_and_days_arithmetic():
    assert cm.months_between(date(2024, 11, 3), date(2026, 8, 6)) == 21
    assert cm.months_between(date(2024, 11, 3), date(2024, 11, 2)) is None
    assert cm.months_between(date(2024, 1, 31), date(2024, 2, 29)) == 0
    assert cm.months_between(None, TODAY) is None
    assert cm.days_between(date(2026, 8, 6), date(2026, 8, 16)) == 10
    assert cm.days_between(date(2026, 8, 16), date(2026, 8, 6)) == 0
    assert cm.add_months(date(2024, 1, 31), 1) == date(2024, 2, 29), "leap clamp"
    assert cm.add_months(date(2025, 1, 31), 1) == date(2025, 2, 28), "non-leap clamp"
    assert cm.add_months(date(2024, 11, 3), 12) == date(2025, 11, 3)


def test_service_stops_accruing_at_discharge():
    """A soldier released in 2023 has not served 67 months."""
    st = cm.service_status(date(2021, 1, 1), date(2023, 10, 1), TODAY)
    assert st["months_served"] == st["total_months"] == 33
    assert st["released"] is True
    assert st["days_left"] == 0


def test_impossible_profile_falls_back_to_general():
    """Discharge before enlistment is a typo, not a soldier. One bad field
    must not produce a confident-looking wrong threshold date."""
    st = cm.service_status(date(2027, 1, 1), date(2025, 1, 1), TODAY)
    assert st["months_served"] is None and st["total_months"] is None
    assert st["grant_date"] is None and st["grant_eligible"] is False
    rows = cm.benefit_rows({"enlist": date(2027, 1, 1),
                            "discharge": date(2025, 1, 1)}, TODAY)
    grant = next(r for r in rows if r["title"] == "מענק שחרור")
    assert "נפתח" not in grant["sub"], "invented a threshold date from bad input"


def test_grant_moves_between_chapters_on_the_axis():
    """The whole point of an axis: a row that has not opened yet lives in
    'soon' with its date, and moves to 'discharge' once the threshold passes."""
    rookie = cm.benefit_rows({"enlist": date(2026, 5, 1),
                              "discharge": date(2029, 2, 1),
                              "track": "oref"}, TODAY)
    grant = next(r for r in rookie if r["title"] == "מענק שחרור")
    assert grant["section"] == "soon" and "1.5.2027" in grant["sub"]

    veteran = cm.benefit_rows({"enlist": date(2024, 11, 3),
                               "discharge": date(2027, 5, 2),
                               "track": "lohem"}, TODAY)
    grant = next(r for r in veteran if r["title"] == "מענק שחרור")
    assert grant["section"] == "discharge"


def test_money_estimate_is_arithmetic_on_user_data():
    est = cm.money_estimate("lohem", 29)
    assert est["grant"] == round(cm.TRACKS["lohem"]["grant"] * 29)
    assert est["deposit"] == round(cm.TRACKS["lohem"]["deposit"] * 29)
    assert est["asof"] == cm.RATES_ASOF
    # no track or no months -> no number invented
    assert cm.money_estimate(None, 29) is None
    assert cm.money_estimate("lohem", None) is None
    assert cm.money_estimate("bogus", 29) is None


def test_grant_threshold_is_the_conscript_one_not_keva():
    """12 months (חוק קליטת חיילים משוחררים), NOT the 24 of מענק שחרורין
    משירות קבע (PM-31.0517) — two different grants in two service tracks."""
    assert cm.GRANT_MIN_MONTHS == 12


def test_single_soldier_row_appears_only_when_relevant():
    plain = [r["title"] for r in cm.benefit_rows({}, TODAY)]
    single = [r["title"] for r in cm.benefit_rows({"single": True}, TODAY)]
    assert "חופשת ביקור קרוב בחו\"ל" not in plain
    assert "חופשת ביקור קרוב בחו\"ל" in single


def test_rows_by_section_orders_and_drops_empties():
    out = cm.rows_by_section({}, TODAY)
    secs = [s for s, _ in out]
    assert secs == [s for s in cm.SECTION_ORDER if s in secs]
    assert all(rows for _, rows in out)


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS", name)
            except AssertionError as exc:
                failures += 1
                print("FAIL", name, "-", exc)
    sys.exit(1 if failures else 0)
