# -*- coding: utf-8 -*-
"""Stage 5 — report the goal as the user stated it, beside the strict number.

The strict rubric in night/grade.py gives 0 to an honest negative on purpose:
when crediting them was tested empirically on 2026-08-21, half the "negatives"
turned out to be wrong negatives, and a rule that credits them rewards the
wrong ones too. That ruler is right for measuring retrieval.

It is the wrong ruler for the goal the user sharpened on 2026-08-23 — "every
question gets a full and correct answer". A question the orders genuinely do
not govern is answered correctly by saying so and pointing somewhere real, and
the strict ruler scores that a zero. So: two numbers, never one.

    strict   answered_parts / parts — the ruler every arm so far was measured
             on, unchanged, so the campaign's history stays comparable
    goal     strict + the questions an arbitration pass verified as ones the
             orders do not govern, where the answer said so instead of inventing

A question is credited to `goal` only when all of these hold:

  - it scored zero (`answered_parts == 0`). The adjudication files were written
    about zero-scoring questions, so their verdict describes exactly that case.
    Crediting a partial answer's unanswered half would read a per-question
    verdict as if it were per-part.
  - an adjudication file carries NO_SUCH_RULE or MISSING for it — a verdict
    written by a separate pass against the raw order text, not by the grader
    and not by this module.
  - the grade level is not a fabrication.

A question nobody adjudicated is never credited. That is the whole guard
against inflating the number by calling every silence honest.

`referred` is reported alongside but never folded in: it counts how many of the
credited answers actually told the soldier where to go next. On 2026-08-23 that
number was zero — the answers are honest and end nowhere, which is the case for
the out-of-scope referral table.

Usage: python -m night.report_goal fresh_v99 ho_v99 after_v99
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common import safe_print
from night import config as C

# Verdicts an arbitration pass writes when the app is right not to answer.
#
# NOT_IN_CORPUS is the pilot-150 pass's name for what the earlier two passes
# called MISSING: the answering rule exists somewhere, and the document holding
# it is not ours. Same concept, two passes, two names. Reading only one of them
# charged the app for 12 questions an arbitration had already cleared it of --
# the docs said 45 of 148 were not the app's failure and this module said 33.
UNANSWERABLE = ("NO_SUCH_RULE", "MISSING", "NOT_IN_CORPUS")

# Levels that mean the answer stated plainly what it does not have. Anything
# outside this set on a zero-scoring question is a fabrication or a silence.
HONEST_LEVELS = ("full", "led_known", "refused")

# "The answer sent the reader somewhere." Directive phrasing only: an order's
# own text names "מפקד היחידה" constantly, so mentioning a body is not enough —
# the sentence has to tell the soldier to go there.
_REFERRAL = re.compile(r"(?:יש|ניתן|מומלץ|כדאי|באפשרות[ךכ])\s+לפנות|לפנות\s+ל|פנ[היו]\s+ל")


def verdicts() -> dict[str, str]:
    """question id -> arbitration verdict, from every adjudication file that
    carries ids. The first pass (adjudication.json) is keyed by question prose
    with no id and is left out — matching by text would credit the wrong rows.
    """
    out: dict[str, str] = {}
    for path in sorted(C.OUT.glob("adjudication*.json")):
        try:
            rows = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(rows, list):
            continue
        for r in rows:
            if isinstance(r, dict) and r.get("id") and r.get("verdict"):
                out[r["id"]] = r["verdict"]
    return out


def tally(rows: list[dict], verds: dict[str, str], tag: str = "") -> dict:
    """The whole rule, over graded rows. Pure — no disk, so the credit rule is
    testable on rows written by hand instead of on a $4 measurement."""
    strict = total = credited_parts = referred = referred_parts = 0
    credited: list[str] = []
    uncredited: list[str] = []
    stranded: list[str] = []

    for r in rows:
        g = r.get("grade") or {}
        parts = g.get("parts") or []
        answered = int(g.get("answered_parts") or 0)
        strict += answered
        total += len(parts)
        if answered or not parts:
            continue
        verdict = verds.get(r.get("id"))
        if verdict in UNANSWERABLE and g.get("level") in HONEST_LEVELS:
            credited.append(r["id"])
            credited_parts += len(parts)
            if _REFERRAL.search(r.get("answer") or ""):
                referred += 1
                referred_parts += len(parts)
            else:
                stranded.append(r["id"])
        else:
            uncredited.append(r["id"])

    return {"tag": tag, "strict": strict, "total": total,
            "goal": strict + credited_parts, "credited_parts": credited_parts,
            "credited": credited, "referred": referred, "uncredited": uncredited,
            "served": strict + referred_parts, "referred_parts": referred_parts,
            "stranded": stranded}


def report(tag: str, verds: dict[str, str] | None = None) -> dict:
    """Both numbers for one measured arm, plus the rows behind them."""
    rows = C.read_jsonl(C.OUT / f"grades_{tag}.jsonl")
    if not rows:
        raise SystemExit(f"no grades on disk for {tag} — expected {C.OUT / f'grades_{tag}.jsonl'}")
    return tally(rows, verdicts() if verds is None else verds, tag)


def _pct(n: int, d: int) -> str:
    return f"{n}/{d} ({100 * n / max(1, d):.0f}%)"


def main(tags: list[str]) -> int:
    verds = verdicts()
    safe_print(f"[goal] {len(verds)} questions carry an arbitration verdict; "
               f"{sum(1 for v in verds.values() if v in UNANSWERABLE)} of them "
               f"say the orders do not govern the question\n")
    for tag in tags:
        d = report(tag, verds)
        safe_print(f"[goal] {d['tag']}")
        safe_print(f"         strict  {_pct(d['strict'], d['total'])}"
                   f"   ->   served  {_pct(d['served'], d['total'])}"
                   f"      (goal, referral not required: {_pct(d['goal'], d['total'])})")
        safe_print(f"         credited: {len(d['credited'])} questions "
                   f"({d['credited_parts']} parts) verified as not governed by the orders")
        safe_print(f"         of those, {d['referred']} told the soldier where to turn; "
                   f"{len(d['stranded'])} end nowhere"
                   + ("   <-- the referral gap" if d["stranded"] else ""))
        safe_print(f"         no credit: {len(d['uncredited'])} zero-scoring questions "
                   f"nobody adjudicated (by design)\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:] or ["fresh_v99", "ho_v99", "after_v99"]))
