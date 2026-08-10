"""Hard-ceiling spend guard for the overnight gap-closing run.

Every paid call in `night/` goes through here. The ceiling is not advisory:
`reserve()` raises before a call is made, so an 8-hour unattended run cannot
drift past what the user approved while nobody is watching.

Prices are per million tokens, first-party Claude API (2026-08).
Batch API halves everything; cache writes cost 1.25x input, reads 0.1x.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

CEILING_USD = 10.00          # approved by the user; do not raise in code
PLANNED_USD = 7.80           # what the design budgeted — crossing this warns

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
            if self.committed + estimate_usd > CEILING_USD:
                raise BudgetExceeded(
                    f"{label}: ${estimate_usd:.2f} would put the run at "
                    f"${self.committed + estimate_usd:.2f}, over the ${CEILING_USD:.2f} ceiling "
                    f"(${self.remaining():.2f} left). Shrink the sample and retry."
                )
            rid = f"{label}#{len(self._state['entries'])}"
            self._state["reserved"] += estimate_usd
            self._state["entries"].append(
                {"id": rid, "label": label, "estimate": round(estimate_usd, 4), "actual": None}
            )
            self._save()
            return rid

    def settle(self, rid: str, actual_usd: float) -> None:
        """Replace a reservation with the measured cost."""
        with self._lock:
            for e in self._state["entries"]:
                if e["id"] == rid and e["actual"] is None:
                    self._state["reserved"] -= e["estimate"]
                    self._state["reserved"] = max(0.0, self._state["reserved"])
                    self._state["spent"] += actual_usd
                    e["actual"] = round(actual_usd, 4)
                    break
            self._save()

    def summary(self) -> str:
        lines = [f"spent ${self.spent:.2f} / ceiling ${CEILING_USD:.2f} "
                 f"(planned ${PLANNED_USD:.2f}, ${self.remaining():.2f} left)"]
        for e in self._state["entries"]:
            actual = f"${e['actual']:.3f}" if e["actual"] is not None else f"~${e['estimate']:.3f} (open)"
            lines.append(f"  {e['label']:<28} {actual}")
        return "\n".join(lines)
