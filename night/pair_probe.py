# -*- coding: utf-8 -*-
"""Paired verdict between two sectprobe-format records (sectprobe, probe_variant, routed_sectprobe).

    venv\\Scripts\\python.exe night/pair_probe.py <base.json> <after.json> [label]

Both files carry {"per": [{"id", "sect_content", "sect_in_window", "doc_in_window", "window_words"}]}.
Prints, for each hit bit both records carry, the totals and the rows lost and gained, then the
mean window words; rows present on one side only are named and never paired. Exit code 1 when
any row is lost. Not for head-100 — its held half is only ever counted (night.head100.compare).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    b = {p["id"]: p for p in json.loads(Path(argv[0]).read_text(encoding="utf-8"))["per"]}
    t = {p["id"]: p for p in json.loads(Path(argv[1]).read_text(encoding="utf-8"))["per"]}
    label = argv[2] if len(argv) > 2 else Path(argv[1]).stem
    ids = [i for i in b if i in t]
    any_lost = False
    for key, name in (("sect_content", "clause"), ("sect_in_window", "verbatim"), ("doc_in_window", "order")):
        if not ids or not all(key in b[i] and key in t[i] for i in ids):
            continue
        lost = [i for i in ids if b[i][key] and not t[i][key]]
        won = [i for i in ids if t[i][key] and not b[i][key]]
        any_lost = any_lost or bool(lost)
        print(f"[{label}] {name}: {sum(bool(b[i][key]) for i in ids)}/{len(ids)} -> "
              f"{sum(bool(t[i][key]) for i in ids)}/{len(ids)}   lost {lost}   gained {won}")
    if ids and all("window_words" in b[i] and "window_words" in t[i] for i in ids):
        wb = sum(b[i]["window_words"] for i in ids) / len(ids)
        wt = sum(t[i]["window_words"] for i in ids) / len(ids)
        moved = [i for i in ids if b[i]["window_words"] != t[i]["window_words"]]
        print(f"[{label}] words: {wb:.0f} -> {wt:.0f} ({(wt - wb) / wb * 100:+.1f}%)   windows changed {moved}")
    only = sorted(set(b) ^ set(t))
    if only:
        print(f"[{label}] unpaired rows {only}")
    return 1 if any_lost else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
