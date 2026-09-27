# -*- coding: utf-8 -*-
"""The coverage ratchet: no wave may silently drop what a block covered (27.09).

Runs night.coverage_audit over the whole corpus (storage/json_store, ~10 s, no model, no
network) and compares it with night/coverage_baseline.json. Fails when an order's block loses
a title term it covered, or when its count of uncovered rules, headings or unverifiable numbers
goes UP. A new order may not arrive with an uncovered title term or a number absent from its
raw_text. Improvements pass; lock them in with

    venv\\Scripts\\python.exe -m night.coverage_audit --write-baseline

A legitimate rise (a re-extracted raw_text, a deliberate trim) is a new baseline written in its
own commit with the reason in the message — never a quiet one.

    venv\\Scripts\\python.exe tests\\test_coverage_ratchet.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from night import coverage_audit as ca


def test_coverage_does_not_regress():
    base = json.loads(ca.BASELINE.read_text(encoding="utf-8"))
    now = ca.ratchet_view(ca.audit_corpus())
    bad = ca.regressions(now, base)
    assert not bad, f"{len(bad)} coverage regression(s):\n  " + "\n  ".join(bad[:20])
    better = sum(1 for k, v in now.items() if k in base and (
        len(v["title"]) < len(base[k]["title"]) or v["rules"] < base[k]["rules"]))
    if better:
        print(f"[ratchet] {better} order(s) improved — lock it in: python -m night.coverage_audit --write-baseline")


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as e:
                fails += 1
                print(f"FAIL {name}: {str(e)[:1500]}")
    raise SystemExit(1 if fails else 0)
