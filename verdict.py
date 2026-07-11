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
_TERM_RE = re.compile(r"^(לא\s+)?(מותר|אסור|מוסמך|רשאי|זכאי|פטור|חייב|ניתן|אפשר|מגיע)(.*)$")
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
