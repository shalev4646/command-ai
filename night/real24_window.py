# -*- coding: utf-8 -*-
"""Window listing for the real production questions (no targets, no score): what the
free instrument serves today for each — orders in window order, words. For a human to
read side by side between two arms; an arm passes when no real question loses its
lead order to an off-topic one.

    venv\\Scripts\\python.exe -m night.real24_window <real_questions.json> <tag>

The questions file (night/out/real_questions.json, gitignored, kept in the production
tree) is given explicitly so the same instrument runs in any worktree. Flags come from
the environment (levers_measure / package_measure set the production ones); HyDE off,
no API key. Writes night/out/real24_window_<tag>.json in the current tree, prints a
paired diff against real24_window_<BASE>.json when BASE=<tag> is passed.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("RETRIEVE_HYDE", "0")
os.environ.pop("ANTHROPIC_API_KEY", None)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    src, tag = Path(argv[0]), argv[1]
    base = next((a.partition("=")[2] for a in argv[2:] if a.startswith("BASE=")), None)
    from night import sectprobe as sp  # noqa: E402  (loads the model)

    rows = json.loads(src.read_text(encoding="utf-8"))
    out = []
    for r in rows:
        q = r.get("clean_q") or r["q"]
        win = sp._free_window(q, r.get("role") or "soldier")
        docs: list[str] = []
        for c in win:
            if c["doc_id"] not in docs:
                docs.append(c["doc_id"])
        words = sum(len(c["text"].split()) for c in win)
        out.append({"id": r["id"], "q": q, "lead": docs[0] if docs else None, "docs": docs, "words": words})
        print(f"{r['id']}  lead={str(out[-1]['lead']):<16} words={words:<5} docs={len(docs)} | {q[:70]}")
    dst = ROOT / "night" / "out" / f"real24_window_{tag}.json"
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print("->", dst)
    if base:
        bp = ROOT / "night" / "out" / f"real24_window_{base}.json"
        if bp.exists():
            b = {x["id"]: x for x in json.loads(bp.read_text(encoding="utf-8"))}
            changed = [(x["id"], b[x["id"]]["lead"], x["lead"], b[x["id"]]["words"], x["words"])
                       for x in out if x["id"] in b and (b[x["id"]]["docs"] != x["docs"] or b[x["id"]]["words"] != x["words"])]
            lead_changed = [c for c in changed if c[1] != c[2]]
            print(f"[real24] vs {base}: {len(changed)} rows changed, {len(lead_changed)} lead changes {lead_changed}")
        else:
            print(f"[real24] no base record {bp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
