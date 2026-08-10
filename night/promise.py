"""Do the curated key-facts actually cover what the anchors promise?

Found by accident and worth measuring properly. Order 33.0304 carries the
anchor "אם הוזמנתי לעדות ולא הגעתי, מה יכול לקרות לי?" — but its curated block
covers suspect warnings, arrest and fingerprints, and says nothing about
failing to appear. So the anchor advertises a question the block cannot answer.
The exact anchor text still retrieves the order (it is indexed as its own
chunk), which hides the gap; two natural paraphrases of the same question do
not put the order in the top 25 at all.

This is the "liftable but empty" defect in a second form. The first form was
structural — no key-facts section at all — and is now closed for all 98 orders.
This form is semantic: the section exists but does not span its own anchors.

Measurement is local and free: embed each anchor and each key-facts clause with
the same ONNX model the retriever uses, and take the best cosine per anchor. A
low best means no clause in the curated block speaks to that anchor.
"""
from __future__ import annotations

import json

import numpy as np

import backend
from night import config as C
from night.audit import _anchors

# Cosine below which an anchor has no clause that plausibly answers it.
#
# 0.35 sits at the 10th percentile of the observed distribution (p10=0.376,
# p50=0.587), so it flags a genuine low tail. The first pass used 0.55 and
# reported "41% uncovered" — a number that looked alarming and meant almost
# nothing, because 0.55 sits just under the median and roughly half of anything
# falls below its own median. Sensitivity is reported alongside the count so the
# threshold cannot smuggle in a conclusion: <0.30 -> 4%, <0.35 -> 7%,
# <0.45 -> 19%, <0.55 -> 41%.
UNCOVERED = 0.35


def _clauses(doc: dict) -> list[str]:
    out = []
    for s in doc.get("sections") or []:
        if not isinstance(s, dict):
            continue
        for cl in s.get("clauses") or []:
            out.append(f"{cl.get('number','')} {cl.get('text','')}".strip())
    return out


def run() -> None:
    from storage.vector_store import _embed_cached

    docs = [d for d in backend.load_documents() if d.get("document_id")]
    rows = []
    for i, d in enumerate(docs, 1):
        anchors, clauses = _anchors(d), _clauses(d)
        if not anchors or not clauses:
            continue
        vecs = np.array(_embed_cached(anchors + clauses), dtype=np.float32)
        a, c = vecs[:len(anchors)], vecs[len(anchors):]
        # embeddings are L2-normalized by the model, so a dot product is cosine
        sim = a @ c.T
        best = sim.max(axis=1)
        for anchor, score, j in zip(anchors, best, sim.argmax(axis=1)):
            rows.append({"doc_id": d["document_id"], "title": d.get("title", ""),
                         "anchor": anchor, "best_cosine": round(float(score), 4),
                         "best_clause": clauses[j][:120]})
        if i % 25 == 0:
            C.log(f"[promise] {i}/{len(docs)}")

    rows.sort(key=lambda r: r["best_cosine"])
    C.write_jsonl(C.OUT / "promise.jsonl", rows)

    scores = np.array([r["best_cosine"] for r in rows])
    C.log(f"[promise] {len(rows)} anchors across {len({r['doc_id'] for r in rows})} orders")
    C.log(f"[promise] cosine to nearest clause: p10={np.percentile(scores,10):.3f} "
          f"p50={np.percentile(scores,50):.3f} p90={np.percentile(scores,90):.3f}")
    weak = [r for r in rows if r["best_cosine"] < UNCOVERED]
    C.log(f"[promise] anchors with NO clause above {UNCOVERED}: {len(weak)} "
          f"({100*len(weak)/len(rows):.0f}%)")

    by_doc: dict[str, int] = {}
    for r in weak:
        by_doc[r["doc_id"]] = by_doc.get(r["doc_id"], 0) + 1
    C.log("[promise] worst orders (uncovered anchors / total):")
    totals: dict[str, int] = {}
    for r in rows:
        totals[r["doc_id"]] = totals.get(r["doc_id"], 0) + 1
    for doc_id, n in sorted(by_doc.items(), key=lambda kv: -kv[1])[:15]:
        C.log(f"[promise]   {doc_id:<12} {n}/{totals[doc_id]}")
    C.log("[promise] widest individual gaps:")
    for r in rows[:8]:
        C.log(f"[promise]   {r['best_cosine']:.3f} {r['doc_id']:<12} {r['anchor'][:70]}")


if __name__ == "__main__":
    run()
