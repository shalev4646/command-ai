"""How much of the context we pay for is publication boilerplate?

The 33.0304 rehearsal found a retrieved chunk whose bulk was the order's
colophon — "נוסח פקודה זה פורסם במאגר הפקודות הצה"לי בתאריך 1 באפריל 1962.
הפקודה עודכנה בתאריכים: ..." — handed to Opus as if it were an answer. That is
one order. This asks the corpus-wide version of the question, for free:

  * how many indexed chunks carry colophon text at all
  * across the 415 gate cases, what share of retrieved context words are
    colophon rather than content

The second number is the one that matters, because it is money: context words
are input tokens on every single question, and boilerplate crowds out the
clause that would have answered.
"""
from __future__ import annotations

import json
import re

import backend
from night import config as C

# The colophon is templated, so it is safe to match on its fixed phrases
# rather than on dates. Both spellings of the publication line appear.
COLOPHON = re.compile(
    r"(נוסח\s+פקודה\s+זה\s+פורסם|הפקודה\s+עודכנה\s+בתארי|במאגר\s+הפקודות\s+הצה)"
)
# a colophon run continues to the end of the date list; measure generously but
# bounded, so a chunk that merely mentions a date is not counted as boilerplate
DATE_RUN = re.compile(r"\d{1,2}\s+ב[א-ת]+\s*\d{4}")


def colophon_words(text: str) -> int:
    """Words belonging to a colophon run, 0 if the chunk has none."""
    m = COLOPHON.search(text)
    if not m:
        return 0
    tail = text[m.start():]
    dates = DATE_RUN.findall(tail)
    # the fixed preamble plus roughly four words per date entry
    return len(re.sub(r"\s+", " ", tail).split()) if len(dates) >= 2 else len(tail.split())


def scan_index() -> dict:
    """Every chunk the retriever can return, checked for colophon content."""
    from storage.vector_store import _get_collection
    col = _get_collection()
    got = col.get(include=["documents", "metadatas"])
    total = len(got["ids"])
    hits = []
    for cid, doc, meta in zip(got["ids"], got["documents"], got["metadatas"]):
        cw = colophon_words(doc or "")
        if cw:
            hits.append({"chunk": cid, "doc_id": meta.get("doc_id"),
                         "section": meta.get("section"),
                         "colophon_words": cw, "total_words": len((doc or "").split())})
    return {"total_chunks": total, "chunks_with_colophon": len(hits), "hits": hits}


def scan_gate_context() -> dict:
    """The share of real retrieved context that is boilerplate, over 415 cases."""
    import sys
    sys.argv = ["colophon"]
    import eval as E
    adv = json.loads((C.ROOT / "eval_adversarial.json").read_text(encoding="utf-8"))
    cases = ([(r, q) for r, q, _ in E.GOLDEN] + [(r, q) for r, q, _ in E.DIRTY]
             + [(p["role"], p["question"]) for p in adv])

    ctx_words = colo_words = 0
    affected = 0
    per_doc: dict[str, int] = {}
    for i, (role, q) in enumerate(cases):
        chunks = backend.retrieve_for_role(q, role, route=set())
        case_colo = 0
        for c in chunks:
            ctx_words += len(c["text"].split())
            cw = colophon_words(c["text"])
            case_colo += cw
            if cw:
                per_doc[c["doc_id"]] = per_doc.get(c["doc_id"], 0) + cw
        colo_words += case_colo
        if case_colo:
            affected += 1
        if (i + 1) % 100 == 0:
            C.log(f"[colophon] gate scan {i + 1}/{len(cases)}")
    return {"cases": len(cases), "cases_with_colophon": affected,
            "context_words": ctx_words, "colophon_words": colo_words,
            "per_doc": dict(sorted(per_doc.items(), key=lambda kv: -kv[1])[:20])}


def main() -> None:
    idx = scan_index()
    C.log(f"[colophon] index: {idx['chunks_with_colophon']}/{idx['total_chunks']} chunks "
          f"carry colophon text")
    worst = sorted(idx["hits"], key=lambda h: -h["colophon_words"])[:12]
    for h in worst:
        C.log(f"[colophon]   {h['doc_id']:<12} {str(h['section'])[:18]:<18} "
              f"{h['colophon_words']:>4}/{h['total_words']:<4} words")

    gate = scan_gate_context()
    pct = 100.0 * gate["colophon_words"] / max(1, gate["context_words"])
    C.log(f"[colophon] gate context: {gate['colophon_words']:,} of {gate['context_words']:,} "
          f"words are boilerplate ({pct:.1f}%)")
    C.log(f"[colophon] cases serving at least some boilerplate: "
          f"{gate['cases_with_colophon']}/{gate['cases']}")
    # Opus input is $5/MTok; Hebrew runs well under 1 word/token, so this is a
    # deliberately conservative floor on the waste, not an estimate of it.
    per_q = gate["colophon_words"] / max(1, gate["cases"])
    C.log(f"[colophon] ~{per_q:.0f} boilerplate words per question "
          f"(~${per_q * 5 / 1_000_000:.5f}/question at Opus input rates, floor)")

    (C.OUT / "colophon.json").write_text(
        json.dumps({"index": idx, "gate": gate}, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
