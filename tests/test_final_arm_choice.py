# -*- coding: utf-8 -*-
"""night/final_arm keeps the answer the app keeps — no model, no API.

Until 27.09 final_arm._final_rows took the second pass's answer whenever there
was one, while production (app.py, "The second search", with
RETRIEVE_SECOND_PASS_KEEP_RULING on in fly.toml) keeps the first when the retry
regresses to a refusal and the first carried a ruling. On final161v2 the two
differ on exactly rs055 and rs058 (night/head100/RUN_LOG.md 15). Pinned on the
tracked records: with the guard off, _final_rows reproduces the graded file
byte for byte; with it on (production), it differs on those two rows only.

    venv\\Scripts\\python.exe tests\\test_final_arm_choice.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import backend  # noqa: E402  imported first: code defaults, the flag is set per test
from night import config as C  # noqa: E402
from night import final_arm as FA  # noqa: E402

TAG = "final161v2"


def _rows(keep: int) -> dict[str, dict]:
    was = backend.RETRIEVE_SECOND_PASS_KEEP_RULING
    backend.RETRIEVE_SECOND_PASS_KEEP_RULING = keep
    try:
        return {r["id"]: r for r in FA._final_rows(TAG)}
    finally:
        backend.RETRIEVE_SECOND_PASS_KEEP_RULING = was


def _official() -> dict[str, dict]:
    return {r["id"]: r for r in C.read_jsonl(C.OUT / f"probe_{TAG}.jsonl")}


def test_without_the_guard_the_graded_file_is_reproduced():
    rows, official = _rows(0), _official()
    assert set(rows) == set(official) and len(rows) == 72
    for i, r in rows.items():
        assert r["answer"] == official[i]["answer"], i
        assert bool(r.get("second_pass")) == bool(official[i].get("second_pass")), i
    assert sum(1 for r in rows.values() if r.get("second_pass")) == 56


def test_production_logic_keeps_the_ruling_on_rs055_and_rs058_only():
    rows, official = _rows(1), _official()
    p1 = {r["id"]: r for r in C.read_jsonl(C.OUT / f"probe_{TAG}_p1.jsonl")}
    p2 = {r["id"]: r for r in C.read_jsonl(C.OUT / f"probe_{TAG}_p2.jsonl")}
    differ = sorted(i for i, r in rows.items() if r["answer"] != official[i]["answer"])
    assert differ == ["rs055", "rs058"], differ
    for i in differ:
        r = rows[i]
        assert r["kept"] == "kept_ruling" and not r.get("second_pass")
        assert r["answer"] == p1[i]["answer"] and r["second_answer"] == p2[i]["answer"]
    assert sum(1 for r in rows.values() if r.get("second_pass")) == 54
    assert {r["kept"] for r in rows.values()} == {"no_gap", "second", "kept_ruling"}


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
    print("all final-arm choice tests passed")
