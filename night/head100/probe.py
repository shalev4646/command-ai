# -*- coding: utf-8 -*-
"""head-100 through the free section-reach instrument, unchanged.

`night/sectprobe.py` stays the ruler; this only points it at another target
file. Free by construction: no HyDE, empty router shortlist, and this worktree
carries no `.env`, so nothing here can reach the API.

The report keeps the split honest: per-question rows are printed for `dev`
only. `held` is reported as an aggregate and nothing else — reading its misses
one by one would turn it into a second dev set.

    set RETRIEVE_... (the production flags) &&
    venv\\Scripts\\python.exe -m night.head100.probe night\\head100\\out\\base.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from common import safe_print  # noqa: E402
from night import sectprobe  # noqa: E402

HERE = Path(__file__).resolve().parent
TARGETS = HERE / "targets.json"


def main(out: Path | None) -> int:
    sectprobe.ADJ = (TARGETS,)
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
    sectprobe.run(out)
    if not out:
        return 0
    rows = {r["id"]: r for r in json.loads(TARGETS.read_text(encoding="utf-8"))}
    per = json.loads(out.read_text(encoding="utf-8"))["per"]
    for split in ("dev", "held"):
        ps = [p for p in per if rows[p["id"]]["split"] == split]
        n = len(ps)
        safe_print(f"[head100] {split}: n={n}  doc-in-window {sum(p['doc_in_window'] for p in ps)}"
                   f"  sect(content) {sum(p['sect_content'] for p in ps)}"
                   f"  doc-rank<=6 {sum(1 for p in ps if p['doc_rank'] and p['doc_rank'] <= 6)}"
                   f"  doc-rank>25/none {sum(1 for p in ps if not p['doc_rank'] or p['doc_rank'] > 25)}")
    safe_print("[head100] dev misses (held misses are deliberately not listed):")
    for p in per:
        r = rows[p["id"]]
        if r["split"] == "dev" and not p["sect_content"]:
            safe_print(f"   {p['id']:<8} doc={'Y' if p['doc_in_window'] else 'n'} "
                       f"rank={p['doc_rank']}  {r['doc_id']}  | {r['question']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1]) if len(sys.argv) > 1 else None))
