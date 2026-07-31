# -*- coding: utf-8 -*-
"""Verdict-line colour classification — shared, pure, and unit-tested.

The share card used to classify its ruling clauses in the iframe's
JavaScript: untested, and free to drift from the chat chip's colours. This
module is the single source of that logic. app.py calls verdict_clauses()
and hands the result to the card, which now only wraps and draws.

The chat chip (_verdict_chip in app.py) is a DIFFERENT contract — it refuses
compound rulings and renders one HTML pill — so it keeps its own gating; but
the per-clause colour rule below matches it (opening term drives the colour,
בתנאים/חלקית → conditional), extended with the card's compound handling: a
clause carrying an OPPOSING term ("מותר או אסור — תלוי") is conditional, and
a ';'-separated ruling is split into one coloured clause per part.
"""
import re

# LRM RLM ZWSP BOM + embedding/override/isolate controls the model sometimes
# emits around RTL text; \s matches none of them, so strip them explicitly.
_BIDI_MARKS = "‎‏​﻿‪‫‬‭‮⁦⁧⁨⁩"
_LINE_RE = re.compile(
    r"^\s*[" + _BIDI_MARKS + r"]*(?:\*\*)?\s*פסיקה:\s*\*{0,2}\s*(.+?)[^\S\n]*$",
    re.MULTILINE,
)
# ── The two halves of a verdict clause's opener, shared by every gate below
# (the card's colour classifier and the chat chip): one list, so the card can
# never colour a clause the chip refuses to recognise, or vice versa. ──
#
# NEG: every negation the model actually writes. Each persona prompt says
# "אתה פונה אל החייל/המפקד בגוף שני", and in that register a term is negated
# with the copula — "אינך זכאי" — not with לא. A לא-only gate therefore missed
# the COMMONEST negative phrasing: the pilot's "**פסיקה:** אינך זכאי להחזר
# דמ"כ בגין אותה ארוחה" scored no colour at all and rendered as plain bold
# body text (phone screenshot, 2026-08-01). Bare אין is deliberately absent —
# it negates a noun ("אין זכאות"), never a term, so it could only match by
# accident.
NEG = r"(?:לא|אינני|אינכם|אינכן|אינך|אינו|אינה|אינם|אינן|איני)"
TERM = r"(?:מותר|אסור|מוסמך|רשאי|זכאי|פטור|חייב|ניתן|אפשר|מגיע(?:\s+ל[ךי])?)"
_TERM_RE = re.compile(rf"^({NEG}\s+)?({TERM})(.*)$")
_OPPOSE_POS = re.compile(r"מותר|רשאי|זכאי|מוסמך|פטור|מגיע")


def clause_class(clause: str) -> str:
    """Colour class for one ruling clause: yes | cond | no | none | accent.

    accent is the neutral "recognised nothing / double negative" fallback —
    never a wrong colour. Mirrors _VERDICT_TERM_RE semantics in app.py.
    """
    s = clause.strip()
    if s.startswith("לא נמצא"):
        return "none"
    m = _TERM_RE.match(s)
    if not m:
        return "accent"
    neg, term, tail = m.group(1), m.group(2), (m.group(3) or "").strip()
    if neg and term == "אסור":            # לא אסור — double negative, no honest colour
        return "accent"
    cls = "no" if (neg or term == "אסור") else "yes"
    if tail.startswith(("בתנאים", "חלקית")):
        return "cond"
    # an OPPOSING term later in the clause makes the ruling conditional
    if cls == "yes" and "אסור" in tail:
        return "cond"
    if cls == "no" and _OPPOSE_POS.search(tail):
        return "cond"
    return cls


def verdict_clauses(content: str) -> list[dict]:
    """The פסיקה line split into coloured clauses for the share card:
    [{"text": clause, "cls": yes|cond|no|none|accent}]. Empty when the answer
    carries no ruling line."""
    m = _LINE_RE.search(content or "")
    if not m:
        return []
    line = m.group(1).replace("**", "").strip("* " + _BIDI_MARKS)
    return [{"text": c.strip(), "cls": clause_class(c)} for c in line.split(";") if c.strip()]


