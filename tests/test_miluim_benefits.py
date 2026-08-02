# -*- coding: utf-8 -*-
"""Unit tests for miluim_benefits.py — plain-assert script (no pytest in venv).

Run: venv\\Scripts\\python.exe tests\\test_miluim_benefits.py
Prints only ASCII (cp1252 console pitfall)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import miluim_benefits as mb


def test_active_status():
    s = mb.active_status(0)
    assert s["active"] is False and s["gap"] == s["threshold"] > 0
    assert s["cite"].strip()
    s2 = mb.active_status(s["threshold"])
    assert s2["active"] is True and s2["gap"] == 0
    s3 = mb.active_status(118)
    assert s3["active"] is True


def test_year_tiers_monotonic_and_arithmetic():
    tiers = mb.year_tiers(46)
    ths = [t["threshold"] for t in tiers]
    assert ths == sorted(ths) and len(ths) == len(set(ths)) and len(ths) >= 3
    for t in tiers:
        assert t["label"].strip()
        if t["threshold"] <= 46:
            assert t["passed"] is True and t["gap"] == 0
        else:
            assert t["passed"] is False and t["gap"] == t["threshold"] - 46


def test_tagmul_estimate():
    assert mb.tagmul_estimate(0) is None
    assert mb.tagmul_estimate(-5) is None
    e = mb.tagmul_estimate(12000)
    assert e is not None
    assert abs(e["daily"] - 400.0) < 0.01  # 12000*3/90
    assert e["min_daily"] <= e["daily"] <= e["max_daily"]
    low = mb.tagmul_estimate(1000)
    assert low["daily"] == low["min_daily"]  # clamped up
    high = mb.tagmul_estimate(99000)
    assert high["daily"] == high["max_daily"]  # clamped down
    assert e["cite"].strip() and e["asof"].strip()


def test_benefit_rows_shape_and_sources():
    rows = mb.benefit_rows(days_year=46, days_3y=118, emp={"employee", "student"},
                           has_salary=True)
    assert rows, "map must not be empty"
    for r in rows:
        assert r["section"] in mb.SECTION_ORDER, r["key"]
        assert r["title"].strip() and r["cite"].strip(), r["key"]
        assert r["how"] and all(h.strip() for h in r["how"]), r["key"]
        if r["civil"]:
            assert r.get("asof"), f"civil row {r['key']} missing asof"
    keys = {r["key"] for r in rows}
    assert "voucher_2026" in keys and "fighter_card" in keys and "tax_credit" in keys


def test_profile_gating():
    base = {r["key"] for r in mb.benefit_rows(46, 118, set(), False)}
    stud = {r["key"] for r in mb.benefit_rows(46, 118, {"student"}, False)}
    selfemp = {r["key"] for r in mb.benefit_rows(46, 118, {"self_employed"}, False)}
    assert "student_rights" not in base and "student_rights" in stud
    assert "self_employed_comp" not in base and "self_employed_comp" in selfemp


def test_sections_and_pointer_and_meta():
    assert list(mb.SECTION_LABELS) == mb.SECTION_ORDER
    assert mb.LOCAL_POINTER["title"].strip() and mb.LOCAL_POINTER["link"].startswith("https://")
    assert mb.LAST_VERIFIED.strip() and mb.DISCLAIMER.strip()


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted({k: v for k, v in globals().items() if k.startswith("test_")}.items()):
        try:
            fn()
            print(f"PASS {name}")
        except AssertionError as e:
            fails += 1
            print(f"FAIL {name}: {e!r}".encode("ascii", "backslashreplace").decode())
    sys.exit(1 if fails else 0)
