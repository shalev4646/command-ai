# -*- coding: utf-8 -*-
"""Contamination record for the HELD half, and the aggregate that must respect it.

`held` exists so that nothing is tuned against it. The paid review of the
head-100A run (22.09) read two held answers one by one and named their defect
(hW3a, hR1db — slang homonyms), and a fix is being built for exactly that
defect. From that moment those ids are dev in everything but name: a gain on
them proves nothing about unseen questions.

held_tuned.json lists them, with the date and the reason. Every held aggregate
(probe.py, compare.py) is printed twice once the list is non-empty — with the
contaminated ids and without them — so a future number cannot quietly include
questions the fix was written against. tests/test_held_tuned.py pins this.

No heavy imports here on purpose: the test must run without the index.
"""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
TUNED = HERE / "held_tuned.json"


def tuned_ids(path: Path = TUNED) -> set[str]:
    """Held ids that were read one by one and then fixed — contaminated."""
    if not path.exists():
        return set()
    return {r["id"] for r in json.loads(path.read_text(encoding="utf-8"))["tuned"]}


def summary_line(label: str, ps: list[dict]) -> str:
    """One aggregate line of the free head-100 instrument, as probe.py prints it."""
    n = len(ps)
    return (f"[head100] {label}: n={n}  doc-in-window {sum(p['doc_in_window'] for p in ps)}"
            f"  sect(content) {sum(p['sect_content'] for p in ps)}"
            f"  doc-rank<=6 {sum(1 for p in ps if p['doc_rank'] and p['doc_rank'] <= 6)}"
            f"  doc-rank>25/none {sum(1 for p in ps if not p['doc_rank'] or p['doc_rank'] > 25)}")


def held_lines(per: list[dict], rows: dict[str, dict], tuned: set[str]) -> list[str]:
    """The held aggregate — always twice when anything is tuned: with the
    contaminated ids, and without them (the number that still means 'unseen')."""
    ps = [p for p in per if rows[p["id"]]["split"] == "held"]
    lines = [summary_line("held", ps)]
    hit = sorted(tuned & {p["id"] for p in ps})
    if hit:
        clean = [p for p in ps if p["id"] not in tuned]
        lines.append(summary_line(f"held excl. tuned ({len(hit)}: {','.join(hit)})", clean))
    return lines


def held_count_lines(ids: list[str], before: dict, after: dict, tuned: set[str], key: str = "sect_content") -> list[str]:
    """compare.py's paired held line, plus the same count without the tuned ids."""
    def count(sub):
        b = sum(before[i][key] for i in sub); a = sum(after[i][key] for i in sub)
        lost = sum(1 for i in sub if before[i][key] and not after[i][key])
        won = sum(1 for i in sub if after[i][key] and not before[i][key])
        return b, a, lost, won
    b, a, lost, won = count(ids)
    lines = [f"[head100] held {b}/{len(ids)} -> {a}/{len(ids)}   (lost {lost}, gained {won} — not listed by design)"]
    hit = sorted(tuned & set(ids))
    if hit:
        clean = [i for i in ids if i not in tuned]
        b, a, lost, won = count(clean)
        lines.append(f"[head100] held excl. tuned ({len(hit)}: {','.join(hit)}) {b}/{len(clean)} -> {a}/{len(clean)}"
                     f"   (lost {lost}, gained {won})")
    return lines
