# -*- coding: utf-8 -*-
"""The free pre-screen of the clause-title index (night/PLAN_ROUND4.md, שלב
1.b, component 1.1): does the question, matched to clause TITLES, reach the
answering clause — and does the V2 serving rule then put it in front of the
model?

Runs anywhere: stdlib + the corpus on disk. No embedding model, no API. It is
NOT the paired window measurement — night/sectprobe.py needs the embedding
model and runs on the user's machine — it isolates the new component and
answers the plan's first question before any money is spent: is the
clause-title index a better clause-finder than the embedding was, and what
does serving its picks cost in words?

Targets: the adjudicated rows the corpus answers, with quotes verified in
raw_text (83 on 2026-09-11). Two groups:
  block   the verified quote (or a 6-word verbatim run of it) sits inside a
          curated clause's text — the answering CLAUSE is known (53)
  raw     the quote exists only in raw_text (30) — only the ORDER is known;
          serving its block may help the reader (22 of the 30 carry
          block_covers=true in the adjudication) but this instrument cannot
          see it, and the numbers below do not count it

Columns:
  doc@1 / doc@2   answering order is the index's #1 / in its top-2 (of 83)
  cl@1/@3/@8      best answering clause's rank among all clauses (of 53)
  served          under the V2 rule — top-N orders' blocks, a block over
                  BLOCK_WORDS reduced to its TOP_K best clauses — the
                  answering clause is in the served text (of 53)
  words           served words per question (the price)
Baseline printed alongside: the answering order was in the ARM's served
window (night/out/probe_second4.jsonl, grades_pilot150.jsonl `sources`) —
production with every flag it runs, 31 of 83 on 2026-09-11. The ARM window
is also the "V1" side of the selection-rule table, so the union V1 + V2 the
extension actually produces can be read without the embedding model.

Result of the first run (2026-09-11), kept in night/out/titleprobe.json:

    variant                        doc@1  doc@2  cl@1  cl@3  cl@8  served  words
    titles only                       15     20     7     9     9      16    933
    titles + anchors                  14     16     7     7     9      12    883
    + order title                     14     15     7     7     9      12    887
    all four                          12     14     7     7     9      12    827

    rule (titles only)             doc-served  clause-served  words
    arm window (V1, all flags)             31              ?
    V2 global top-2                        20             16    933
    V1 lead + V2 top-1                     22             14    851
    V1 lead + top-2 of the rest            25             17   1312
    top-3 of V1 ∪ V2                       30             17   1398

So: the title index alone finds the ORDER less often than the embedding
window (20 vs 31) and the CLAUSE far more often than the embedding's
historical 5/59 — the two are complementary, which is why V2 is an appended
extension and not a replacement. Its price is ~900 words a question.

    venv\\Scripts\\python.exe -m night.titleprobe            # the tables
    venv\\Scripts\\python.exe -m night.titleprobe out\\titleprobe.json
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import backend
from common import safe_print
from night import config as C
from storage import clause_index as ci

ADJ = (C.OUT / "adjudication_pilot150.json", C.OUT / "adjudication_realstyle.json")
ARMS = (C.OUT / "probe_second4.jsonl", C.OUT / "grades_pilot150.jsonl")
N_DOCS, BLOCK_WORDS, TOP_K = 2, 600, 6


def _norm(s: str) -> str:
    s = re.sub(r'["״׳\'“”‘’]', "", s or "")
    return re.sub(r"\s+", " ", s).strip()


def _runs(q: str, k: int = 6) -> list[str]:
    w = q.split()
    if len(w) < k:
        return [q] if q else []
    return [" ".join(w[i:i + k]) for i in range(len(w) - k + 1)]


def _arm_sources() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for p in ARMS:
        if p.exists():
            for r in C.read_jsonl(p):
                if r.get("id"):
                    out[r["id"]] = list(r.get("sources") or [])
    return out


def _docs() -> dict[str, dict]:
    return {d["document_id"]: d for d in backend.load_documents() if d.get("document_id")}


def targets() -> list[dict]:
    rows: list[dict] = []
    for p in ADJ:
        if p.exists():
            rows.extend(json.loads(p.read_text(encoding="utf-8")))
    by_id = _docs()
    arm = _arm_sources()
    out = []
    for r in rows:
        if not (r.get("verdict") or "").startswith("ANSWERED_IN_CORPUS"):
            continue
        if not (r.get("verified_quotes") and r.get("doc_id")):
            continue
        doc = by_id.get(r["doc_id"])
        if doc is None:
            continue
        quotes = [_norm(q) for q in r["verified_quotes"] if _norm(q)]
        runs = [x for q in quotes for x in _runs(q)]
        answering = set()
        for s in doc.get("sections") or []:
            if "key-facts" not in (s.get("id") or ""):
                continue
            for cl in s.get("clauses") or []:
                t = _norm(cl.get("text") or "")
                if any(q in t for q in quotes) or any(x in t for x in runs):
                    answering.add((str(s.get("id") or ""), str(cl.get("number") or "")))
        out.append({"id": r["id"], "q": r["question"], "role": r.get("role") or "soldier",
                    "doc_id": r["doc_id"], "answering": answering,
                    "group": "block" if answering else "raw",
                    "arm_window": [d for d in arm.get(r["id"], []) if d in by_id],
                    "arm_doc_served": r["doc_id"] in arm.get(r["id"], [])})
    return out


def _scope(role: str) -> set[str]:
    docs = backend._docs_for_role(role)
    if backend.RETRIEVE_CURATED_ONLY:
        docs = [d for d in docs if backend._has_key_facts(d)]
    return {d["document_id"] for d in docs if d.get("document_id")}


def _served(t: dict, docs: list[str], hits: ci.Hits, by_id: dict,
            block_words: int, top_k: int) -> tuple[bool, int]:
    """Is the answering clause in the text the rule serves, and how many words."""
    keys, words = set(), 0
    for d in docs:
        if d not in by_id:
            continue
        for c in backend.v2_block_chunks(by_id[d], hits.clause_scores(d),
                                         block_words=block_words, top_k=top_k):
            keys.add((d, c["section"], c["clause"]))
            words += len(c["text"].split())
    ok = t["group"] == "block" and any((t["doc_id"], s, c) in keys for s, c in t["answering"])
    return ok, words


def probe(ts: list[dict], index: ci.ClauseIndex, n_docs: int = N_DOCS,
          block_words: int = BLOCK_WORDS, top_k: int = TOP_K) -> dict:
    """The V2 rule on its own: the index's top-N orders, no window, no router."""
    by_id = _docs()
    per = []
    for t in ts:
        hits = index.rank(t["q"], doc_ids=_scope(t["role"]))
        doc_rank = next((i for i, (_, d) in enumerate(hits.docs, 1) if d == t["doc_id"]), None)
        clause_rank = next((i for i, (_, d, s, c) in enumerate(hits.clauses, 1)
                            if d == t["doc_id"] and (s, c) in t["answering"]), None)
        top = [d for _, d in hits.docs[:n_docs]]
        served, words = _served(t, top, hits, by_id, block_words, top_k)
        per.append({"id": t["id"], "group": t["group"], "doc_id": t["doc_id"],
                    "doc_rank": doc_rank, "clause_rank": clause_rank,
                    "doc_top": t["doc_id"] in top, "served": served, "words": words,
                    "arm_doc_served": t["arm_doc_served"]})
    n = len(per)
    blk = [p for p in per if p["group"] == "block"]
    agg = {
        "n": n, "n_block": len(blk),
        "doc_at1": sum(1 for p in per if p["doc_rank"] == 1),
        "doc_top": sum(1 for p in per if p["doc_top"]),
        "arm_doc_served": sum(1 for p in per if p["arm_doc_served"]),
        "clause_at1": sum(1 for p in blk if p["clause_rank"] == 1),
        "clause_at3": sum(1 for p in blk if p["clause_rank"] and p["clause_rank"] <= 3),
        "clause_at8": sum(1 for p in blk if p["clause_rank"] and p["clause_rank"] <= 8),
        "served": sum(1 for p in blk if p["served"]),
        "avg_words": round(sum(p["words"] for p in per) / n) if n else 0,
    }
    return {"agg": agg, "per": per}


