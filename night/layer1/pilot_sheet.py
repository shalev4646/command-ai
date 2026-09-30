# -*- coding: utf-8 -*-
"""The pilot's reading sheet (free; night/layer1/CRITERION.md, "פיילוט").

Prints every pilot clause with its questions for a human read, and the numbers the pilot is
judged on: parse rate, skip rate, and title echo — a question whose content words are mostly
the clause title's. The chunk embeds "סעיף <title>: <text>", so an echo reaches the clause by
repeating it, and the reach it earns is borrowed.

    python -m night.layer1.pilot_sheet night/layer1/out/layer1-pilot.jsonl
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from night.sectprobe import _content_words  # noqa: E402

ECHO = 0.7


def echo(q: str, title: str) -> float:
    """Share of the question's content words that the clause title carries."""
    qw = _content_words(q)
    return len(qw & _content_words(title)) / len(qw) if qw else 0.0


def main() -> int:
    path = Path(sys.argv[1])
    sys.stdout.reconfigure(encoding="utf-8")
    rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    units = json.loads((path.parent / (path.stem + "_units.json")).read_text(encoding="utf-8"))["units"]
    by = {}
    for r in rows:
        by.setdefault(r["uid"], []).append(r)
    qs = echoes = 0
    kinds = set()
    for u in units:
        fulltext = u.get("section") == "fulltext"
        kinds.add("rule" if fulltext else "title")
        rs = by.get(u["uid"])
        tag = f" [{'in a block' if u.get('in_block') else 'only in the text'}]" if fulltext else " [curated clause]"
        print(f"\n== {u['doc_id']} | {u['clause']} [{u['role']}]{tag}")
        print(f"   {u['text'][:400]}")
        if not rs:
            print("   (not parsed)")
            continue
        if rs[0]["skip"]:
            print(f"   SKIP: {rs[0]['skip']}")
            continue
        for r in rs:
            # a full-text rule has no title: the echo is against the rule's own words (CRITERION.md, addendum)
            e = echo(r["q"], u["text"] if fulltext else u["clause"])
            qs += 1
            echoes += e >= ECHO
            print(f"   {'*' if e >= ECHO else ' '} {r['q']}   (echo {e:.2f})")
    parsed = len(by)
    skipped = sum(1 for rs in by.values() if rs[0]["skip"])
    kind = "/".join(sorted(kinds)) or "title"
    print(f"\n[pilot] parsed {parsed}/{len(units)}, skip {skipped}, questions {qs}, "
          f"{kind} echo >= {ECHO}: {echoes} ({echoes / qs:.0%})" if qs else "\n[pilot] no questions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
