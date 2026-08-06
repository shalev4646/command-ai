# -*- coding: utf-8 -*-
"""Structural tests for distress_guide.py — plain-assert script (no pytest in venv).

Run: venv\\Scripts\\python.exe tests\\test_distress_guide.py
Prints only ASCII (cp1252 console pitfall)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import distress_guide as dg


def test_emergency_first():
    assert dg.EMERGENCY["title"].strip() and dg.EMERGENCY["cite"].strip()
    assert len(dg.EMERGENCY["lines"]) >= 3 and all(l.strip() for l in dg.EMERGENCY["lines"])
    assert dg.DISCLAIMER.strip()


def test_steps_numbered_and_spec4():
    assert [s["n"] for s in dg.STEPS] == list(range(1, len(dg.STEPS) + 1))
    assert len(dg.STEPS) >= 3
    for s in dg.STEPS:
        assert s["title"].strip() and s["cite"].strip(), s["n"]
        assert s["lines"] and all(l.strip() for l in s["lines"]), s["n"]
        assert isinstance(s["how"], list) and all(h.strip() for h in s["how"]), s["n"]
        if s["link"] is not None:
            assert s["link"].startswith("https://") and s["link_label"], s["n"]


def test_forbidden_grounded():
    assert len(dg.FORBIDDEN) >= 3
    for f in dg.FORBIDDEN:
        assert f["title"].strip() and f["cite"].strip()
        assert f.get("tag") is None or f["tag"].strip()


def test_hotlines_verified():
    assert len(dg.HOTLINES) >= 2
    for h in dg.HOTLINES:
        assert h["name"].strip()
        assert (h["phone"] or "").strip() or (h["link"] or "").strip()
        if h["link"] is not None:
            assert h["link"].startswith("https://")


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