def selection_rules(ts: list[dict], index: ci.ClauseIndex) -> dict:
    """Block-serving rules that combine the ARM's window (V1) with the index
    (V2): what the appended extension produces, read without the model."""
    by_id = _docs()
    agg: dict[str, dict] = {}
    for t in ts:
        W = t["arm_window"]
        hits = index.rank(t["q"], doc_ids=_scope(t["role"]))
        sc = {d: s for s, d in hits.docs}
        g2 = [d for _, d in hits.docs[:2]]
        by_score = sorted(dict.fromkeys(W + g2), key=lambda d: -sc.get(d, 0.0))
        lead = W[:1]
        rest = [d for d in by_score if d not in lead]
        rules = {
            "V2 global top-2": g2,
            "V1 lead + V2 top-1": list(dict.fromkeys(lead + g2[:1])),
            "V1 lead + top-2 of the rest": lead + rest[:2],
            "top-3 of V1 ∪ V2": by_score[:3],
        }
        for name, docs in rules.items():
            a = agg.setdefault(name, {"doc": 0, "served": 0, "words": 0})
            a["doc"] += t["doc_id"] in docs
            ok, w = _served(t, docs, hits, by_id, BLOCK_WORDS, TOP_K)
            a["served"] += ok
            a["words"] += w
    for a in agg.values():
        a["words"] = a["words"] // max(1, len(ts))
    return agg


