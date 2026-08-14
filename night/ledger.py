"""Hard-ceiling spend guard for the overnight gap-closing run.

Every paid call in `night/` goes through here. The ceiling is not advisory:
`reserve()` raises before a call is made, so an 8-hour unattended run cannot
drift past what the user approved while nobody is watching.

Prices are per million tokens, first-party Claude API (2026-08).
Batch API halves everything; cache writes cost 1.25x input, reads 0.1x.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

# Raised on explicit approval, in steps, each one visible in the diff rather
# than bypassed at a call site: 10 -> 12 (red-band probe) -> 20 (launch work).
# The $20 covers, in order of what is actually unblocked:
#   ~$6   finish extending key-facts over the remaining 79 uncovered anchors
#   ~$2   re-measure the saved 54 questions for a paired before/after
#   ~$7   curate wave 1 (85 orders) once the PDFs are downloaded
#   ~$0.5 grow the gate by 3 probes per new order
CEILING_USD = 45.00
PLANNED_USD = 43.00
# Raised 20 -> 24 when wave 1 actually landed, then 24 -> 45 for wave 2, which
# is the first step big enough to measure: 162 orders takes coverage from 28%
# to 64%, where wave 1's 26 orders moved it 22% -> 28% and produced no
# detectable change (20 of 63 question-parts answered before AND after).
#
# The 45 is not an estimate, it is the measured 90th percentile. 37 curations
# were actually paid for here: mean $0.127, median $0.101, p90 $0.232, worst
# single order $0.454. So 162 orders costs $20.60 at the mean and $37.60 if
# every one behaves like the expensive tail, plus $0.29 of gate probes and
# ~$3.80 for four checkpoint measurements. $45 covers the pessimistic case.
#
# The user was offered $80 — enough for all 323 missing orders — and it was cut
# in half deliberately: 161 of those sit in families (civilian employment,
# construction, organisational HPO) that touched none of the measured gaps.
# Buying them would be buying corpus, not answers. Whether they are ever needed
# is a decision to make after wave 2 is measured, not before.

# $ per million tokens: (input, output)
PRICES = {
    "claude-opus-4-8": (5.00, 25.00),
    "claude-opus-5": (5.00, 25.00),
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-haiku-4-5-20251001": (1.00, 5.00),
}
CACHE_WRITE_MULT = 1.25
CACHE_READ_MULT = 0.10
BATCH_MULT = 0.50


class BudgetExceeded(RuntimeError):
    """Raised instead of making a call that would cross the ceiling."""


# KNOWN GAP, measured rather than assumed: calls made *inside* backend —
# `_route_docs` on a cache miss and `_standalone_question` on a typo'd question —
# bill the API without passing through this ledger, because the sweep reuses the
# production code path instead of reimplementing it. Measured at 150 questions
# the leak was under $0.08, worst case $0.37 across the full set, so the run
# books that worst case as spend up front. The ceiling therefore holds, but by
# over-charging rather than by intercepting every call: treat `spent` as an
# upper bound on the true bill, not an exact one.


def cost_usd(model: str, *, input_tokens: int = 0, output_tokens: int = 0,
             cache_write_tokens: int = 0, cache_read_tokens: int = 0,
             batch: bool = False) -> float:
    """Dollar cost of one call. Unknown models price as Opus — the safe side."""
    inp, out = PRICES.get(model, PRICES["claude-opus-4-8"])
    usd = (
        input_tokens * inp
        + output_tokens * out
        + cache_write_tokens * inp * CACHE_WRITE_MULT
        + cache_read_tokens * inp * CACHE_READ_MULT
    ) / 1_000_000
    return usd * (BATCH_MULT if batch else 1.0)


class Ledger:
    """Append-only spend log with a reserve-then-settle protocol.

    Reservations exist because a batch of 150 Opus calls commits the money
    before any usage comes back: reserving the estimate up front means a
    mid-batch crash can never leave the ceiling silently breached, and
    `settle()` refunds the difference once real usage is known.
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._state = self._load()

    def _load(self) -> dict:
        if self.path.exists():
            try:
                return json.loads(self.path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        return {"spent": 0.0, "reserved": 0.0, "entries": []}

    def _save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._state, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path)

    def _merge_disk(self) -> None:
        """Fold in entries written by anyone else since we last read.

        Without this the ledger loses money silently, and always in the unsafe
        direction: a long run holds `_state` in memory for an hour, so a second
        writer's entries are overwritten by the next settle() and the ceiling
        then guards a total that is smaller than what was actually spent. That
        is not hypothetical — an $18.12 carry-forward booked while a curation
        run was live vanished exactly this way, leaving a $24 ceiling willing to
        authorise roughly $42.

        Entries are the source of truth and `spent`/`reserved` are derived from
        them, so a merge cannot double-count: an id seen twice is one entry.
        """
        mine = {e["id"]: e for e in self._state["entries"]}
        for e in self._load()["entries"]:
            # an entry we already have, but settled elsewhere, keeps its actual
            if e["id"] in mine and mine[e["id"]]["actual"] is None:
                mine[e["id"]] = e
            elif e["id"] not in mine:
                mine[e["id"]] = e
        entries = sorted(mine.values(), key=lambda e: e["id"])
        self._state = {
            "spent": sum(e["actual"] for e in entries if e["actual"] is not None),
            "reserved": sum(e["estimate"] for e in entries if e["actual"] is None),
            "entries": entries,
        }

    @property
    def spent(self) -> float:
        return self._state["spent"]

    @property
    def committed(self) -> float:
        """Spent plus not-yet-settled reservations — what the ceiling is checked against."""
        return self._state["spent"] + self._state["reserved"]

    def remaining(self) -> float:
        return max(0.0, CEILING_USD - self.committed)

    def reserve(self, label: str, estimate_usd: float) -> str:
        """Claim budget before a call. Raises BudgetExceeded rather than overspending."""
        with self._lock:
            self._merge_disk()
            if self.committed + estimate_usd > CEILING_USD:
                raise BudgetExceeded(
                    f"{label}: ${estimate_usd:.2f} would put the run at "
                    f"${self.committed + estimate_usd:.2f}, over the ${CEILING_USD:.2f} ceiling "
                    f"(${self.remaining():.2f} left). Shrink the sample and retry."
                )
            # pid-qualified: two processes both at entry 7 would otherwise mint
            # the same id, and the merge would treat the second as a duplicate
            # of the first and drop its cost.
            rid = f"{label}#{os.getpid()}-{len(self._state['entries'])}"
            self._state["reserved"] += estimate_usd
            self._state["entries"].append(
                {"id": rid, "label": label, "estimate": round(estimate_usd, 4), "actual": None}
            )
            self._save()
            return rid

    def settle(self, rid: str, actual_usd: float) -> None:
        """Replace a reservation with the measured cost."""
        with self._lock:
            self._merge_disk()
            for e in self._state["entries"]:
                if e["id"] == rid and e["actual"] is None:
                    e["actual"] = round(actual_usd, 4)
                    break
            # totals are derived from entries, so recompute rather than adjust
            self._state["spent"] = sum(
                e["actual"] for e in self._state["entries"] if e["actual"] is not None)
            self._state["reserved"] = sum(
                e["estimate"] for e in self._state["entries"] if e["actual"] is None)
            self._save()

    def summary(self) -> str:
        lines = [f"spent ${self.spent:.2f} / ceiling ${CEILING_USD:.2f} "
                 f"(planned ${PLANNED_USD:.2f}, ${self.remaining():.2f} left)"]
        for e in self._state["entries"]:
            actual = f"${e['actual']:.3f}" if e["actual"] is not None else f"~${e['estimate']:.3f} (open)"
            lines.append(f"  {e['label']:<28} {actual}")
        return "\n".join(lines)
