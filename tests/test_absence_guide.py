# -*- coding: utf-8 -*-
"""Structural tests for absence_guide.py — plain-assert script (no pytest in venv).

Run: venv\\Scripts\\python.exe tests\\test_absence_guide.py
Prints only ASCII (cp1252 console pitfall)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import absence_guide as ag


def test_branches_and_timeline():
    keys = [k for k, _ in ag.BRANCHES]
    assert keys == ["sadir", "miluim"]
    assert all(lbl.strip() for _, lbl in ag.BRANCHES)
    assert len(ag.TIMELINE) == 4 and all(t.strip() for t in ag.TIMELINE)
    assert ag.STANDING_WARNING["text"].strip() and ag.STANDING_WARNING["cite"].strip()
    assert ag.DISCLAIMER.strip()


def test_guides_shape_and_spec4():
    for key, _ in ag.BRANCHES:
        g = ag.guide_for(key)
        assert g is not None, key
        assert 3 <= len(g["steps"]) <= 5, key
        for s in g["steps"]:
            assert s["title"].strip() and s["cite"].strip(), key
            assert s["lines"] and all(l.strip() for l in s["lines"]), key
            assert isinstance(s["how"], list) and all(h.strip() for h in s["how"]), key
            if s["link"] is not None:
                assert s["link"].startswith("https://") and s["link_label"], key


def test_miluim_branch_has_no_bare_day_thresholds():
    # the reserve order's scanned digits are OCR-corrupt, so the miluim branch
    # must never cite a numeric day threshold (see curation notes)
    import re
    g = ag.guide_for("miluim")
    text = " ".join(l for s in g["steps"] for l in s["lines"] + s["how"])
    assert not re.search(r"\d+\s*ימים", text), "miluim branch must stay qualitative"


def test_unknown_branch():
    assert ag.guide_for("nosuch") is None


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
