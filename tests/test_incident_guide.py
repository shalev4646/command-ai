# -*- coding: utf-8 -*-
"""Structural tests for incident_guide.py — plain-assert script (no pytest in venv).

Run: venv\\Scripts\\python.exe tests\\test_incident_guide.py
Prints only ASCII (cp1252 console pitfall)."""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import incident_guide as ig


def test_events_nonempty_unique():
    keys = [k for k, _ in ig.EVENTS]
    assert len(keys) >= 3 and len(keys) == len(set(keys))
    assert all(lbl.strip() for _, lbl in ig.EVENTS)
    assert ig.STANDING_WARNING["text"].strip() and ig.STANDING_WARNING["cite"].strip()
    assert ig.DISCLAIMER.strip()


def test_guides_shape_and_spec4():
    for key, _ in ig.EVENTS:
        g = ig.guide_for(key)
        assert g is not None, key
        assert 2 <= len(g["steps"]) <= 4, key
        for s in g["steps"]:
            assert s["title"].strip() and s["cite"].strip(), key
            assert s["lines"] and all(l.strip() for l in s["lines"]), key
            assert isinstance(s["how"], list) and all(h.strip() for h in s["how"]), key
            if s["link"] is not None:
                assert s["link"].startswith("https://") and s["link_label"], key


def test_arrest_event_has_no_digit_hours():
    # the arrest order's scanned digits are OCR-corrupt, so the arrest event
    # must never cite a numeric hour span (see curation notes)
    g = ig.guide_for("arrest")
    text = " ".join(l for s in g["steps"] for l in s["lines"] + s["how"])
    assert not re.search(r"\d+\s*שעות", text), "arrest event must stay qualitative"


def test_unknown_event():
    assert ig.guide_for("nosuch") is None


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
