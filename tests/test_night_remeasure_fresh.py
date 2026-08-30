# -*- coding: utf-8 -*-
"""night.remeasure's fresh set: the honest yardstick after the held-out 24 were
read for diagnosis on 2026-08-18. Pins what makes it a yardstick — deterministic,
disjoint from every probed question, red-band blind only, sized by REMEASURE_FRESH_N."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from night import config as C
from night import remeasure as R


def test_fresh_is_disjoint_from_probed_and_deterministic():
    a, b = R.fresh_ids(), R.fresh_ids()
    assert a == b, "fresh set must not depend on run order"
    probed = {r["id"] for r in C.read_jsonl(C.OUT / "grades_baseline.jsonl")}
    assert not (set(a) & probed), "fresh set overlaps a probed question"
    assert not (set(a) & set(R.heldout_ids()))
    assert len(a) == R.FRESH_N


def test_fresh_is_red_band_blind_only():
    sweep = {r["id"]: r for r in C.read_jsonl(C.SWEEP)}
    for i in R.fresh_ids():
        assert sweep[i]["source"] == "blind" and sweep[i]["band"] == "red", i


if __name__ == "__main__":
    # sweep.jsonl is deliberately untracked (regenerable bulk, .gitignore) —
    # on a clean checkout (CI) the instrument has no data to pin, so skip
    # LOUDLY rather than fail. The instrument itself stays strict: an empty
    # pool crashing fresh_ids() is correct in a measurement run.
    if not C.read_jsonl(C.SWEEP):
        print("SKIP: night/out/sweep.jsonl absent (untracked measurement "
              "data) - fresh-set invariants only checkable on a machine "
              "that ran the sweep")
        sys.exit(0)
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
    print("all fresh-set tests passed")
