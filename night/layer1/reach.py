# -*- coding: utf-8 -*-
"""Layer 1, step 2 — does a user's question reach the clause it asks about? (free; night/layer1/CRITERION.md)

For each generated question, the window is built the way night/sectprobe.py builds it: the
production window plus the free extensions, the router seats empty, no HyDE, no API. The flags
are the deployed ones, the [env] table of fly.toml in this tree, with HyDE forced off.

The hit is the clause's own words: a 6-word verbatim run of the clause text (a clause shorter
than that: its whole text) in any served chunk of its order. That is copied text, so the
similarity machinery a fix would tune cannot fake it, and a clause folded into the lead chunk
still counts. The order in the window is recorded beside it, which splits a miss into
"the order came without the clause" and "the order did not come".

Held clauses and held phrasings are written for pairing and reported only as counts.

    python -m night.layer1.reach night/layer1/out/layer1-full.jsonl <tag> [--base <tag>] [--workers 4]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from multiprocessing import Pool
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "night" / "layer1" / "out"
RUN = 6


def fly_env() -> dict[str, str]:
    """The [env] table of fly.toml — the deployed flags, verbatim (night/diag_final161v2.py)."""
    env, inside = {}, False
    for line in (ROOT / "fly.toml").read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s.startswith("["):
            inside = s == "[env]"
            continue
        m = re.match(r'^([A-Z_][A-Z0-9_]*)\s*=\s*"(.*)"\s*$', s)
        if inside and m:
            env[m.group(1)] = m.group(2)
    return env


def _norm(s: str) -> str:
    s = re.sub(r'["״׳\'“”‘’]', "", s or "")
    return re.sub(r"\s+", " ", s).strip()


def _loose(s: str) -> str:
    """Letters, digits and single spaces only — a full-text rule is matched against served chunks that may be
    raw windows (a period glued to a word's start, "מילואים," vs "מילואים") or clean units alike."""
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]|_", " ", s or "")).strip()


def runs(text: str, k: int = RUN, loose: bool = False) -> list[str]:
    w = (_loose(text) if loose else _norm(text)).split()
    if len(w) < k:
        return [" ".join(w)] if w else []
    return [" ".join(w[i:i + k]) for i in range(len(w) - k + 1)]


def clause_hit(clause_runs: list[str], served: list[str]) -> bool:
    return any(r in s for s in served for r in clause_runs)


_S = None
_CA = None


def _init() -> None:
    global _S, _CA
    from night import coverage_audit, sectprobe
    _S, _CA = sectprobe, coverage_audit


def fulltext_hit(rule: str, rule_runs: list[str], mine: list[dict]) -> bool:
    """A full-text rule reaches the model when a 6-word run of it is in a served chunk of its order
    (letters and digits only), or when a served curated clause of the order carries it — the coverage
    audit's own test (overlap ≥ CONTENT_MIN), since a block states a rule in its own words."""
    served = [_loose(c["text"]) for c in mine]
    if clause_hit(rule_runs, served):
        return True
    return any(_CA.overlap(rule, (c.get("clause") or "") + " " + c["text"]) >= _CA.CONTENT_MIN
               for c in mine if "key-facts" in (c.get("section") or ""))


def _one(job: tuple) -> tuple[str, bool, bool]:
    qid, q, role, doc, clause_runs = job[0], job[1], job[2], job[3][0], job[3][1:]
    win = _S._free_window(q, role)
    mine = [c for c in win if c["doc_id"] == doc]
    if len(job) > 4 and job[4] == "fulltext":
        return qid, bool(mine), fulltext_hit(job[5], clause_runs, mine)
    served = [_norm(c["text"]) for c in mine]
    return qid, bool(served), clause_hit(clause_runs, served)


def summarize(rows: list[dict]) -> dict:
    def rate(rs):
        n = len(rs)
        return {"n": n, "clause": sum(r["hit_clause"] for r in rs), "doc": sum(r["hit_doc"] for r in rs)}
    dev = [r for r in rows if r["split"] == "dev" and r["phrasing"] == "dev"]
    return {"dev": rate(dev),
            "dev_clause_held_phrasing": rate([r for r in rows if r["split"] == "dev" and r["phrasing"] == "held"]),
            "held_clauses": rate([r for r in rows if r["split"] == "held"]),
            "by_role": {k: rate([r for r in dev if r["role"] == k]) for k in sorted({r["role"] for r in dev})},
            "by_section": {k: rate([r for r in dev if r["section"] == k]) for k in sorted({r["section"] for r in dev})},
            # full-text units: what the summary carries vs what only the order's text has
            "by_in_block": {str(k): rate([r for r in dev if r.get("in_block") == k])
                            for k in sorted({r["in_block"] for r in dev if "in_block" in r})}}


