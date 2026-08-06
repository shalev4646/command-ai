# -*- coding: utf-8 -*-
"""Structural tests for keva_benefits.py — plain-assert script (no pytest in venv).

Run: venv\\Scripts\\python.exe tests\\test_keva_benefits.py
Prints only ASCII (cp1252 console pitfall)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import keva_benefits as kb


def test_meta():
    assert kb.LAST_VERIFIED.strip() and kb.DISCLAIMER.strip()
    assert list(kb.SECTION_LABELS) == kb.SECTION_ORDER


def test_seniority_leave_monotonic():
    vals = [kb.seniority_leave(y) for y in (0, 1, 3, 6, 12, 25)]
    assert all(v is not None for v in vals)
    days = [v["days"] for v in vals]
    assert days == sorted(days), "leave days must not decrease with seniority"
    assert days[0] == days[1], "year 0 clamps to the first tier"
    for v in vals:
        assert v["cite"].strip() and v["tier_label"].strip()


def test_rows_shape_and_spec4():
    rows = kb.benefit_rows(6, True)
    assert rows, "map must not be empty"
    for r in rows:
        assert r["section"] in kb.SECTION_ORDER, r["key"]
        assert r["title"].strip() and r["cite"].strip(), r["key"]
        assert isinstance(r["how"], list) and r["how"] and all(h.strip() for h in r["how"]), r["key"]
        if r["link"] is not None:
            assert r["link"].startswith("https://") and r["link_label"], r["key"]
        if r["civil"]:
            assert r.get("asof"), "civil row missing asof: " + r["key"]
    keys = [r["key"] for r in rows]
    assert len(keys) == len(set(keys)), "duplicate row keys"
    assert {"annual_leave", "sick_leave", "unpaid_leave", "pension",
            "release_grant", "benefits_return"} <= set(keys)


def test_family_gating():
    base = {r["key"] for r in kb.benefit_rows(6, False)}
    fam = {r["key"] for r in kb.benefit_rows(6, True)}
    assert "family_dental" not in base and "family_dental" in fam
    assert base < fam


def test_sections_covered():
    rows = kb.benefit_rows(10, True)
    used = {r["section"] for r in rows}
    assert used == set(kb.SECTION_ORDER), "every section must have at least one row"


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted({k: v for k, v in globals().items() if k.startswith("test_")}.items()):
        try:
            fn()
            print("PASS " + name)
        except AssertionError as e:
            fails += 1
            print("FAIL " + name + ": " + str(e).encode("ascii", "replace").decode())
    sys.exit(1 if fails else 0)
