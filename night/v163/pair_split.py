# -*- coding: utf-8 -*-
"""Paired verdict for a mixed dev/held record: dev rows listed, held rows counted only.
    python pair_split.py <base.json> <after.json> <targets.json> [label]"""
import json
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
base_p, new_p, tgt_p = sys.argv[1:4]
label = sys.argv[4] if len(sys.argv) > 4 else new_p
split = {r["id"]: r.get("split") for r in json.load(open(tgt_p, encoding="utf-8"))}
load = lambda p: {r["id"]: r for r in json.load(open(p, encoding="utf-8"))["per"]}
b, t = load(base_p), load(new_p)
ids = [i for i in b if i in t]
rc = 0
for sp in ("dev", "held"):
    I = [i for i in ids if split.get(i) == sp]
    for key in ("sect_content", "doc_in_window"):
        lost = [i for i in I if b[i].get(key) and not t[i].get(key)]
        won = [i for i in I if t[i].get(key) and not b[i].get(key)]
        nb, nt = sum(bool(b[i].get(key)) for i in I), sum(bool(t[i].get(key)) for i in I)
        if sp == "dev":
            rc |= bool(lost)
            print(f"[{label}] {sp} {key}: {nb}/{len(I)} -> {nt}/{len(I)}   lost {lost}   gained {won}")
        else:
            rc |= nt < nb
            print(f"[{label}] {sp} {key}: {nb}/{len(I)} -> {nt}/{len(I)}   (aggregate only)")
only = sorted(set(b) ^ set(t))
if only:
    print(f"[{label}] unpaired rows: {len(only)}")
sys.exit(rc)
