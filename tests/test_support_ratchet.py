# -*- coding: utf-8 -*-
"""The support ratchet: no wave may add block sentences the order does not support (27.09).

Runs night.support_audit over the whole corpus (no model, no network) against
night/support_baseline.json. Fails when an order's count of unsupported sentences or of entity
misses (a number outside its supporting passage, a fraction, a rank or acronym absent from the
order) goes UP. Improvements pass; lock them in with

    venv\\Scripts\\python.exe -m night.support_audit --write-baseline

in their own commit. A new block must arrive clean.

    venv\\Scripts\\python.exe tests\\test_support_ratchet.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from night import support_audit as sa


def test_support_does_not_regress():
    base = json.loads(sa.BASELINE.read_text(encoding="utf-8"))
    now = sa.ratchet_view(sa.audit_corpus())
    bad = sa.regressions(now, base)
    assert not bad, f"{len(bad)} support regression(s):\n  " + "\n  ".join(bad[:20])


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
