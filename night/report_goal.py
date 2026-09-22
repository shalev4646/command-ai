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
    served   strict + an honest negative that reached a VERIFIED family door.
             The user's goal is "every question gets a full and correct
             answer", and an answer that reports silence and stops is not one:
             the soldier arrived with a problem and leaves with the same
             problem. This is the number to read against the goal.
    goal     served + an honest negative that reached the catch-all door. That
             door names no address -- it says what the orders do not govern is
             set by the unit, and quotes the escalation route the orders
             define. Procedure, not an answer, so it sits in its own tier.
             The served->goal gap is the count `out_of_scope` asked to be
             watched: on 2026-08-28 it was 40 of 42 on a fresh set.

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

import out_of_scope
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

# Whether the soldier was sent anywhere is NOT a question about the answer's
# prose. The referral strip is rendered by app.py AFTER the model is done, from
# `out_of_scope.destination_for`, and `night/probe.py` measures
# `backend.stream_ai_answer` -- which never sees it. A regex over the answer
# text therefore reports on a surface the app does not use: it said 6 of 45 on
# pilot-150 where the real gate fires on 107 of 107.
#
# So the instrument replays the app's own two gates instead of guessing: the
# ANSWER declares a gap (out_of_scope.declares_gap -- the one predicate the
# chip and the strip read too, since 22.09), and the QUESTION matches a
# family. Both modules are pure strings -- no Streamlit, no network, no API.

# The catch-all family added on 2026-08-26 so no soldier ends up with nothing.
# It names no address: it says what the orders do not govern is set by the
# unit, and quotes the escalation route the orders themselves define.
# Procedure, not an answer -- so it is counted apart from a verified door.
# `out_of_scope` asked for exactly this number to be watched: "how many
# questions reach here. A number that grows means the specific families are
# lagging behind reality."
DEFAULT_FAMILY = "unit_level_default"

# 2026-09-19: the catch-all split in two. `unit_level_default` keeps the
# confident wording for daily-routine topics, where the orders themselves hand
# the rule to the unit (33.0401, 21.0113, 61.0104); `not_in_our_orders` is the
# honest half for everything else, which asserts nothing about where the rule
# lives. Both are catch-alls: neither names a new address, so neither may buy
# a single part of `served`. Counting the split as two "verified families"
# would have moved the number without moving the product -- which is the exact
# failure `night/DOORS_CRITERION.md` was written to prevent.
DEFAULT_FAMILIES = frozenset({DEFAULT_FAMILY, "not_in_our_orders"})


def door(answer: str, question: str) -> str | None:
    """The app's gate, replayed. Family name, or None when nothing fires.

    22.09 (rs041): the answer-side test was "carries a routing marker"; the
    kept second-pass answer opened "**תשובה:** אין בפקודות מספר ימים קבוע…"
    with no rule-2א line and fell through here before the family was ever
    asked. The gate is now `out_of_scope.declares_gap`, measured free against
    every answered marker-less answer on disk (zero captures) -- see there."""
    if not answer or not out_of_scope.declares_gap(answer):
        return None
    return out_of_scope.family_of(question)


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
    testable on rows written by hand instead of on a $4 measurement.

    Three tiers, because two hid the thing worth seeing:

      strict    the orders answered it
      served    strict + an honest negative that reached a VERIFIED family door
      goal      served + an honest negative that reached the catch-all door

    The gap between `served` and `goal` is the count `out_of_scope` asked to be
    watched: every question in it is one the specific families did not know.
    """
    strict = total = credited_parts = 0
    served_parts = default_parts = 0
    credited: list[str] = []
    uncredited: list[str] = []
    stranded: list[str] = []
    defaulted: list[str] = []

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
            fam = door(r.get("answer") or "", r.get("clean_q") or r.get("q") or "")
            if fam is None:
                stranded.append(r["id"])
            elif fam in DEFAULT_FAMILIES:
                defaulted.append(r["id"])
                default_parts += len(parts)
            else:
                served_parts += len(parts)
        else:
            uncredited.append(r["id"])

    return {"tag": tag, "strict": strict, "total": total,
            "goal": strict + served_parts + default_parts,
            "served": strict + served_parts,
            "credited_parts": credited_parts, "credited": credited,
            "referred": len(credited) - len(stranded) - len(defaulted),
            "uncredited": uncredited, "stranded": stranded,
            "defaulted": defaulted, "default_parts": default_parts}


def report(tag: str, verds: dict[str, str] | None = None) -> dict:
    """Both numbers for one measured arm, plus the rows behind them.

    שלב 6 (night/review.py): the flagged rows ride along, and when a manual
    review file exists the tally is computed a second time over the reviewed
    rows — `reviewed` — beside the graded one, never instead of it."""
    from night import review as _review
    rows = C.read_jsonl(C.OUT / f"grades_{tag}.jsonl")
    if not rows:
        raise SystemExit(f"no grades on disk for {tag} — expected {C.OUT / f'grades_{tag}.jsonl'}")
    verds = verdicts() if verds is None else verds
    d = tally(rows, verds, tag)
    d["flagged"] = sorted(_review.flag_rows(rows))
    rev = _review.load_review(tag)
    d["reviewed"] = tally(_review.apply_review(rows, rev), verds, tag) if rev else None
    d["reviewed_rows"] = sorted(rev)
    return d


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
        safe_print(f"         strict {_pct(d['strict'], d['total'])}"
                   f"   ->  served {_pct(d['served'], d['total'])}"
                   f"   ->  goal {_pct(d['goal'], d['total'])}")
        safe_print(f"         credited: {len(d['credited'])} questions "
                   f"({d['credited_parts']} parts) verified as not governed by the orders")
        safe_print(f"         doors: {d['referred']} verified family, "
                   f"{len(d['defaulted'])} catch-all, {len(d['stranded'])} none"
                   + ("   <-- the families are lagging" if d["defaulted"] else ""))
        safe_print(f"         no credit: {len(d['uncredited'])} zero-scoring questions "
                   f"nobody adjudicated (by design)")
        if d.get("flagged"):
            safe_print(f"         review: {len(d['flagged'])} answers carry a ruling and scored short "
                       f"-- read before counting as failures: {', '.join(d['flagged'])}")
        if d.get("reviewed"):
            r = d["reviewed"]
            safe_print(f"         after manual review ({len(d['reviewed_rows'])} rows): "
                       f"strict {_pct(r['strict'], r['total'])}"
                       f"   ->  served {_pct(r['served'], r['total'])}"
                       f"   ->  goal {_pct(r['goal'], r['total'])}")
        safe_print("")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:] or ["fresh_v99", "ho_v99", "after_v99"]))
