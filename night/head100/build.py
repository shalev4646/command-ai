# -*- coding: utf-8 -*-
"""head-100: build the target file from topics.json, and prove every quote.

The set is MODEL-WRITTEN (2026-09-18) in soldier vocabulary, one topic = one
answering clause, two phrasings. It is NOT the frozen ruler and never replaces
it: it exists to find failures on the head of the distribution and to check
vocabulary bridges, with half of it locked away from tuning.

  dev   phrasings anyone may look at while writing anchors / glossary / doors
  held  phrasings nobody tunes against — read only as an aggregate

The split alternates by topic, so both halves mix short and story-style
phrasings. A quote that no indexed chunk of its order contains is a build
error: the instrument would charge retrieval with the set's own mistake.

    venv\\Scripts\\python.exe -m night.head100.build
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import storage.vector_store as vs  # noqa: E402

HERE = Path(__file__).resolve().parent
TOPICS = HERE / "topics.json"
TARGETS = HERE / "targets.json"


def _norm(s: str) -> str:
    s = re.sub(r'["״׳\'“”‘’]', "", s or "")
    return re.sub(r"\s+", " ", s).strip()


def build() -> tuple[list[dict], list[str]]:
    topics = json.loads(TOPICS.read_text(encoding="utf-8"))
    by_doc: dict[str, list[str]] = {}
    for c in vs._get_corpus():
        by_doc.setdefault(c["doc_id"], []).append(_norm(c["text"]))

    rows: list[dict] = []
    errors: list[str] = []
    seen: set[str] = set()
    for i, t in enumerate(topics):
        tid = t["t"]
        if tid in seen:
            errors.append(f"{tid}: duplicate topic id")
        seen.add(tid)
        answerable = bool(t.get("doc_id"))
        if answerable:
            texts = by_doc.get(t["doc_id"])
            if not texts:
                errors.append(f"{tid}: order {t['doc_id']} is not in the index")
                continue
            for q in t["quotes"]:
                nq = _norm(q)
                if not any(nq in x for x in texts):
                    errors.append(f"{tid}: quote not found in {t['doc_id']}: {q}")
        # alternate which phrasing is locked away
        split = ("dev", "held") if i % 2 == 0 else ("held", "dev")
        for key, sp in zip(("a", "b"), split):
            rows.append({
                "id": f"h{tid}{key}", "topic_id": tid, "topic": t["topic"],
                "split": sp, "question": t[key], "role": t["role"],
                "verdict": "ANSWERED_IN_CORPUS_IN_BLOCK" if answerable else t["expect"],
                "doc_id": t.get("doc_id"),
                "verified_quotes": t.get("quotes") or [],
                "note": t.get("note", ""),
            })
    return rows, errors


def main() -> int:
    rows, errors = build()
    for e in errors:
        print("ERROR", e)
    if errors:
        print(f"{len(errors)} errors — targets.json NOT written")
        return 1
    TARGETS.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    ans = [r for r in rows if r["doc_id"]]
    print(f"topics={len(rows)//2} phrasings={len(rows)} answerable={len(ans)} "
          f"no-order={len(rows)-len(ans)} "
          f"dev={sum(r['split']=='dev' for r in rows)} held={sum(r['split']=='held' for r in rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
