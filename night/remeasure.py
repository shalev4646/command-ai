"""Re-run the saved baseline questions and report a paired before/after.

The 54 blind questions from the night's probe are on disk with their graded
outcomes. Re-running the same questions after a corpus change gives a paired
comparison — the difference is attributable to the corpus, not to question
variance — which is why this is worth more than a fresh random sample of the
same size and cost.

Only questions whose retrieval actually changed are re-run, plus a small
control group whose retrieval did not. The control measures how much of any
observed movement is just the model sampling differently on identical input;
without it, a few flipped verdicts look like progress.
"""
from __future__ import annotations

import json
import os

from night import config as C
from night.ledger import Ledger
from night.probe import build_requests, run_batch
from night.redband import _outcome, wilson

N_CONTROL = 12
# Hard cap on how many questions get re-asked, because the alternative failure
# is worse than a small sample: run_batch reserves the whole batch up front, so
# a set that does not fit under the ceiling raises BudgetExceeded and produces
# no measurement at all. Capping trades interval width — which is honest and
# reportable — for the run completing. Set to 0 for no cap.
#
# Pairing survives the cap: the same questions are compared before and after, so
# the comparison stays paired and only its precision drops. The cap takes the
# lowest ids so a re-run measures the same subset rather than a fresh draw.
MAX_SAMPLE = int(os.environ.get("REMEASURE_MAX", "0"))

# Each run writes under its own tag. `grades_remeasure.jsonl` from wave 1 is a
# historical record of what the corpus answered at 124 orders; overwriting it to
# save a filename would destroy the only copy of a number that was paid for.
TAG = os.environ.get("REMEASURE_TAG", "remeasure3")
OUT = C.OUT / f"probe_{TAG}.jsonl"

# --- the before side ---------------------------------------------------------
# The comparison is only paired if both sides are graded by the SAME ruler, and
# this repo has two. The pre-2026-08-13 grader was handed the question and the
# answer together and split the question differently every run (63 parts became
# 79; one question went from 1 part to 4), so `grades_baseline.jsonl` and
# `grades_remeasure.jsonl` are not comparable to anything graded since. The
# frozen ruler decomposes from the question alone and caches the result in
# question_parts.json; its rows carry `grade.parts`, which is how a file
# declares which ruler produced it.
#
# Candidates newest first: wave 1's after-side is wave 2's before-side.
# REMEASURE_BEFORE=<tag> names one explicitly, which is how a held-out pair
# (heldout_before -> heldout_after) is compared without touching this list.
BEFORE_CANDIDATES = ("grades_remeasure2.jsonl", "grades_base30.jsonl")

# The frozen 30 are the sample every fix so far was diagnosed against, which
# makes them a tuning set: a fix aimed at their failures will look good on them
# whether or not it generalises. REMEASURE_SET=heldout switches the sample to
# the OTHER blind questions from the same sweep — the ones no diagnosis has ever
# looked at — so a fix can be scored on questions it was not written for.
SAMPLE_SET = os.environ.get("REMEASURE_SET", "frozen")


def heldout_ids() -> list[str]:
    """The blind questions the night probed but the frozen 30 left out.

    Same yellow-band selection as the frozen set (the 30 are its lowest ids),
    so the two halves are comparable; the 419 blind questions in the sweep at
    large are not — most were never selected for probing at all.
    """
    sweep = {r["id"]: r for r in C.read_jsonl(C.SWEEP)}
    probed = {r["id"] for r in C.read_jsonl(C.OUT / "grades_baseline.jsonl")}
    frozen = {r["id"] for r in C.read_jsonl(C.OUT / "grades_base30.jsonl")}
    return sorted(i for i in probed - frozen
                  if sweep.get(i, {}).get("source") == "blind")


# 2026-08-18: the held-out 24 were read question-by-question to build the
# deepening targets, which makes them a tuning set from here on, exactly as the
# frozen 30 became one on 08-16. This is the next honest yardstick: blind
# questions the night never probed. All 54 yellow-band blind questions are
# already spent (30 frozen + 24 held-out), so this draws from the RED band —
# which is 393 of the 449 blind questions, i.e. what most real questions look
# like to the retriever, and never measured for answer quality until now. Not
# comparable to the yellow sets; comparable to itself, before and after.
FRESH_N = int(os.environ.get("REMEASURE_FRESH_N", "24"))


def fresh_ids() -> list[str]:
    """Deterministic: lowest ids per role, roles in the pool's proportion, so a
    re-run measures the same subset."""
    sweep = C.read_jsonl(C.SWEEP)
    probed = {r["id"] for r in C.read_jsonl(C.OUT / "grades_baseline.jsonl")}
    pool = [r for r in sweep if r.get("source") == "blind" and r["id"] not in probed
            and r.get("band") == "red"]
    by_role: dict[str, list[str]] = {}
    for r in sorted(pool, key=lambda r: r["id"]):
        by_role.setdefault(r.get("role") or "?", []).append(r["id"])
    total = sum(len(v) for v in by_role.values()) or 1
    quota = {role: round(FRESH_N * len(ids) / total) for role, ids in by_role.items()}
    # rounding can leave FRESH_N±1; trim/top-up from the largest role
    largest = max(by_role, key=lambda k: len(by_role[k]))
    quota[largest] += FRESH_N - sum(quota.values())
    return sorted(i for role, ids in by_role.items() for i in ids[:quota[role]])


