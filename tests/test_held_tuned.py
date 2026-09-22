# -*- coding: utf-8 -*-
"""night/head100/held_tuned.json — the contamination record of the HELD half.

Two held answers were read one by one in the paid review of 22.09 and a fix is
being built against them. From then on every held aggregate has to be reported
twice — with those ids and without them — or a future 'held +N' could be the
fix reading its own homework. No index, no API: the aggregate functions are
pure and the record is a small file.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from night.head100 import tuned as T  # noqa: E402


def _targets():
    return {r["id"]: r for r in json.loads((ROOT / "night/head100/targets.json").read_text(encoding="utf-8"))}


def test_record_exists_and_names_held_ids_with_date_and_reason():
    rec = json.loads(T.TUNED.read_text(encoding="utf-8"))
    rows = _targets()
    assert rec["tuned"], "the record must not be empty once a held id was read one by one"
    for r in rec["tuned"]:
        assert r["id"] in rows, r
        assert rows[r["id"]]["split"] == "held", f"{r['id']} is not a held id — nothing to record"
        assert r["date"] and r["reason"], r
    assert {"hW3a", "hR1db"} <= T.tuned_ids()


def _synthetic():
    rows = {"a": {"split": "held"}, "b": {"split": "held"}, "c": {"split": "held"}, "d": {"split": "dev"}}
    per = [{"id": "a", "doc_in_window": 1, "sect_content": 1, "doc_rank": 1},
           {"id": "b", "doc_in_window": 1, "sect_content": 0, "doc_rank": 11},
           {"id": "c", "doc_in_window": 0, "sect_content": 0, "doc_rank": None},
           {"id": "d", "doc_in_window": 1, "sect_content": 1, "doc_rank": 2}]
    return rows, per


def test_held_aggregate_is_reported_with_and_without_the_tuned_ids():
    rows, per = _synthetic()
    lines = T.held_lines(per, rows, {"b"})
    assert len(lines) == 2
    assert lines[0].startswith("[head100] held: n=3") and "sect(content) 1" in lines[0]
    assert "excl. tuned (1: b)" in lines[1] and "n=2" in lines[1]
    # a gain on the tuned id moves the first line only — the clean number is immune
    per[1]["sect_content"] = 1
    lines2 = T.held_lines(per, rows, {"b"})
    assert "sect(content) 2" in lines2[0]
    assert lines2[1] == lines[1]


def test_without_a_record_the_aggregate_is_printed_once():
    rows, per = _synthetic()
    assert len(T.held_lines(per, rows, set())) == 1
    assert T.tuned_ids(ROOT / "night/head100/does-not-exist.json") == set()


def test_paired_held_count_has_the_clean_twin():
    ids = ["a", "b", "c"]
    before = {"a": {"sect_content": 1}, "b": {"sect_content": 0}, "c": {"sect_content": 0}}
    after = {"a": {"sect_content": 1}, "b": {"sect_content": 1}, "c": {"sect_content": 0}}
    lines = T.held_count_lines(ids, before, after, {"b"})
    assert lines[0].startswith("[head100] held 1/3 -> 2/3")
    assert lines[1].startswith("[head100] held excl. tuned (1: b) 1/2 -> 1/2")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("PASS", name)