# ── Chat-chip clause gate ────────────────────────────────────────────────
# Stricter than clause_class above: the chat chip is a nowrap badge, so a
# clause only chips when it is badge-sized AND unambiguous — a wrong chip is
# worse than no chip. These regexes were app.py's local _VERDICT_TERM_RE /
# _QUAL_CONFLICT_RE; they moved here (app.py imports them) so the chip gate
# is unit-testable in eval's structural suite and cannot drift from the
# two-sided stacked-chip path, which calls chip_clause() below.
#
# A verdict clause must OPEN with a term. The model sometimes opens with a
# TOPIC ("בנוגע למסדר בוקר — ייתכן שאתה פטור...") — chipping that fragment
# produced a meaningless green badge on the pilot phone check (2026-07-10),
# so a non-term opener never chips. A qualifier may follow the term ("אסור
# בתנועה רגלית", pilot 2026-07-11); it bars ';' (a mid-list cut) and '*'
# (markdown residue), and CHIP_QUAL_MAX keeps it badge-sized.
#
# ONE cap, both chip paths. The lone chip used to be capped at 18 on the
# reasoning that .verdict-chip is a NOWRAP pill with no max-width, so the cap
# WAS the overflow guard; the two-sided stacked path meanwhile shipped 50,
# because a chip that wraps (.verdict-wrap) only needs its clause to stay
# badge-LIKE, not paragraph-short. 18 is what rejected the pilot's 26-char
# qualifier ("להחזר דמ"כ בגין אותה ארוחה") and dropped that whole ruling into
# the body (2026-08-01). Wrapping is now applied by length on both paths, so
# the guard the 18 provided is gone and the distinction with it. 50 mirrors
# the none-clause budget (NONE_CHIP_MAX minus a term).
CHIP_QUAL_MAX = 50
CHIP_TERM_RE = re.compile(
    rf"^(?P<neg>{NEG}\s+)?(?P<term>{TERM})(?P<qual>\s+[^;*]{{1,{CHIP_QUAL_MAX}}})?$"
)
# A qualifier that itself cites a verdict/ruling verb or a negation is a
# COMPOUND ruling ("מותר אך אסור במדים", "ניתן צו האוסר...") — one color
# would misstate it, so the clause never chips. Substring matching
# over-catches Hebrew prefixed forms (ואסור, שמותר); the failure mode is
# "no chip", the safe one. לא/אין are matched as words with ו/ש/כ/ב
# prefixes — bare substrings would hit מלא, אלא, לאחר.
QUAL_CONFLICT_RE = re.compile(
    r"מותר|אסור|אוסר|מתיר|מוסמך|רשאי|זכאי|פטור|חייב|ניתן|אפשר|מגיע"
    r"|(?:^|\s)[ושכב]?(?:לא|אין)(?=\s|$)"
)
# "לא נמצאה בפקודות חובה..." — the no-rule clause of a two-sided ruling —
# carries a sentence, not a term+qualifier, so it gets its own cap. Its chip
# wears .verdict-wrap (white-space:normal), so 60 chars renders as at most
# two lines at the 290px breakpoint; beyond that the line stays body text.
NONE_CHIP_MAX = 60


def chip_clause(clause: str) -> tuple[str, str, str] | None:
    """(cls, icon, text) for ONE ruling clause that must stand alone as a
    chat chip — no remainder returns to the body — or None when it cannot
    honestly chip. Used by app.py's two-sided (';'-separated) stacked-chip
    path; the single-clause path keeps its own sep/rest handling but shares
    the regexes above."""
    s = clause.strip("* ." + _BIDI_MARKS)
    # the no-rule clause: protective "you broke no rule", coloured none —
    # never a wrong colour ("לא נמצאה בפקודות חובה למצוא מחליף מיידי")
    if s.startswith("לא נמצא"):
        return ("none", "ⓘ", s) if len(s) <= NONE_CHIP_MAX else None
    m = CHIP_TERM_RE.match(s)
    if not m:
        return None
    qual = (m.group("qual") or "").strip()
    if (
        QUAL_CONFLICT_RE.search(qual)                        # compound ruling
        or (m.group("neg") and m.group("term") == "אסור")    # לא אסור — double negative, no honest single color
        or (qual and m.group("term") in ("ניתן", "אפשר") and not qual.startswith("ל"))  # ניתן צו... — passive verb, not the modal
        # a BARE authority/modal term answers nothing on its own — a
        # "✓ מוסמך" pill left the pilot user asking "מוסמך למה?"
        # (2026-07-21 phone report). מותר/אסור/זכאי/פטור/חייב carry a
        # complete ruling alone; מוסמך/רשאי/ניתן/אפשר need their object.
        or (not qual and m.group("term") in ("מוסמך", "רשאי", "ניתן", "אפשר"))
    ):
        return None
    if qual.startswith(("בתנאים", "חלקית")):
        return ("cond", "⚠", s)
    if m.group("neg") or m.group("term") == "אסור":
        return ("no", "✗", s)
    return ("yes", "✓", s)