def load_before() -> list[dict]:
    """Newest frozen-ruler measurement, or refuse to run."""
    names = BEFORE_CANDIDATES
    if os.environ.get("REMEASURE_BEFORE"):
        names = (f"grades_{os.environ['REMEASURE_BEFORE']}.jsonl",)
    for name in names:
        if name == f"grades_{TAG}.jsonl":
            continue          # a run is never its own before side
        rows = C.read_jsonl(C.OUT / name)
        # A row the grader dropped (batch item failed) has no parts and simply
        # is not part of the pair; a file where NO row has parts is the old
        # ruler and is refused outright.
        graded = [r for r in rows if (r.get("grade") or {}).get("parts")]
        if graded:
            if len(graded) < len(rows):
                C.log(f"[remeasure] {name}: {len(rows) - len(graded)} ungraded "
                      f"rows excluded from the pair")
            C.log(f"[remeasure] before side: {name} ({len(graded)} questions, "
                  f"{sum(len(r['grade']['parts']) for r in graded)} parts)")
            return graded
    raise SystemExit(
        "no frozen-ruler baseline on disk — the files that exist were graded by "
        "the drifting ruler and cannot be compared against. Re-grade one first.")


def _fingerprint(row: dict) -> str:
    """What retrieval handed the model, reduced to a comparable key.

    Read off the stored `sources` — the distinct orders behind the answer, which
    build_requests records for every run — rather than recomputed. Recomputing
    would mean a router call per question ($0.0025 each) to learn something the
    paid run already wrote down, and it would answer with today's retrieval for
    both sides, which is the one thing this must not do.
    """
    return "|".join(row.get("sources") or [])


def run() -> None:
    ledger = Ledger(C.LEDGER)
    sweep = {r["id"]: r for r in C.read_jsonl(C.SWEEP)}

    if SAMPLE_SET == "custom":
        # An explicit id list, for measuring a change the standing sets cannot
        # see. The fresh/held-out/frozen samples were drawn before the corpus
        # held anything outside GHQ orders, so a reserves compendium or a
        # statute lands in them a question or two at a time and reads as noise.
        # The ids still come from the SAME blind sweep — questions a generator
        # that never saw the new sources already wrote — so this selects the
        # audience, never the wording. REMEASURE_IDS names the file.
        ids = json.loads((C.ROOT / os.environ["REMEASURE_IDS"]).read_text(encoding="utf-8"))
        before = load_before() if os.environ.get("REMEASURE_BEFORE") else []
        by_id = {r["id"]: r for r in before}
        sample = [by_id.get(i, {"id": i, "sources": None}) for i in ids]
        C.log(f"[remeasure] custom set from {os.environ['REMEASURE_IDS']} "
              f"({len(sample)})" + (f", paired against {os.environ['REMEASURE_BEFORE']}"
                                    if before else ", no before side"))
    elif SAMPLE_SET in ("heldout", "fresh"):
        # First held-out/fresh run has no before side by definition — it IS the
        # before side of the next one. Later runs pair against REMEASURE_BEFORE.
        ids = heldout_ids() if SAMPLE_SET == "heldout" else fresh_ids()
        before = load_before() if os.environ.get("REMEASURE_BEFORE") else []
        by_id = {r["id"]: r for r in before}
        sample = [by_id.get(i, {"id": i, "sources": None}) for i in ids]
        label = ("held-out set: blind questions outside the frozen 30"
                 if SAMPLE_SET == "heldout" else
                 "fresh set: red-band blind questions never probed")
        C.log(f"[remeasure] {label} ({len(sample)})" + (f", paired against "
              f"{os.environ['REMEASURE_BEFORE']}" if before else ", no before side"))
    else:
        before = load_before()
        # The sample is not re-chosen. Whoever the before side asked is exactly
        # who the after side asks — that is what makes the difference
        # attributable to the corpus rather than to a different draw of
        # questions. The cap applies only if it is tighter than the frozen set.
        sample = sorted(before, key=lambda r: r["id"])
    if MAX_SAMPLE and len(sample) > MAX_SAMPLE:
        C.log(f"[remeasure] CAPPED at {MAX_SAMPLE} — {len(sample) - MAX_SAMPLE} "
              f"of the set not re-asked; intervals widen accordingly.")
        sample = sample[:MAX_SAMPLE]

    reqs, meta = build_requests([{**sweep[r["id"]]} for r in sample])
    for m, r in zip(meta, sample):
        m["baseline_outcome"] = _outcome(r) if r.get("grade") else None
        m["baseline_sources"] = r.get("sources")
    run_batch(reqs, meta, ledger, OUT, f"probe-{TAG}")

    from night.grade import grade_file
    grade_file(OUT, Ledger(C.LEDGER), TAG)
    if before:
        report()


