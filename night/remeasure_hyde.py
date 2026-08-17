"""Arm B of the hypothetical-answer measurement: the same questions, flag on.

Exists as a module rather than an env-var incantation because two things have
to happen before `night.remeasure` imports, and getting either wrong produces a
number that looks fine and measures something else:

  the flag       `backend.RETRIEVE_HYDE` is read at import, so setting it after
                 the import silently measures the control twice
  the cache      the hypotheticals were generated and BOOKED by `night.hyde`;
                 without preloading them, `backend.hypothetical` regenerates
                 every one through the API outside the ledger

    REMEASURE_TAG=hyde30 REMEASURE_BEFORE=base30b python -m night.remeasure_hyde
"""
from __future__ import annotations

import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import backend
from night import config as C
from night import hyde

backend.RETRIEVE_HYDE = True
if backend.HYDE_EXTRA_CHUNKS <= 0:
    raise SystemExit("HYDE_EXTRA_CHUNKS is 0 — arm B would be identical to arm A")
C.log(f"[remeasure_hyde] flag on, {hyde.preload_backend()} hypotheticals preloaded, "
      f"+{backend.HYDE_EXTRA_CHUNKS} chunk(s) per question")

# A question with no cached hypothetical would be bought live and unbooked. Fail
# loudly instead: the arm is only comparable if every question got the same
# treatment, and a partial one is worse than none.
_real = backend.hypothetical


def _cached_only(question: str) -> str:
    if question not in backend._hyde_cache:
        raise SystemExit(f"no cached hypothetical for {question[:60]!r} — "
                         f"run night.hyde generation for this sample first")
    return backend._hyde_cache[question]


backend.hypothetical = _cached_only

from night import remeasure  # noqa: E402  (must import AFTER the flag is set)

if __name__ == "__main__":
    remeasure.run()
