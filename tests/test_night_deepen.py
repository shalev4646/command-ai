# -*- coding: utf-8 -*-
"""night.deepen: targets from a file, and digit-free blocks deepened under the
digit-free gate instead of being skipped.

Until 2026-08-18 the deepening targets were a dict in the module and any order
whose digits did not survive extraction was skipped — which, after 71 orders
were curated digit-free (`night.curate --digit-free`), would have made every one
of them un-deepenable although its block was written under a gate that makes
deepening safe. These pin the two seams: the loader's shape, and the rule
"broken digits + digit-free block ⇒ deepen digit-free; broken digits + plain
block ⇒ skip".
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from night import deepen
from night.curate import check


def test_load_targets_shape():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "t.json"
        p.write_text(json.dumps({
            "PM-35.0402": [["חייל צריך להיות בבית — איזה אישור?", ["חופשה מיוחדת מטעמים"]],
                           ["כמה ימים?", []]],
        }, ensure_ascii=False), encoding="utf-8")
        t = deepen.load_targets(p)
    assert list(t) == ["PM-35.0402"]
    assert t["PM-35.0402"][0] == ("חייל צריך להיות בבית — איזה אישור?", ["חופשה מיוחדת מטעמים"])
    assert t["PM-35.0402"][1] == ("כמה ימים?", [])


def test_digit_free_section_is_recognised_both_ways():
    assert deepen._is_digit_free({"id": "key-facts-nodigits", "clauses": []})
    assert deepen._is_digit_free({"id": "key-facts", "digit_free": True, "clauses": []})
    assert not deepen._is_digit_free({"id": "key-facts", "clauses": []})


def test_digit_free_gate_rejects_a_number_in_added_clauses():
    # the same gate deepen now passes for a digit-free order: a digit or a
    # spelled-out quantity in the ADDED clause is a rejection, exactly as in
    # curation, so a deepened digit-free block cannot smuggle a repaired number
    raw = "מפקד היחידה רשאי לאשר לחייל חופשה מיוחדת מטעמים משפחתיים " * 5
    good = {"clauses": [{"number": "מי מאשר", "text":
            "מפקד היחידה רשאי לאשר לחייל חופשה מיוחדת מטעמים משפחתיים " * 2}]}
    bad = {"clauses": [{"number": "כמה ימים", "text":
           "מפקד היחידה רשאי לאשר לחייל חופשה מיוחדת של שבעה ימים מטעמים משפחתיים " * 2}]}
    assert check(good, raw, digit_free=True)[0] == []
    assert check(bad, raw, digit_free=True)[0] != []


def test_windows_short_order_passes_whole():
    text, missing = deepen.windows("מילה " * 100, ["לא קיים"])
    assert missing == [] and text.split() == ["מילה"] * 100


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
    print("all deepen tests passed")
