"""Shared constants and helpers used across app, backend, and ingestion."""
import sys

# Canonical role set — the single source of truth. UI labels/prompts live in
# app.py/backend.py; anything validating or enumerating roles imports this.
ROLES = ("soldier", "commander", "reserve")


# The refusal the answer prompt mandates verbatim when nothing in the retrieved
# context applies (_COMMON_RULES in backend.py). Copies of this string also live
# in app.py (chip detection) and eval.py (gate) — unify them here when those
# files are next touched; changing the wording breaks all three at once.
REFUSAL_SENTENCE = "המידע לא קיים בפקודות שסופקו"


def is_refusal(answer: str) -> bool:
    """True when the refusal IS the answer, not a caveat inside one.

    Mirrors the chip rule in app.py so the logged flag counts exactly what the
    user saw labelled "לא נמצא במאגר": the sentence must open the answer, at
    most after a short topic prefix ("לגבי סכום המענק — "). A substantive
    answer often carries the same sentence later — as a trailing scope caveat,
    or as the ruling for only PART of a compound question — and those are
    answers, not failures. The two-sided "לא נמצאה בפקודות הפרה בהתנהלות X"
    is likewise a valid verdict and never matches here.
    """
    idx = (answer or "").find(REFUSAL_SENTENCE)
    return 0 <= idx < 80


def safe_print(msg: str) -> None:
    """print() that survives non-UTF-8 stdout (Windows pipes default to the
    ANSI codepage, where Hebrew text raises UnicodeEncodeError)."""
    try:
        print(msg)
    except UnicodeEncodeError:
        sys.stdout.buffer.write((msg + "\n").encode("utf-8", errors="replace"))
        sys.stdout.buffer.flush()
