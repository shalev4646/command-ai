"""Remove clause citations that cannot be verified against their source.

The PDF extractor mirrors digit runs, so an order's clause markers can come out
absent (no numbering survives at all) or scrambled (33.0808 yields 80-88 where
the source has 08-88, and its own header reads "תוקף סעיפים1 עד82" for clauses
1 to 28). Either way the numbering is not something a citation can be checked
against, and curation wrote "(סעיף 44)" anyway — 242 clauses across 45 orders.

Those citations are not necessarily wrong. They are uncheckable, which is
enough to remove them: a soldier reading "(סעיף 44)" will go looking for clause
44 in an order that does not have one, and a pointer that cannot be followed is
worse than no pointer.

Only the parenthetical goes. The prose around it passed the vocabulary-grounding
gate on its own and stays exactly as written, so nothing paid for is discarded —
re-curating all 45 would cost real money and throw away blocks Opus wrote.

    python -m night.decite            # report only
    python -m night.decite --apply    # rewrite storage/json_store
"""
from __future__ import annotations

import io
import json
import re
import sys
from pathlib import Path

# reconfigure rather than re-wrap: a second TextIOWrapper over the same buffer
# closes the first when it is collected, which kills stdout for anything that
# imports two of these modules at once.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import backend
from night.curate import cited_numbers, clause_numbers_in_raw, is_numbered
from night.rehearse import doc_path

# סעיף ends in a FINAL fey, so `סעיפים?` matches the plural and never the
# singular — which is the common form. Spelling it out both ways is the whole
# difference between this stripping anything and stripping nothing.
SEC = r"סעי(?:ף|פים)"

# "(סעיף 43)" / "(סעיפים 44–45)" / "(סעיפים 1, 7 ו-9)" — the whole parenthetical,
# plus the space in front so removal leaves no double space.
PAREN = re.compile(rf"\s*[(（]\s*{SEC}\s*[\d\s,،\-–—ו]+\s*[)）]")

# The bare form only where it refers to THIS order. "לפי סעיף 12 לחוק השיפוט
# הצבאי" cites a statute and "סעיף 4 לפ\"מ 33.0304" cites another order — both
# are real, checkable references that happen to match the same words, and
# removing them would break the sentence and lose true information.
# Every number carries (?!\d) so the engine cannot back off a digit to satisfy
# the guard: without it, "סעיף 12 לחוק" matched as "סעיף 1" — the lookahead saw
# "2" instead of " לחוק", passed, and the strip left "לפי2 לחוק השיפוט".
NUM = r"\d{1,3}(?!\d)"
# The attached prefix goes with the word: "קבועה בסעיף 15 והיא" must not become
# "קבועה ב והיא". Hebrew glues ב/ל/כ/מ/ו/ה straight onto the noun.
BARE = re.compile(rf"\s*[בלכמוה]?{SEC}\s*{NUM}(?:\s*[–\-—]\s*{NUM})?"
                  rf"(?:\s*,\s*(?:ו-?\s*)?{NUM})*"
                  rf"(?!\s*ל(?:חוק|פקודה|פ[\"״']?מ|הוראות|תקנות))")


def unverifiable(doc: dict) -> list[tuple[int, str, list[int]]]:
    """Indices of clauses whose citations cannot be checked, with what they cite."""
    secs = doc.get("sections") or []
    if not secs:
        return []
    raw = str(doc.get("raw_text", ""))
    numbered = is_numbered(raw)
    real = clause_numbers_in_raw(raw)
    out = []
    for i, cl in enumerate(secs[0].get("clauses", [])):
        cites = cited_numbers(str(cl.get("text", "")))
        if not cites:
            continue
        if not numbered:
            out.append((i, "no numbering", sorted(cites)))
        elif cites - real:
            out.append((i, "not in source", sorted(cites - real)))
    return out


def strip(text: str) -> str:
    """Drop citation parentheticals, then tidy what the removal leaves behind."""
    out = PAREN.sub("", text)
    if cited_numbers(out):
        out = BARE.sub("", out)
    # a citation sitting before the final stop leaves " ." behind
    out = re.sub(r"\s+([.,;:])", r"\1", out)
    out = re.sub(r"\s{2,}", " ", out).strip()
    # and one that WAS the end of the sentence leaves it unterminated
    if out and out[-1] not in ".!?":
        out += "."
    return out


def main(apply: bool = False) -> None:
    docs = backend.load_documents()
    touched = clauses = 0
    for doc in docs:
        hits = unverifiable(doc)
        if not hits:
            continue
        touched += 1
        clauses += len(hits)
        if not apply:
            why = hits[0][1]
            print(f"  {doc['document_id']:<13} {len(hits):>2} clauses  ({why})")
            continue
        cls = doc["sections"][0]["clauses"]
        for i, _why, _nums in hits:
            cls[i]["text"] = strip(str(cls[i]["text"]))
        p = doc_path(doc)
        json.dump(doc, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    verb = "stripped from" if apply else "would strip from"
    print(f"\n{clauses} citations {verb} {touched} orders")
    if not apply:
        print("dry run — nothing written. Use --apply.")
    else:
        left = sum(len(unverifiable(d)) for d in backend.load_documents())
        print(f"re-check on disk: {left} unverifiable citations remain")


if __name__ == "__main__":
    main("--apply" in sys.argv)
