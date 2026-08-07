# -*- coding: utf-8 -*-
"""Structural tests for soldier_distress.py + the shared hotlines source.

Run: venv\\Scripts\\python.exe tests\\test_soldier_distress.py
Prints only ASCII (cp1252 console pitfall)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import distress_guide as dg
import hotlines
import soldier_distress as sd


def test_meta():
    assert sd.DISCLAIMER.strip() and sd.HOTLINES_VERIFIED.strip()
    assert sd.EMERGENCY["title"].strip() and sd.EMERGENCY["sub"].strip()
    assert sd.EMERGENCY["life_threat"].strip()


def test_one_source_of_truth_for_emergency_numbers():
    """The reason hotlines.py exists: two copies of an emergency number is
    how one of them goes stale without anyone noticing. Both the commander's
    guide and the soldier's must resolve to the same object."""
    assert sd.hotlines() == dg.HOTLINES == hotlines.HOTLINES
    assert sd.HOTLINES_VERIFIED == dg.HOTLINES_VERIFIED == hotlines.VERIFIED
    phones = [h["phone"] for h in sd.hotlines()]
    assert phones == ["1201", "1-800-363-363", "055-9571399"]
    assert len(set(phones)) == len(phones)


def test_hotline_shape():
    for h in sd.hotlines():
        assert h["name"].strip() and h["phone"].strip()
        assert h["link"] is None or h["link"].startswith("https://")


def test_cards_grounded_and_ask():
    cards = sd.cards()
    assert len(cards) >= 4
    for c in cards:
        assert c["title"].strip() and c["sub"].strip(), c["title"]
        assert c["cite"].strip(), c["title"]
        assert isinstance(c["how"], list) and c["how"], c["title"]
        assert all(h.strip() for h in c["how"]), c["title"]
        # spec 3.1 — every card hands the user back to the chat
        assert c.get("ask", "").strip(), "card has no ask: " + c["title"]
        # integrity rule 2 — no invented links
        assert c.get("link") is None, c["title"]


def test_every_card_cites_one_of_the_verified_orders():
    """No card may rest on a claim with no clause behind it — the same rule
    that removed 'assur to punish for asking for help' from the commander's."""
    allowed = ["33.0219", "61.0113", "31.0116", "35.0803"]
    for c in sd.cards():
        assert any(doc in c["cite"] for doc in allowed), \
            "card cites nothing verifiable: " + c["title"]


def test_the_card_that_justifies_the_tool_exists():
    """The question that actually stops a soldier from speaking. If this card
    ever disappears the tool is just a phone list."""
    card = next((c for c in sd.cards() if "יפגע לי בשירות" in c["title"]), None)
    assert card is not None, "the confidentiality card is the reason this tool exists"
    assert "61.0113" in card["cite"]


def test_cards_are_copies_not_the_live_list():
    sd.cards().append({"title": "bogus"})
    assert all(c["title"] != "bogus" for c in sd.cards())
    sd.hotlines().append({"phone": "000"})
    assert all(h["phone"] != "000" for h in sd.hotlines())


def test_commander_guide_still_intact_after_the_refactor():
    """hotlines.py was carved out of distress_guide.py — the commander's tool
    must not have lost anything in the move."""
    assert dg.STEPS and dg.FORBIDDEN and dg.DOC.strip()
    assert dg.HOTLINES and dg.HOTLINES_VERIFIED.strip()


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
