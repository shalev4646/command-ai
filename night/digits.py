"""Which orders came out of extraction with their digits intact?

A parallel content review of 25 curated orders found something the machine gates
could not see: in 12 of them the digits are substituted throughout the BODY, with
a different mapping per document — not the header-only mirroring already known.
One agent decoded 32.0314 against a Hebrew-letter date: "ב' אלול התשע\"א
(5 בספטמבר 9155)" is 1.9.2011, so 9155 stands for 2011.

That breaks every gate at once, and quietly:

  * The citation gate checks that "סעיף 12" exists as a marker in the body — but
    the body's markers are themselves scrambled, so a block that faithfully
    copies "(סעיף 02)" passes and then misleads a soldier holding the order.
  * night/numbers.py verifies each number against raw_text, which on a broken
    document means verifying the corruption.
  * Worst, the model silently REPAIRS some of it from world knowledge. 33.0144's
    source says "פ\"מ 49106.3"; the block says "פ\"מ 33.0316" — a number that
    exists in neither reading. That is the "טופס 55" class: true-sounding,
    not from the order.

So these documents must be kept out of curation entirely, not gated harder.
Curating them produces confident wrong numbers, which is worse than no answer.

Detection uses years, because every order carries them and their valid range is
narrow: a four-digit number in an IDF order is nearly always a year between the
founding and now. When a document's four-digit numbers are overwhelmingly
outside that range, its digits did not survive.

    python -m night.digits            # report, with the labelled set scored
"""
from __future__ import annotations

import io
import re
import sys

# reconfigure rather than re-wrap: a second TextIOWrapper over the same buffer
# closes the first when it is collected, which kills stdout for anything that
# imports two of these modules at once.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import backend

# A four-digit run in an order is a year far more often than anything else.
FOUR = re.compile(r"(?<!\d)(\d{4})(?!\d)")
YEAR_LO, YEAR_HI = 1948, 2026

# Below this share of plausible years, the digits are not trustworthy. Tuned on
# the hand-labelled set in REVIEW_wave1.md; see main() for the sweep.
MIN_PLAUSIBLE = 0.34
MIN_SAMPLE = 3          # too few four-digit runs to judge either way


def year_share(raw: str) -> tuple[float | None, int]:
    """Fraction of four-digit runs that could be a real year, and how many there were."""
    four = [int(y) for y in FOUR.findall(raw)]
    if len(four) < MIN_SAMPLE:
        return None, len(four)
    ok = sum(1 for y in four if YEAR_LO <= y <= YEAR_HI)
    return ok / len(four), len(four)


def trustworthy(doc: dict) -> bool:
    """True only when something positively vouches for this document's digits.

    Neither available signal separates the labelled set on its own — 5 of the 13
    clean orders have a year share as low as the broken ones, and clause
    numbering is absent from several clean orders too. But they fail in
    different places, so either one passing is enough, and on the 25 hand-read
    orders the disjunction lets through 8 of 13 clean and 0 of 12 broken.

    The asymmetry is deliberate. Excluding a sound order costs one key-facts
    block. Curating a broken one produces confident wrong numbers — a discharge
    threshold, a deadline, a rank — that a soldier will act on, and that no
    downstream gate can catch, because every gate checks against the same
    corrupted text. Silence is recoverable; a wrong number is not.
    """
    raw = str(doc.get("raw_text", ""))
    share, _n = year_share(raw)
    if share is not None and share >= MIN_PLAUSIBLE:
        return True
    # intact clause numbering vouches for the digits just as well
    from night.curate import is_numbered
    return is_numbered(raw)


def is_broken(doc: dict) -> bool:
    return not trustworthy(doc)


def main() -> None:
    docs = backend.load_documents()

    # Ground truth from the 25-order content review.
    broken = {"33.0306", "31.0519", "31.0508", "33.0144", "32.0221", "31.0507",
              "32.0314", "31.0317", "35.0219", "33.0914", "31.0214", "35.0314"}
    clean = {"38.0123", "PM-33.0136", "38.0101", "PM-33.0113", "31.0504",
             "58.0301", "31.0117", "33.0117", "31.0202", "35.0115", "33.0913",
             "31.1014", "31.0201"}
    by_id = {d["document_id"]: d for d in docs if d.get("document_id")}

    print("--- threshold sweep against the hand-labelled 25 ---")
    for th in (0.10, 0.20, 0.34, 0.50, 0.70):
        tp = fp = nb = nc = 0
        for i in broken:
            if i not in by_id:
                continue
            nb += 1
            s, _ = year_share(str(by_id[i].get("raw_text", "")))
            tp += s is not None and s < th
        for i in clean:
            if i not in by_id:
                continue
            nc += 1
            s, _ = year_share(str(by_id[i].get("raw_text", "")))
            fp += s is not None and s < th
        print(f"  share<{th:<5} catches {tp}/{nb} broken, false-flags {fp}/{nc} clean")

    flagged = [d for d in docs if is_broken(d)]
    unknown = [d for d in docs
               if year_share(str(d.get("raw_text", "")))[0] is None]
    print(f"\ncorpus: {len(docs)} orders")
    print(f"  digits not trustworthy: {len(flagged)}")
    print(f"  too few years to judge:  {len(unknown)}")
    print(f"  digits look intact:      {len(docs) - len(flagged) - len(unknown)}")


if __name__ == "__main__":
    main()
