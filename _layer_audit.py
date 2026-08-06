"""How much of retrieval is the hand-written anchor layer holding up?

eval.py reports ~100% on the retrieval suites, but those questions have been
patched with anchors over time — it measures a corpus that was fitted to it.
A real user phrases things nobody anticipated, and that case is what this
measures: the same suites run against an index with every anchor removed.

Two numbers come out, and the gap between them is the anchor layer's load:

    A. deployed        anchors in the index — what eval.py sees
    B. anchors stripped what a never-before-seen phrasing actually gets

It also reports, for the cases that survive without an anchor, whether the
winning chunk was a curated key-facts block or the order's own text. That
ratio bounds what adding documents can buy: key-facts are a few percent of
the index but the majority of wins, so an order whose key-facts don't cover
a topic is largely unreachable on that topic even though its text is indexed.

Baseline at 87 orders / 3,149 chunks (2026-08-06): A 100%, B 81.2%, and 65%
of anchor-free wins came from key-facts. Re-run after every corpus round —
if B drops, the matching layer is losing ground and more documents will not
help until it recovers.

Read-only. No API calls (retrieve() is called directly, so the router is not
involved and this costs nothing).
"""
import collections
import sys
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import numpy as np

import backend
import eval as E
from storage import vector_store as vs
from storage.vector_store import retrieve

TOP_K = 3
CASES = list(E.GOLDEN) + list(E.DIRTY)

corpus = vs._get_corpus()
ef = vs._get_ef()
no_sq = [c for c in corpus if c["section"] != "sq"]
n_anchor = len(corpus) - len(no_sq)
print(f"corpus: {len(corpus)} chunks | without anchors: {len(no_sq)} "
      f"({n_anchor} anchors, {100 * n_anchor / len(corpus):.0f}%)")

role_docs = {r: [d["document_id"] for d in backend._docs_for_role(r) if d.get("document_id")]
             for r in ("soldier", "commander", "reserve")}
emb_no_sq = np.stack([c["embedding"] for c in no_sq])


def stripped_rank(question: str, role: str, expected: set[str]):
    """Rank of the expected order, and its best chunk, with anchors removed.

    Mirrors retrieve()'s scoring (cosine + the same lexical rerank) rather
    than calling it, because retrieve() reads the live index and the point
    here is to score against a corpus the anchors have been taken out of.
    """
    ids = set(role_docs[role])
    idx = [i for i, c in enumerate(no_sq) if c["doc_id"] in ids]
    q = np.asarray(ef([question])[0], dtype=np.float32)
    scores = emb_no_sq[idx] @ q
    cands = [{"text": no_sq[i]["text"], "doc_id": no_sq[i]["doc_id"],
              "section": no_sq[i]["section"], "score": round(float(s), 3)}
             for i, s in zip(idx, scores)]
    vs._lexical_rerank(question, cands)
    cands.sort(key=lambda c: c["score"], reverse=True)
    ranked, seen = [], set()
    for c in cands:
        if c["doc_id"] not in seen:
            seen.add(c["doc_id"])
            ranked.append(c["doc_id"])
    best = next((c for c in cands if c["doc_id"] in expected), None)
    rank = next((i for i, d in enumerate(ranked, 1) if d in expected), None)
    return rank, best


a_hits = b_hits = 0
layer: collections.Counter[str] = collections.Counter()
lost = []
for role, question, exp in CASES:
    accepted = set(exp) if isinstance(exp, tuple) else {exp}
    chunks = retrieve(question, n_results=backend.MAX_CONTEXT_CHUNKS, doc_ids=role_docs[role])
    top, seen = [], set()
    for c in chunks:
        if c["doc_id"] not in seen:
            seen.add(c["doc_id"])
            top.append(c["doc_id"])
    a_ok = bool(accepted & set(top[:TOP_K]))
    a_hits += a_ok

    rank, best = stripped_rank(question, role, accepted)
    b_ok = rank is not None and rank <= TOP_K
    b_hits += b_ok
    if b_ok:
        layer["key-facts" if best["section"].startswith("key-facts") else "raw text"] += 1
    else:
        lost.append((role, question, exp, rank, a_ok))

n = len(CASES)
print(f"\ncases: {n}")
print(f"A. deployed (anchors in) : {a_hits}/{n} = {100 * a_hits / n:.1f}%")
print(f"B. anchors stripped      : {b_hits}/{n} = {100 * b_hits / n:.1f}%")
print(f"   -> {a_hits - b_hits} cases live only on a hand-written anchor")

total_layer = max(1, sum(layer.values()))
print("\nwhen the stripped index DOES find the doc, the winning chunk is:")
for k, v in layer.most_common():
    print(f"   {k:<12} {v:>4}  ({100 * v / total_layer:.0f}%)")

print(f"\nlost without anchors ({len(lost)}), rank of correct doc:")
for role, q, exp, rank, a_ok in lost:
    tag = "(anchor saved it)" if a_ok else "(fails either way)"
    print(f"   {tag:<19} rank={str(rank):<5} {exp} :: {q[:60]}")
