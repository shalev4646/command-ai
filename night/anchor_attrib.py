# -*- coding: utf-8 -*-
"""Attribute each anchor question to the ONE curated clause it was written for.

The fold in storage/vector_store.py knows an anchor win means "this document
answers", but WHICH clause gets the lift is a coin flip — both 2026-08-05
pilot misses were the right order and the wrong paragraph, which is why the
winner's clauses are merged into one block today. The merge is capped though
(_KEY_FACTS_MERGE_WORDS), and for every lifted document that does NOT win the
window, only the lead clause is served at all — so lead identity still decides
what the model reads.

An anchor CAN carry clause identity: it was written from the block's content,
so the clause that shares its wording is the clause it promises. This module
recovers that attribution offline, for free, from evidence that is already on
disk — the indexed embeddings and the texts themselves — and writes
storage/anchor_clauses.json for the fold to consult behind
RETRIEVE_ANCHOR_CLAUSE.

Attribution is deliberately conservative: an anchor is attributed only when
the same clause wins BOTH signals —
  - char 4-gram coverage (the representation that beat word-level on Hebrew
    morphology, `night/why_default.py`, AUC 0.758), and
  - embedding cosine (the retrieval's own geometry) —
and the 4-gram coverage clears a floor. Agreement of two unrelated signals is
the confidence gate; anchors the signals disagree on stay unattributed and the
fold keeps today's behavior for them.

    venv\\Scripts\\python.exe -m night.anchor_attrib          # stats only
    venv\\Scripts\\python.exe -m night.anchor_attrib --write  # + json
    venv\\Scripts\\python.exe -m night.anchor_attrib --sample # eyeball 15
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np

import storage.vector_store as vs
from common import safe_print

OUT = Path(__file__).resolve().parents[1] / "storage" / "anchor_clauses.json"
GRAM_FLOOR = 0.12   # min share of the anchor's 4-grams the clause must carry


def _norm(s: str) -> str:
    s = re.sub(r'["״׳\'“”‘’]', "", s or "")
    return re.sub(r"\s+", " ", s).strip()


def _grams(s: str, k: int = 4) -> set[str]:
    s = _norm(s)
    return {s[i:i + k] for i in range(len(s) - k + 1)} if len(s) >= k else {_norm(s)}


def build() -> tuple[dict, dict]:
    """attribution map + stats. Map: {doc_id: {norm_anchor: [section, clause]}}"""
    anchors: dict[str, list[dict]] = {}
    clauses: dict[str, list[dict]] = {}
    for c in vs._get_corpus():
        sec = str(c.get("section") or "")
        if sec == "sq":
            anchors.setdefault(c["doc_id"], []).append(c)
        elif sec.startswith("key-facts"):
            clauses.setdefault(c["doc_id"], []).append(c)

    out: dict[str, dict] = {}
    n_anchors = n_attr = 0
    for doc_id, ancs in anchors.items():
        cls = clauses.get(doc_id) or []
        if len(cls) < 2:
            # one clause needs no attribution — the merge already serves it
            continue
        cl_grams = [_grams(c["text"]) for c in cls]
        cl_embs = np.stack([c["embedding"] for c in cls])
        for a in ancs:
            n_anchors += 1
            ag = _grams(a["text"])
            if not ag:
                continue
            cov = np.array([len(ag & g) / len(ag) for g in cl_grams])
            cos = cl_embs @ a["embedding"]
            gi, ci = int(cov.argmax()), int(cos.argmax())
            if gi != ci or cov[gi] < GRAM_FLOOR:
                continue
            tgt = cls[gi]
            out.setdefault(doc_id, {})[_norm(a["text"])] = [
                str(tgt.get("section")), str(tgt.get("clause"))]
            n_attr += 1
    stats = {"anchors_seen": n_anchors, "attributed": n_attr,
             "docs": len(out),
             "share": round(n_attr / n_anchors, 3) if n_anchors else 0.0}
    return out, stats


def main() -> int:
    amap, stats = build()
    safe_print(f"[attrib] anchors seen {stats['anchors_seen']}, attributed "
               f"{stats['attributed']} ({stats['share']:.0%}) across {stats['docs']} docs")
    if "--sample" in sys.argv:
        shown = 0
        for doc_id, m in amap.items():
            for a, (sec, cl) in m.items():
                safe_print(f"  {doc_id} | {a[:60]} -> {sec}/{cl}")
                shown += 1
                if shown >= 15:
                    break
            if shown >= 15:
                break
    if "--write" in sys.argv:
        OUT.write_text(json.dumps(amap, ensure_ascii=False, indent=0),
                       encoding="utf-8")
        safe_print(f"[attrib] -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
