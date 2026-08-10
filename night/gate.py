"""Run the 415 retrieval gate cases for free.

eval.py's own runner calls retrieve_for_role WITHOUT a route, so every case
pays a Haiku router call — $1.04 per full run, which makes it unusable as an
inner loop. This runs the identical assertion (expected order in the top-K)
with the router bypassed. That is a slightly harsher setting than production,
since the router's +0.05 bonus is exactly what rescues borderline cases, so a
pass here is strictly stronger evidence than a pass there.
"""
from __future__ import annotations

import json
import sys

import backend
from night import config as C


def load_cases() -> list[tuple[str, str, object, str]]:
    sys.argv = ["gate"]
    import eval as E
    cases = [(r, q, e, "golden") for r, q, e in E.GOLDEN]
    cases += [(r, q, e, "dirty") for r, q, e in E.DIRTY]
    adv = json.loads((C.ROOT / "eval_adversarial.json").read_text(encoding="utf-8"))
    cases += [(p["role"], p["question"], p["expected"], "adversarial") for p in adv]
    return cases


def run(top_k: int = 3) -> dict:
    cases = load_cases()
    by_set: dict[str, list[int]] = {}
    failures = []
    for i, (role, q, expected, tag) in enumerate(cases):
        accepted = tuple(expected) if isinstance(expected, (list, tuple)) else (expected,)
        tops: list[str] = []
        for c in backend.retrieve_for_role(q, role, route=set()):
            if c["doc_id"] not in tops:
                tops.append(c["doc_id"])
        ok = any(e in tops[:top_k] for e in accepted)
        by_set.setdefault(tag, [0, 0])
        by_set[tag][1] += 1
        if ok:
            by_set[tag][0] += 1
        else:
            failures.append({"set": tag, "role": role, "question": q,
                             "expected": expected, "got": tops[:top_k]})
        if (i + 1) % 100 == 0:
            C.log(f"[gate] {i + 1}/{len(cases)}")

    total_ok = sum(v[0] for v in by_set.values())
    C.log(f"[gate] {total_ok}/{len(cases)} pass (router bypassed, top-{top_k})")
    for tag, (ok, n) in by_set.items():
        C.log(f"[gate]   {tag:<12} {ok}/{n}")
    result = {"total": len(cases), "passed": total_ok,
              "by_set": {k: {"passed": v[0], "of": v[1]} for k, v in by_set.items()},
              "failures": failures}
    (C.OUT / "gate.json").write_text(json.dumps(result, ensure_ascii=False, indent=1),
                                     encoding="utf-8")
    return result


if __name__ == "__main__":
    r = run()
    for f in r["failures"][:25]:
        C.log(f"[gate] FAIL [{f['set']}] {f['question'][:64]}")
        C.log(f"[gate]        want {f['expected']}  got {f['got']}")