def report() -> None:
    """Print the paired before/after. Separate from run() so `night.collect`
    can produce it for a batch that landed after its submitting process died."""
    import collections

    after = C.read_jsonl(C.OUT / f"grades_{TAG}.jsonl")
    if not after:
        raise SystemExit(f"nothing graded under tag {TAG}")
    # Pair on the INTERSECTION of what both sides actually graded. A batch item
    # that fails leaves a row with no `answered` flags, and keeping it on one
    # side only moves the denominator: base30b lost one question that way and
    # the report read "20/63 -> 19/60, DENOMINATORS DIFFER — not comparable",
    # when the paired truth was a flat 20/60 -> 19/60. The warning was correct
    # and useless; a pair that drops the unmatched row is correct and readable.
    def graded(r) -> bool:
        return bool((r.get("grade") or {}).get("answered"))

    before_all = {r["id"]: r for r in load_before()}
    both = {r["id"] for r in after if graded(r)} & {
        i for i, r in before_all.items() if graded(r)}
    dropped = [r["id"] for r in after if r["id"] not in both]
    if dropped:
        C.log(f"[remeasure] {len(dropped)} question(s) graded on one side only, "
              f"excluded from the pair: {', '.join(map(str, dropped[:5]))}")
    after = [r for r in after if r["id"] in both]
    before = [before_all[i] for i in (r["id"] for r in after)]

    b_full = sum(1 for r in before if _outcome(r) == "full")
    a_full = sum(1 for r in after if _outcome(r) == "full")
    lo_b, hi_b = wilson(b_full, len(before))
    lo_a, hi_a = wilson(a_full, len(after))
    C.log(f"[remeasure] full answers: {b_full}/{len(before)} "
          f"({100*b_full/max(1,len(before)):.0f}%, {100*lo_b:.0f}-{100*hi_b:.0f}) -> "
          f"{a_full}/{len(after)} ({100*a_full/max(1,len(after)):.0f}%, "
          f"{100*lo_a:.0f}-{100*hi_a:.0f})")

    for name, rows in (("before", before), ("after ", after)):
        c = collections.Counter(_outcome(r) for r in rows)
        n = max(1, len(rows))
        C.log(f"[remeasure] {name} (n={len(rows)}): " + " · ".join(
            f"{k} {c[k]} ({100*c[k]/n:.0f}%)" for k in ("full", "partial", "nothing")))

    # The sensitive metric. A question with four parts that goes from one
    # answered to three has plainly improved, and the three-way outcome calls it
    # `partial` on both sides and reports nothing. Parts are frozen per question,
    # so this denominator is identical on both sides by construction.
    def parts_score(rows):
        return (sum((r.get("grade") or {}).get("answered_parts", 0) for r in rows),
                sum(len((r.get("grade") or {}).get("parts") or []) for r in rows))
    b_ans, b_tot = parts_score(before)
    a_ans, a_tot = parts_score(after)
    C.log(f"[remeasure] question-parts answered: {b_ans}/{b_tot} -> {a_ans}/{a_tot}"
          + ("" if b_tot == a_tot else "   ⚠ DENOMINATORS DIFFER — not comparable"))

    # Recomputed from the before rows rather than read off `baseline_outcome`:
    # that field is stamped at run time from whatever the run treated as its
    # before side, and wave 1 stamped it from the drifting ruler.
    was = {r["id"]: _outcome(r) for r in before}
    flips = [r for r in after if r["id"] in was and _outcome(r) != was[r["id"]]]
    C.log(f"[remeasure] verdicts that moved: {len(flips)}/{len(after)}")
    for k, v in collections.Counter(
            f'{was[r["id"]]}->{_outcome(r)}' for r in flips).most_common():
        C.log(f"[remeasure]   {k}: {v}")

    # A flip on a question whose retrieval did not move is the model sampling
    # differently on identical input, not the corpus working.
    known = [r for r in after if r.get("baseline_sources") is not None]
    if not known:
        C.log("[remeasure] no control split — the before side predates "
              "baseline_sources, so 'did retrieval move' is unknown, not 'no'")
        ctrl = ctrl_flips = []
    else:
        ctrl = [r for r in known
                if _fingerprint(r) == "|".join(r["baseline_sources"])]
        ctrl_flips = [r for r in ctrl if r["id"] in was
                      and _outcome(r) != was[r["id"]]]
        C.log(f"[remeasure] retrieval unchanged for {len(ctrl)}/{len(known)} "
              f"(controls); {len(ctrl_flips)} of them flipped anyway")

    (C.OUT / f"{TAG}_summary.json").write_text(json.dumps(
        {"tag": TAG, "before_full": b_full, "before_n": len(before),
         "after_full": a_full, "after_n": len(after),
         "before_parts": [b_ans, b_tot], "after_parts": [a_ans, a_tot],
         "flips": len(flips), "controls": len(ctrl),
         "control_flips": len(ctrl_flips)}, ensure_ascii=False, indent=1),
        encoding="utf-8")


if __name__ == "__main__":
    run()