VARIANTS = [
    ("titles only", dict(kinds=("title",))),
    ("titles + anchors", dict(kinds=("title", "anchor"))),
    ("+ order title", dict(kinds=("title", "anchor", "doc_title"))),
    ("all four", dict(kinds=ci.ClauseIndex.ALL_KINDS)),
]


def main(out_path: Path | None = None) -> dict:
    ts = targets()
    docs = backend.load_documents()
    n_block = sum(1 for t in ts if t["group"] == "block")
    safe_print(f"[titleprobe] {len(ts)} targets: {n_block} clause-known, "
               f"{len(ts) - n_block} raw-only; the arm served the order in "
               f"{sum(1 for t in ts if t['arm_doc_served'])}")
    safe_print(f"\n{'variant':<30} {'doc@1':>6} {'doc@2':>6} {'cl@1':>5} {'cl@3':>5} {'cl@8':>5} "
               f"{'served':>7} {'words':>6}")
    results: dict = {"variants": {}, "rules": {}}
    for name, kw in VARIANTS:
        r = probe(ts, ci.ClauseIndex(docs, **kw))
        a = r["agg"]
        safe_print(f"{name:<30} {a['doc_at1']:>6} {a['doc_top']:>6} {a['clause_at1']:>5} "
                   f"{a['clause_at3']:>5} {a['clause_at8']:>5} {a['served']:>7} {a['avg_words']:>6}")
        results["variants"][name] = r
    index = ci.ClauseIndex(docs)   # production kinds
    safe_print(f"\n{'rule (production index)':<30} {'doc-served':>11} {'clause-served':>14} {'words':>6}")
    safe_print(f"{'arm window (V1, all flags)':<30} {sum(1 for t in ts if t['arm_doc_served']):>11} {'?':>14}")
    rules = selection_rules(ts, index)
    for name, a in rules.items():
        safe_print(f"{name:<30} {a['doc']:>11} {a['served']:>14} {a['words']:>6}")
    results["rules"] = rules
    safe_print(f"\n{'V2 rule knobs (N / words / K)':<30} {'doc@N':>6} {'served':>7} {'words':>6}")
    for n_docs, bw, k in ((1, 600, 6), (2, 600, 6), (2, 400, 6), (2, 800, 6), (2, 600, 8), (3, 600, 6)):
        a = probe(ts, index, n_docs=n_docs, block_words=bw, top_k=k)["agg"]
        safe_print(f"{f'{n_docs} / {bw} / {k}':<30} {a['doc_top']:>6} {a['served']:>7} {a['avg_words']:>6}")
    if out_path:
        out_path.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
        safe_print(f"[titleprobe] -> {out_path}")
    return results


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else None)
