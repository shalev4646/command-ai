# -*- coding: utf-8 -*-
"""Layer 1 (night/layer1/CRITERION.md): the units, the held split, the request grouping, the
answer parser, and the reach hit rule. No API: the paid calls are never reached here.

    venv\\Scripts\\python.exe tests\\test_layer1.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from night.layer1 import generate as G
from night.layer1 import reach as R

UNITS = G.build_units()


def test_units_are_deterministic_and_unique():
    again = G.build_units()
    assert [u["uid"] for u in again] == [u["uid"] for u in UNITS]
    assert G.fingerprint(again) == G.fingerprint(UNITS)
    keys = [(u["doc_id"], u["clause"]) for u in UNITS]
    assert len(keys) == len(set(keys)) and len({u["uid"] for u in UNITS}) == len(UNITS)
    assert all(u["text"] and u["role"] in G.PERSONA and u["held_q"] in (0, 1, 2) for u in UNITS)


def test_about_one_clause_in_five_is_held():
    held = sum(u["split"] == "held" for u in UNITS)
    assert 0.15 < held / len(UNITS) < 0.25, held


def test_every_clause_is_asked_once_one_order_and_persona_per_request():
    reqs = G.requests_for(UNITS)
    seen = [u["uid"] for _, us, _ in reqs for u in us]
    assert sorted(seen) == sorted(u["uid"] for u in UNITS)
    for cid, us, prompt in reqs:
        assert 1 <= len(us) <= G.PER_REQUEST
        assert len({(u["doc_id"], u["role"]) for u in us}) == 1
        assert all(u["text"] in prompt for u in us) and G.PERSONA[us[0]["role"]] in prompt


def test_the_parser_keeps_whole_answers_and_skips_only():
    us = UNITS[:3]
    text = ('{"items": [{"n": 1, "questions": ["א?", "ב?", "ג?"], "skip": ""},'
            ' {"n": 2, "questions": [], "skip": "נוהל פנימי"},'
            ' {"n": 3, "questions": ["רק שתיים?", "עוד?"], "skip": ""},'
            ' {"n": 9, "questions": ["x", "y", "z"], "skip": ""}]}')
    got = G.parse(text, us)
    assert got[us[0]["uid"]] == {"questions": ["א?", "ב?", "ג?"], "skip": ""}
    assert got[us[1]["uid"]] == {"questions": [], "skip": "נוהל פנימי"}
    assert us[2]["uid"] not in got, "fewer than three questions is not an answer"
    assert G.parse("not json", us) == {}


def test_held_phrasings():
    dev = next(u for u in UNITS if u["split"] == "dev")
    held = next(u for u in UNITS if u["split"] == "held")
    for u in (dev, held):
        rows = G.rows_for({u["uid"]: u}, {u["uid"]: {"questions": ["a", "b", "c"], "skip": ""}}, {u["uid"]: "r0"})
        ph = [r["phrasing"] for r in rows]
        if u is dev:
            assert ph.count("held") == 1 and ph[u["held_q"]] == "held"
        else:
            assert ph == ["held"] * 3


def test_the_hit_is_a_verbatim_run_of_the_clause():
    text = 'חייל זכאי ל"חופשה" של 24 שעות כדי להשתתף בשמחה משפחתית של קרוב'
    runs = R.runs(text)
    assert all(len(r.split()) == R.RUN for r in runs)
    served = [R._norm('... כי חייל זכאי לחופשה של 24 שעות כדי להשתתף בשמחה ...')]
    assert R.clause_hit(runs, served), "quote marks and spacing do not break the run"
    assert not R.clause_hit(runs, [R._norm("חייל זכאי לחופשה בת יום אחד")])
    assert R.runs("שלוש מילים בלבד") == ["שלוש מילים בלבד"]


def test_pairing_lists_dev_only():
    base = [{"qid": "a", "split": "dev", "phrasing": "dev", "hit_clause": False},
            {"qid": "b", "split": "held", "phrasing": "held", "hit_clause": True}]
    now = [{"qid": "a", "split": "dev", "phrasing": "dev", "hit_clause": True},
           {"qid": "b", "split": "held", "phrasing": "held", "hit_clause": False}]
    p = R.pair(base, now)
    assert p["dev"]["gained_ids"] == ["a"] and p["held_clauses"] == {"n": 1, "gained": 0, "lost": 1}


FT = G.build_fulltext_units()


def test_fulltext_units_are_deterministic_rules_with_their_paragraph():
    again = G.build_fulltext_units()
    assert [u["uid"] for u in again] == [u["uid"] for u in FT] and G.fingerprint(again) == G.fingerprint(FT)
    assert len({u["uid"] for u in FT}) == len(FT) > len(UNITS), "the full text has more rules than the blocks"
    assert all(u["section"] == "fulltext" and isinstance(u["in_block"], bool) and u["context"] for u in FT)
    assert any(u["in_block"] for u in FT) and any(not u["in_block"] for u in FT)
    held = sum(u["split"] == "held" for u in FT)
    assert 0.15 < held / len(FT) < 0.25


def test_fulltext_requests_use_their_own_prompt():
    reqs = G.requests_for(FT[:7])
    assert reqs and all("כלל:" in p and "הקשר:" in p and "כותרת:" not in p for _, _, p in reqs)
    creq = G.requests_for(UNITS[:3])
    assert all("כותרת:" in p for _, _, p in creq), "the curated prompt is untouched"


def test_fulltext_hit_reads_raw_punctuation_and_curated_wording():
    R._init()
    rule = "חייל רשאי לפנות למפקדו בבקשה לחופשה מיוחדת בתוך שבעה ימים מיום האירוע"
    raw = {"doc_id": "X", "section": "chunk3", "clause": "3",
           "text": "T\n.חייל רשאי לפנות למפקדו ,בבקשה לחופשה מיוחדת בתוך שבעה ימים"}
    assert R.fulltext_hit(rule, R.runs(rule, loose=True), [raw]), "punctuation of a raw window does not hide the rule"
    other = {"doc_id": "X", "section": "chunk4", "clause": "4", "text": "T\nמסדר הבוקר יתקיים בכל יום"}
    assert not R.fulltext_hit(rule, R.runs(rule, loose=True), [other])


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
    print("all layer-1 tests passed")
