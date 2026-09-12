# -*- coding: utf-8 -*-
"""Stage 6 — flag graded answers the ruler may have wronged, before they count
as failures. (night/PLAN_ROUND4.md, שלב 6)

Why it exists, measured 2026-09-12:
  * two samples of the SAME configuration (arms lack3 and clean3) differed by
    ±3 full answers of 72 — the noise floor of grader + sampling;
  * in clean3, three of the four "losses" against the base carried the same
    ruling and cited the same orders as the base answer that had scored full
    (rs023 „אסור בתנאים" from PM-33.0213, rs037 the same timeline, rs026 the
    same two orders). The grader read the answer's own gap line — the one
    rule 2ב makes the model write — as "not answered".
A ruler that fails identical substance eats every gain a mechanism can
produce, so every ruling-bearing answer that scored short is flagged for a
human before it is counted.

Two rules, deterministic and free:
  A  ruling-scored-short   the answer opens with a ruling (**פסיקה:** /
                           **תשובה:**) that is not a refusal, and the grade
                           left at least one part unanswered
  B  same-substance-loss   paired: the base answer scored full, the new one
                           scored short, and both cite the same orders

A flag never changes a grade. The manual verdicts live in
night/out/review_<tag>.json — {id: {"answered_parts": n, "note": "..."}} —
and report_goal prints strict twice, as graded and as reviewed, so the
historical ruler stays and the correction is visible beside it.

    python -m night.review grade-clean3                 # rule A
    python -m night.review grade-clean3 grade-second4   # rules A + B
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common import safe_print
from night import config as C

# MUST stay in step with backend._RULING_LINE / _REFUSAL_OPENERS
# (tests/test_review.py pins it): the same words that decide the UI chip decide
# what counts as a ruling here.
_RULING = re.compile(r"\*\*(?:פסיקה|תשובה):\*\*\s*([^\n]*)")
REFUSAL_OPENERS = ("המידע לא קיים", "לא נמצא", "לא קיים")
# A ruling line that opens by saying the orders do not set the thing asked is
# a declared gap in a ruling's clothes, not a ruling — rule 2 tells the model
# to "present the rule and say what the orders do not set", and the grader's
# zero on such a line is usually right (rs001, rs047 — adjudicated no rule).
NEGATIVE_OPENERS = ("הפקודות אינן", "הפקודות שסופקו אינן", "הפקודות לא", "אין בפקודות",
                    "אין בקטעים", "לא נמצאה")
_ORDER = re.compile(r"(?:PM-)?\d\d\.\d{4}|HKA-[\d-]+|\d\d-\d\d-\d\d|CHOK-[A-Z-]+\d*")


def opening_ruling(answer: str) -> str | None:
    """The ruling clause an answer opens with, or None for a refusal / no label."""
    m = _RULING.search(answer or "")
    if not m:
        return None
    clause = m.group(1).strip("* ").strip()
    if not clause or clause.startswith(REFUSAL_OPENERS) or clause.startswith(NEGATIVE_OPENERS):
        return None
    return clause


def cited_orders(answer: str) -> set[str]:
    return set(_ORDER.findall(answer or ""))


def _short(row: dict) -> bool:
    g = row.get("grade") or {}
    parts = g.get("parts") or []
    return bool(parts) and int(g.get("answered_parts") or 0) < len(parts)


def _full(row: dict) -> bool:
    g = row.get("grade") or {}
    parts = g.get("parts") or []
    return bool(parts) and int(g.get("answered_parts") or 0) == len(parts)


def flag_rows(rows: list[dict], base_rows: list[dict] | None = None) -> dict[str, list[str]]:
    """id -> reasons. Pure."""
    base = {r["id"]: r for r in (base_rows or []) if r.get("id")}
    out: dict[str, list[str]] = {}
    for r in rows:
        qid = r.get("id")
        if not qid or not _short(r):
            continue
        ruling = opening_ruling(r.get("answer") or "")
        reasons = []
        if ruling:
            reasons.append(f"ruling-scored-short: {ruling[:60]}")
        b = base.get(qid)
        if b is not None and _full(b):
            # no ruling required here: rs037 lost its full score while giving
            # the same timeline as the base, behind a "the orders do not set
            # a number of days" opening — the shared citations are the signal
            same = cited_orders(r.get("answer")) & cited_orders(b.get("answer"))
            if same:
                reasons.append(f"same-substance-loss: base full, both cite {sorted(same)}")
        if reasons:
            out[qid] = reasons
    return out


def review_path(tag: str) -> Path:
    return C.OUT / f"review_{tag}.json"


def load_review(tag: str) -> dict[str, dict]:
    p = review_path(tag)
    if not p.exists():
        return {}
    data = json.loads(p.read_text(encoding="utf-8"))
    return {k: v for k, v in data.items() if isinstance(v, dict) and "answered_parts" in v}


def apply_review(rows: list[dict], review: dict[str, dict]) -> list[dict]:
    """Rows with the reviewed `answered_parts` — copies, capped at the part
    count, untouched where no verdict exists."""
    out = []
    for r in rows:
        v = review.get(r.get("id"))
        if not v:
            out.append(r)
            continue
        g = dict(r.get("grade") or {})
        n = len(g.get("parts") or [])
        g["answered_parts"] = max(0, min(n, int(v["answered_parts"])))
        g["reviewed"] = True
        out.append({**r, "grade": g})
    return out


def main(argv: list[str]) -> int:
    if not argv:
        safe_print("usage: python -m night.review <tag> [base_tag]")
        return 1
    tag = argv[0]
    rows = C.read_jsonl(C.OUT / f"grades_{tag}.jsonl")
    base = C.read_jsonl(C.OUT / f"grades_{argv[1]}.jsonl") if len(argv) > 1 else None
    flags = flag_rows(rows, base)
    review = load_review(tag)
    safe_print(f"[review] {tag}: {len(flags)} of {len(rows)} answers flagged; "
               f"{len(review)} carry a manual verdict ({review_path(tag).name})")
    for r in rows:
        if r["id"] not in flags:
            continue
        g = r["grade"]
        mark = "reviewed" if r["id"] in review else "OPEN"
        safe_print(f"\n  {r['id']} [{mark}] {g['answered_parts']}/{len(g['parts'])} | {(r.get('clean_q') or r.get('q') or '')[:70]}")
        for why in flags[r["id"]]:
            safe_print(f"     - {why}")
        opening = re.sub(r"\s+", " ", (r.get("answer") or "")[:140])
        safe_print(f"     opening: {opening}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
