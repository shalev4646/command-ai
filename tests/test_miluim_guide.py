# -*- coding: utf-8 -*-
"""Unit tests for miluim_guide.py — plain-assert script (no pytest in venv).

Run: venv\\Scripts\\python.exe tests\\test_miluim_guide.py
Prints only ASCII (cp1252 console pitfall)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import miluim_guide as mg


def test_causes():
    keys = [k for k, _ in mg.CAUSES]
    assert keys == ["economic", "studies", "personal"], keys
    assert all(label.strip() for _, label in mg.CAUSES)


def test_guide_shape():
    for key, _ in mg.CAUSES:
        g = mg.guide_for(key)
        steps = g["steps"]
        assert len(steps) == 4, (key, len(steps))
        for s in steps:
            assert s["title"].strip(), (key, s)
            assert s["lines"] and all(ln.strip() for ln in s["lines"]), (key, s)
            assert s["cite"].strip(), (key, s)
            assert "31.0603" in s["cite"] or "31.0605" in s["cite"], (key, s["cite"])


def test_timeline_and_warning():
    assert len(mg.TIMELINE) == 4
    assert mg.STANDING_WARNING["text"].strip()
    assert "31.0603" in mg.STANDING_WARNING["cite"]


def test_letter_key_matches_letters_module():
    from letters import LETTER_TYPES
    assert mg.LETTER_KEY in LETTER_TYPES, mg.LETTER_KEY


def test_personal_routes_away_from_committee():
    # routine personal requests are NOT heard by the ולת"ם committee (31.0603
    # §2) — the personal-cause guide must route to the unit commander /
    # emergency personal committee instead of claiming a committee submission.
    g = mg.guide_for("personal")
    joined = " ".join(ln for s in g["steps"] for ln in [s["title"], *s["lines"]])
    assert "מפקד" in joined or "ועדת פרט" in joined or "פרט" in joined
    assert mg.guide_for("personal")["note"], "personal cause should carry the routing note"


def test_disclaimer_and_doc():
    assert mg.DISCLAIMER.strip()
    assert mg.DOC == 'פ"מ 31.0603'


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
