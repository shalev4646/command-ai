# -*- coding: utf-8 -*-
"""The real-miss ratchet: a retrieval miss that was verified and fixed does not come back (27.09).

night/out/real_misses.json holds soldiers' own phrasings that got „not found" although the rule
was in the corpus (added with `python -m night.triage_notfound --add-miss …`). It is gitignored —
users' question text never enters git — and lives in the main checkout's night/out, which every
worktree shares; a copy in this tree's night/out wins. No file ⇒ nothing to check (PASS, said).

  active  — at least one target order must be in the free window (v159 flags, route=∅, no HyDE).
  pending — the fix is not in yet; reported, and flagged for promotion once it reaches.

    venv\\Scripts\\python.exe tests\\test_real_misses.py
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from night import triage_notfound as tn


def _entries():
    p = tn.misses_path()
    if not p.exists():
        return p, []
    return p, json.loads(p.read_text(encoding="utf-8"))


def test_entries_are_well_formed():
    p, rows = _entries()
    ids = set()
    for r in rows:
        assert r.get("id") and r["id"] not in ids, r
        ids.add(r["id"])
        assert r.get("q") and r.get("targets") and r.get("status") in ("active", "pending"), r


def test_active_misses_reach_the_window():
    p, rows = _entries()
    if not rows:
        print(f"[real_misses] no file at {p} — nothing to check")
        return
    tn._prod_env()
    lost, promote = [], []
    for r in rows:
        served = tn.window_docs(r["q"], r.get("role") or "soldier")
        hit = [t for t in r["targets"] if t in served]
        if r["status"] == "active" and not hit:
            lost.append(f"{r['id']} {r['targets']} (window: {served[:8]})")
        if r["status"] == "pending" and hit:
            promote.append(r["id"])
    n_active = sum(1 for r in rows if r["status"] == "active")
    print(f"[real_misses] {len(rows)} entries ({n_active} active) from {p}"
          + (f"; now reaching, promote to active: {promote}" if promote else ""))
    assert not lost, "a fixed miss came back:\n  " + "\n  ".join(lost)


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as e:
                fails += 1
                print(f"FAIL {name}: {str(e)[:800]}")
    raise SystemExit(1 if fails else 0)
