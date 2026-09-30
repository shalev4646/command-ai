# -*- coding: utf-8 -*-
"""Stage-6 prep for the Opus 5 arm, free: (1) every quoted string of every final answer checked against the exact
window the model received and against the corpus; (2) arm vs base statistics (gap declared, kept, output tokens,
stop reasons). Needs only probe_<tag>*.jsonl — runs before the grader finishes.
    python night/arm_review/arm_prep.py opus5v2"""
import json
import sys
from collections import Counter
from pathlib import Path

RUN = Path(__file__).resolve().parents[2]
OUT = RUN / "night" / "out"
sys.path.insert(0, str(RUN))
sys.path.insert(0, str(Path(__file__).parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from review_view import check, corpus_text  # noqa: E402
import backend  # noqa: E402

tag = sys.argv[1]
BASE = "final161v2"


def rows(p):
    return {json.loads(l)["id"]: json.loads(l) for l in open(p, encoding="utf-8") if l.strip()}


arm, arm1, arm2 = rows(OUT / f"probe_{tag}.jsonl"), rows(OUT / f"probe_{tag}_p1.jsonl"), rows(OUT / f"probe_{tag}_p2.jsonl")
base, base1, base2 = rows(OUT / f"probe_{BASE}.jsonl"), rows(OUT / f"probe_{BASE}_p1.jsonl"), rows(OUT / f"probe_{BASE}_p2.jsonl")
sheet = {x["id"]: x for x in json.loads((OUT / f"review_sheet_{BASE}.json").read_text(encoding="utf-8"))}
order = list(sheet)

print("== gaps declared (production rule: second search iff lacked_from(first) non-empty)")
ga = {i for i in order if backend.lacked_from(arm1[i].get("answer") or "")}
gb = {i for i in order if backend.lacked_from(base1[i].get("answer") or "")}
print(f"arm {len(ga)}  base {len(gb)}  arm-only {sorted(ga - gb)}  base-only {sorted(gb - ga)}")
print("kept:", dict(Counter(r.get("kept") for r in arm.values())), "| base second passes", len(base2))
lack = lambda t: bool(backend.lacked_from(t or ""))
print(f"final answer still declares a gap: arm {sum(lack(arm[i]['answer']) for i in order)}  base {sum(lack(base[i]['answer']) for i in order)}")
print("arm final says gap, base final does not:", [i for i in order if lack(arm[i]["answer"]) and not lack(base[i]["answer"])])
print("base final says gap, arm final does not:", [i for i in order if lack(base[i]["answer"]) and not lack(arm[i]["answer"])])

print("\n== tokens / stops")
mo = lambda rs: sum((r.get("usage") or {}).get("output_tokens", 0) for r in rs.values()) / max(1, len(rs))
print(f"output tokens mean: arm p1 {mo(arm1):.0f} p2 {mo(arm2):.0f} | base p1 {mo(base1):.0f} p2 {mo(base2):.0f}")
print("stop reasons p1:", dict(Counter(r.get("stop_reason") for r in arm1.values())), "p2:", dict(Counter(r.get("stop_reason") for r in arm2.values())))
print("truncated:", [i for i, r in arm.items() if r.get("truncated")], "| refusal:", [i for i, r in arm.items() if r.get("refusal_stop") or r.get("p1_stop_reason") == "refusal"])
print(f"answer length words mean: arm {sum(len((r.get('answer') or '').split()) for r in arm.values()) / 72:.0f}  base {sum(len((r.get('answer') or '').split()) for r in base.values()) / 72:.0f}")

print("\n== quotes not in the window the model received (per final answer)")
corpus = corpus_text()
n_q = n_miss = 0
for i in order:
    r = arm[i]
    res = check(r.get("answer") or "", r.get("sent_user_content") or "", corpus)
    n_q += len(res)
    miss = [(q, where) for q, in_w, where in res if not in_w]
    if miss:
        n_miss += len(miss)
        print(f"-- {i} ({sheet[i]['verdict']}, kept={r.get('kept')}): {len(miss)}/{len(res)} quote(s) not in window")
        for q, where in miss:
            print(f"     {q[:150]}  | corpus: {where or 'NOT FOUND'}")
print(f"quotes checked {n_q}, not in window {n_miss}")
