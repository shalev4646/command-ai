"""Does expanding the query with corpus vocabulary rescue the failing cases?

MEASURED, AND NO — DO NOT WIRE THIS IN. Four settings, all consistently worse:

    baseline                top-3 387/415   delivered 410/415
    first=6  k=6            top-3 357 (-30) delivered 375 (-35)   +7  -37
    first=8  k=8            top-3 355 (-32) delivered 373 (-37)  +11  -43
    first=10 k=6            top-3 357 (-30) delivered 376 (-34)   +9  -39
    first=10 k=12           top-3 349 (-38) delivered 366 (-44)   +6  -44

The reason is the same thing that makes the failures interesting. Pseudo-
relevance feedback assumes the first pass is roughly right and only needs
sharpening. Here, in exactly the cases that fail, the first pass is wholly
wrong: the "שעות מוקדשות" query returns the housing cluster, so the expansion
adds housing vocabulary to the question and drives it further from the order it
needed. It reinforces the error instead of correcting it.

What remains untested, and is not blocked by this result: an LLM rewriting the
query before retrieval. That does not depend on a first pass at all — it
translates "שעות מוקדשות" into "קיצור שעות פעילות" from understanding rather
than from results. It costs about $0.0025 a question.

Kept rather than deleted so the next attempt starts from this number.


The measured failure is not missing content and not missing anchors. It is
wording: a soldier types "שעות מוקדשות" where the order says "קיצור שעות
פעילות", and the two do not meet in embedding space. Retrieval then locks onto
the modifiers instead — the housing cluster for that query — and the right
order never surfaces. Of the gate's 28 genuine failures, 23 sit at rank 4-5
with a median score gap of 0.008, which is the resolution limit of the
similarity itself rather than a ranking bug.

Pseudo-relevance feedback is the standard answer and costs nothing here, since
the embeddings are local: retrieve once, take the vocabulary of what came back,
append it to the query, retrieve again. If the first pass is roughly right the
second sharpens it; if the first pass is wrong the expansion is wrong too, so
this can hurt as easily as help. Which is why it is measured against the gate's
415 cases before anything is wired into production.

    python -m night.expand           # measure current vs expanded
    python -m night.expand --tune    # sweep the knobs
"""
from __future__ import annotations

import re
import sys
from collections import Counter

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import backend
from night.gate import load_cases
from storage import vector_store as vs

# Words too common to carry signal — expanding with them just adds noise.
STOP = {"פקודה", "פקודות", "חייל", "חיילים", "צהל", "צה", "יחידה", "מפקד",
        "אשר", "אינו", "אינה", "יהיה", "תהיה", "לפי", "בהתאם", "כאמור",
        "עיקרי", "סעיף", "סעיפים", "לרבות", "רשאי", "חובה", "כפי", "שנקבע"}

WORD = re.compile(r"[א-ת]{4,}")


def expansion_terms(chunks: list[dict], k: int) -> list[str]:
    """The most characteristic words of the first-pass results."""
    counts: Counter[str] = Counter()
    for c in chunks:
        for w in set(WORD.findall(str(c.get("text", "")))):
            w = w.translate(vs._FINALS)
            if w not in STOP:
                counts[w] += 1
    # a term shared by several of the returned chunks describes the topic;
    # one that appears in a single chunk usually describes that chunk alone
    return [w for w, n in counts.most_common(k * 3) if n >= 2][:k]


def expanded_retrieve(q: str, role: str, *, first: int, k: int) -> list[dict]:
    doc_ids = [d["document_id"] for d in backend._docs_for_role(role)
               if d.get("document_id")]
    seed = vs.retrieve(q, n_results=first, doc_ids=doc_ids)
    terms = expansion_terms(seed, k)
    if not terms:
        return vs.retrieve(q, n_results=backend.MAX_CONTEXT_CHUNKS, doc_ids=doc_ids)
    return vs.retrieve(q + " " + " ".join(terms),
                       n_results=backend.MAX_CONTEXT_CHUNKS, doc_ids=doc_ids)


def top_docs(chunks: list[dict]) -> list[str]:
    out: list[str] = []
    for c in chunks:
        if c["doc_id"] not in out:
            out.append(c["doc_id"])
    return out


def score(first: int, k: int, cases) -> tuple[int, int, int, int]:
    """(top3, delivered, gained, lost) for one setting."""
    t3 = deliv = gained = lost = 0
    for role, q, exp, _tag, base3 in cases:
        acc = tuple(exp) if isinstance(exp, (list, tuple)) else (exp,)
        docs = top_docs(expanded_retrieve(q, role, first=first, k=k))
        hit3 = any(e in docs[:3] for e in acc)
        t3 += hit3
        deliv += any(e in docs for e in acc)
        gained += hit3 and not base3
        lost += base3 and not hit3
    return t3, deliv, gained, lost


def main(tune: bool = False) -> None:
    cases = []
    base_t3 = base_deliv = 0
    for role, q, exp, tag in load_cases():
        acc = tuple(exp) if isinstance(exp, (list, tuple)) else (exp,)
        docs = top_docs(backend.retrieve_for_role(q, role, route=set()))
        hit3 = any(e in docs[:3] for e in acc)
        base_t3 += hit3
        base_deliv += any(e in docs for e in acc)
        cases.append((role, q, exp, tag, hit3))
    n = len(cases)
    print(f"baseline: top-3 {base_t3}/{n}  delivered {base_deliv}/{n}")

    settings = [(6, 6), (8, 8), (10, 6), (10, 12)] if tune else [(8, 8)]
    for first, k in settings:
        t3, deliv, gained, lost = score(first, k, cases)
        print(f"  first={first:<3} k={k:<3} -> top-3 {t3}/{n} ({t3 - base_t3:+d})  "
              f"delivered {deliv}/{n} ({deliv - base_deliv:+d})  "
              f"gained {gained}, lost {lost}")


if __name__ == "__main__":
    main("--tune" in sys.argv)
