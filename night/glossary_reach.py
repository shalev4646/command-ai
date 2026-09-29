# -*- coding: utf-8 -*-
"""Before a glossary entry is written: where does its vocabulary live, and whom would it touch?

    venv\\Scripts\\python.exe -m night.glossary_reach counts <phrase> [<phrase> ...]
    venv\\Scripts\\python.exe -m night.glossary_reach fire '<{"key": "expansion", ...}>' [real_questions.json]

`counts` — for each phrase, the documents and occurrences in raw_text and in the curated
blocks (whole word, up to two Hebrew prefix letters), the six documents that carry it most.
The glossary's two rules read off it: the soldier form must be rare in the orders, the
expansion must sit in the answering order rather than across the subject.

`fire` — adds the candidate entries IN MEMORY and lists, per free instrument, the questions
whose expansion changes (the only ones whose window can move: a query without a key reaches
retrieval byte-identical). Held rows of head-100 are COUNTED, never listed; real questions are
listed by id only. Nothing is written.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:] = [p for p in sys.path if Path(p or ".").resolve() != ROOT / "night"]
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_Q = str.maketrans({"״": '"', "׳": "'"})


def _docs() -> dict[str, tuple[str, str]]:
    out = {}
    for p in sorted((ROOT / "storage" / "json_store").glob("*.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        blk = []
        for s in d.get("sections") or []:
            blk.append(s.get("title") or "")
            for c in s.get("clauses") or []:
                blk.append(f"{c.get('number') or ''} {c.get('text') or ''}")
        out[d["document_id"]] = ((d.get("raw_text") or "").translate(_Q), " \n".join(blk).translate(_Q))
    return out


def counts(phrases: list[str]) -> None:
    docs = _docs()
    print(f"{len(docs)} documents")
    for t in phrases:
        rx = re.compile(r"(?<![א-ת])[ולבמכשה]{0,2}" + re.escape(t.translate(_Q)) + r"(?![א-ת])")
        per = {}
        for did, (raw, blk) in docs.items():
            a, b = len(rx.findall(raw)), len(rx.findall(blk))
            if a or b:
                per[did] = (a, b)
        top = sorted(per.items(), key=lambda kv: -sum(kv[1]))[:6]
        print(f"{t!r}: docs={len(per)} raw={sum(v[0] for v in per.values())} "
              f"blocks={sum(v[1] for v in per.values())} top={top}")


def _load(rel: str):
    return json.loads((ROOT / rel).read_text(encoding="utf-8"))


def fire(entries: dict[str, str], real: Path | None) -> None:
    from storage import glossary as G
    sys.argv = ["gate"]
    import eval as E  # noqa: E402  (the gate's own case list)
    src: dict[str, list[tuple[str, str]]] = {}
    src["gate"] = [(f"g{i}", q) for i, (_r, q, _e) in enumerate(list(E.GOLDEN) + list(E.DIRTY))]
    src["gate"] += [(f"adv{i}", p["question"]) for i, p in enumerate(_load("eval_adversarial.json"))]
    for name, rel in (("pilot150", "night/out/adjudication_pilot150.json"),
                      ("ruler", "night/out/adjudication_realstyle.json"),
                      ("ruler_v2", "night/out/adjudication_realstyle_v2.json"),
                      ("c", "night/head100/targets_c.json"),
                      ("homset", "night/head100/homonym_targets.json")):
        src[name] = [(r["id"], r["question"]) for r in _load(rel)]
    h = _load("night/head100/targets.json")
    src["dev"] = [(r["id"], r.get("question") or r.get("q")) for r in h if r.get("split") == "dev"]
    held = [(r["id"], r.get("question") or r.get("q")) for r in h if r.get("split") == "held"]
    if real and real.exists():
        src["real24"] = [(r["id"], r.get("clean_q") or r["q"]) for r in json.loads(real.read_text(encoding="utf-8"))]
    base = dict(G.GLOSSARY)
    before = {n: {i: G.expansions(q) for i, q in rows} for n, rows in list(src.items()) + [("held", held)]}
    try:
        G.GLOSSARY.update(entries)
        for n, rows in src.items():
            hit = [i for i, q in rows if G.expansions(q) != before[n][i]]
            print(f"{n:9} {len(hit):3}/{len(rows):<4} {hit if hit else ''}")
        print(f"{'held':9} {sum(G.expansions(q) != before['held'][i] for i, q in held):3}/{len(held):<4} (count only)")
    finally:
        G.GLOSSARY.clear()
        G.GLOSSARY.update(base)


def main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[0] == "counts":
        counts(argv[1:])
        return 0
    if len(argv) >= 2 and argv[0] == "fire":
        fire(json.loads(argv[1]), Path(argv[2]) if len(argv) > 2 else None)
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
