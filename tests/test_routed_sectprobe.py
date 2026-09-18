"""night/routed_sectprobe.compare — the corpus gate reads LOST targets, not the total.

A wave that gains three sections and loses one still shows +2 in the total; the
stop rule is about the one that was lost. No API, no network.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from night.routed_sectprobe import compare  # noqa: E402


def _res(**served):
    return {"per": [{"id": i, "sect_content": ok} for i, ok in served.items()]}


def test_a_target_served_before_and_not_after_is_lost():
    lost, gained = compare(_res(rs001=True, rs002=True), _res(rs001=True, rs002=False))
    assert lost == ["rs002"] and gained == []


def test_a_gain_does_not_hide_a_loss():
    before = _res(q1=True, q2=False, q3=False, q4=False)
    after = _res(q1=False, q2=True, q3=True, q4=True)
    lost, gained = compare(before, after)
    assert lost == ["q1"]
    assert gained == ["q2", "q3", "q4"]


def test_a_target_that_disappeared_from_the_run_counts_as_lost():
    lost, gained = compare(_res(q1=True, q2=True), _res(q1=True))
    assert lost == ["q2"] and gained == []


def test_never_served_targets_are_neither_lost_nor_gained():
    lost, gained = compare(_res(q1=False), _res(q1=False))
    assert lost == [] and gained == []


def test_identical_runs_have_no_difference():
    same = _res(a=True, b=False, c=True)
    assert compare(same, same) == ([], [])


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
    print("all routed sectprobe tests passed")