def worklist(rows: list[dict]) -> dict:
    """Dev clauses by their two dev phrasings: both miss (by class) or one misses."""
    by: dict[str, list[dict]] = {}
    for r in rows:
        if r["split"] == "dev" and r["phrasing"] == "dev":
            by.setdefault(r["uid"], []).append(r)
    both, one = [], []
    for uid, rs in sorted(by.items()):
        miss = [r for r in rs if not r["hit_clause"]]
        if not miss:
            continue
        item = {"uid": uid, "doc_id": rs[0]["doc_id"], "clause": rs[0]["clause"], "section": rs[0]["section"],
                "role": rs[0]["role"], "questions": [r["q"] for r in rs],
                "class": "order-missing" if not any(r["hit_doc"] for r in rs) else "clause-missing"}
        (both if len(miss) == len(rs) else one).append(item)
    return {"both_miss": both, "one_misses": one,
            "both_miss_by_class": dict(Counter(i["class"] for i in both))}


def pair(base: list[dict], now: list[dict]) -> dict:
    b = {r["qid"]: r for r in base}
    out = {}
    for name, keep in (("dev", lambda r: r["split"] == "dev" and r["phrasing"] == "dev"),
                       ("dev_clause_held_phrasing", lambda r: r["split"] == "dev" and r["phrasing"] == "held"),
                       ("held_clauses", lambda r: r["split"] == "held")):
        rs = [r for r in now if keep(r) and r["qid"] in b]
        gained = [r["qid"] for r in rs if r["hit_clause"] and not b[r["qid"]]["hit_clause"]]
        lost = [r["qid"] for r in rs if not r["hit_clause"] and b[r["qid"]]["hit_clause"]]
        out[name] = {"n": len(rs), "gained": len(gained), "lost": len(lost)}
        if name == "dev":        # dev may be read one by one; held never is
            out[name]["gained_ids"], out[name]["lost_ids"] = gained, lost
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("questions")
    ap.add_argument("tag")
    ap.add_argument("--base")
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--limit", type=int, default=0, help="first N questions only (a smoke run)")
    ap.add_argument("--units", choices=["curated", "fulltext"], default="curated")
    ap.add_argument("--source", default="", help="fulltext: night/cleantext/build_source.py output")
    args = ap.parse_args()

    os.environ.update(fly_env())
    os.environ["RETRIEVE_HYDE"] = "0"
    os.environ.pop("ANTHROPIC_API_KEY", None)
    sys.stdout.reconfigure(encoding="utf-8")
    from night.layer1.generate import SOURCE, build_fulltext_units, build_units, fingerprint

    units = build_units() if args.units == "curated" else build_fulltext_units(source=Path(args.source or SOURCE))
    fp = fingerprint(units)
    text_of = {u["uid"]: u["text"] for u in units}
    rows = [json.loads(l) for l in open(args.questions, encoding="utf-8") if l.strip()]
    rows = [r for r in rows if r.get("q")]
    if args.limit:
        rows = rows[:args.limit]
    corp = {r.get("corpus") for r in rows}
    if corp != {fp}:
        print(f"[reach] the questions were cut from corpus {sorted(corp)}, this tree is {fp} — refusing")
        return 2
    if args.units == "curated":
        jobs = [(r["qid"], r["q"], r["role"], [r["doc_id"], *runs(text_of[r["uid"]])]) for r in rows]
    else:
        jobs = [(r["qid"], r["q"], r["role"], [r["doc_id"], *runs(text_of[r["uid"]], loose=True)], "fulltext",
                 text_of[r["uid"]]) for r in rows]
    if args.workers > 1:
        with Pool(args.workers, initializer=_init) as pool:
            res = pool.map(_one, jobs, chunksize=8)
    else:
        _init()
        res = [_one(j) for j in jobs]
    got = {qid: (doc, cl) for qid, doc, cl in res}
    for r in rows:
        r["hit_doc"], r["hit_clause"] = got[r["qid"]]

    flags = {k: v for k, v in os.environ.items() if k.startswith(("RETRIEVE_", "ANSWER_"))}
    rec = {"corpus": fp, "flags": flags, "questions": args.questions, "rows": rows, "summary": summarize(rows)}
    if args.base:
        base = json.loads((OUT / f"reach_{args.base}.json").read_text(encoding="utf-8"))["rows"]
        rec["paired"] = pair(base, rows)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"reach_{args.tag}.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT / f"worklist_{args.tag}.json").write_text(json.dumps(worklist(rows), ensure_ascii=False, indent=1),
                                                   encoding="utf-8")
    s = rec["summary"]
    for k in ("dev", "dev_clause_held_phrasing", "held_clauses"):
        v = s[k]
        if v["n"]:
            print(f"[reach] {args.tag} {k}: clause {v['clause']}/{v['n']} ({v['clause'] / v['n']:.1%}), "
                  f"order {v['doc']}/{v['n']}")
    wl = worklist(rows)
    print(f"[reach] worklist: {len(wl['both_miss'])} dev clauses miss on both dev phrasings "
          f"{wl['both_miss_by_class']}, {len(wl['one_misses'])} on one")
    if args.base:
        for k, v in rec["paired"].items():
            print(f"[reach] paired {k}: +{v['gained']} −{v['lost']} of {v['n']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
