# -*- coding: utf-8 -*-
"""Paired verdict for a treatment against the base run, on all three free instruments.

    python -m night.head100.compare base gl1

Reads out/gate_<base>.json + out/gate_<tag>.json, out/ruler_<base>.json +
out/ruler_<tag>.json, out/<base>.json + out/<tag>.json. Held rows are never
listed — only counted.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
from night.head100.tuned import held_count_lines, tuned_ids  # noqa: E402
OUT = HERE / "out"


def _load(name: str):
    return json.loads((OUT / name).read_text(encoding="utf-8"))


def main(base: str, tag: str) -> int:
    # 1. the gate — by question text, since cases carry no id
    gb, ga = _load(f"gate_{base}.json"), _load(f"gate_{tag}.json")
    fb = {(f["set"], f["question"]) for f in gb["failures"]}
    fa = {(f["set"], f["question"]) for f in ga["failures"]}
    print(f"[gate] {base} {gb['passed']}/{gb['total']} -> {tag} {ga['passed']}/{ga['total']}"
          f"   newly failing {len(fa - fb)}   newly passing {len(fb - fa)}")
    for s, q in sorted(fa - fb):
        print(f"   REGRESSION [{s}] {q}")
    for s, q in sorted(fb - fa):
        print(f"   gain       [{s}] {q}")

    # 2. the frozen ruler
    rb = {p["id"]: p for p in _load(f"ruler_{base}.json")["per"]}
    ra = {p["id"]: p for p in _load(f"ruler_{tag}.json")["per"]}
    for key, label in (("sect_content", "clause"), ("doc_in_window", "order")):
        lost = [i for i in rb if rb[i][key] and not ra[i][key]]
        won = [i for i in rb if ra[i][key] and not rb[i][key]]
        print(f"[ruler] {label}: {base} {sum(p[key] for p in rb.values())}/82 -> "
              f"{sum(p[key] for p in ra.values())}/82   lost {lost}   gained {won}")

    # 3. head-100
    rows = {r["id"]: r for r in _load("../targets.json")}
    hb = {p["id"]: p for p in _load(f"{base}.json")["per"]}
    ha = {p["id"]: p for p in _load(f"{tag}.json")["per"]}
    for split in ("dev", "held"):
        ids = [i for i in hb if rows[i]["split"] == split]
        b = sum(hb[i]["sect_content"] for i in ids)
        a = sum(ha[i]["sect_content"] for i in ids)
        lost = [i for i in ids if hb[i]["sect_content"] and not ha[i]["sect_content"]]
        won = [i for i in ids if ha[i]["sect_content"] and not hb[i]["sect_content"]]
        if split == "dev":
            print(f"[head100] dev {b}/{len(ids)} -> {a}/{len(ids)}   lost {lost}   gained {won}")
        else:
            for line in held_count_lines(ids, hb, ha, tuned_ids()):
                print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(*(sys.argv[1:3] if len(sys.argv) > 2 else ("base", "gl1"))))
