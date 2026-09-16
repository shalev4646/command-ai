# -*- coding: utf-8 -*-
"""The free section-reach instrument: does the ANSWERING chunk reach the window?

The campaign's sharpest number — "the answering section reaches the window in
5 of 59" (3e7cadf) — came from a scratch script that no longer exists. This
module is its permanent replacement, with the hit rule pinned in code so every
future treatment is judged by the same ruler. The absolute level is not
comparable to the historical 5/59 (that script's exact hit criterion was lost
with it); PAIRED deltas on this instrument are the currency.

Targets: the arbitration rows (pilot-150 and, since 2026-09-10, the realstyle
set) whose verdict says the corpus answers and that carry quotes verified
verbatim in raw_text.

The section hit is judged on CONTENT, not chunk identity: the fold merges the
winning document's key-facts clauses into the lead chunk's text, so answering
content can arrive under another clause's identity — identity matching
undercounts exactly the mechanism being improved. A window "carries the
section" when some SERVED text contains a full verified quote or a 6-or-more
word verbatim run of one (checked against 42/62 targets whose curated clauses
carry such a run — a verbatim run is copied text, so this cannot be gamed by
the same similarity machinery the treatments tune).

The window is the production window PLUS the free extensions the environment
turns on (RETRIEVE_FULL_BLOCKS, RETRIEVE_V2, RETRIEVE_QUANTITY_CLAUSES) —
that is what the model reads, and "the clause reaches the window" must be
judged on it. The paid extensions never run here: the hypothetical is forced
off for the run and the router seats get an empty shortlist. With every flag
at its code default the window is the bare ranking, as before 2026-09-11, so
paired runs stay paired: same env on both sides except the flag under test.

Reported per run, all free (route=set(), no HyDE, no API):
  doc-in-window   the answering ORDER made the served window
  sect-in-window  answering CONTENT made the served window (rule above)
  doc-rank        best global rank of any chunk of the answering order
  sect-rank       best global rank of a raw chunk containing a full quote
  sect-content    sect-in-window, OR a served curated clause of the order
                  carries >= CONTENT_MIN of a quote's content words (16.09)
Rank distributions are the smooth signal treatments actually move; the
in-window bits are the product truth they must eventually cash into.

    venv\\Scripts\\python.exe -m night.sectprobe out\\sect_base.json
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import backend
import storage.vector_store as vs
from common import safe_print
from night import config as C

# pilot-150 (2026-08-25) and the realstyle set (2026-09-10) — both carry
# regex-located verbatim quotes; a file that is not on disk is skipped
ADJ = (C.OUT / "adjudication_pilot150.json", C.OUT / "adjudication_realstyle.json")
RANK_POOL = 200   # global ranking depth for the rank diagnostics

# ── The content rule (16.09) ─────────────────────────────────────────────────
# The verbatim rule below cannot see a curated clause that carries the rule in
# soldier language: the 16.09 attribution found the rule present in 73 of 83
# curated blocks while the verbatim rule counted 12 of 82 served -- the
# instrument, not the blocks, was the blind spot. So a served CURATED clause of
# the answering order also counts when it carries at least CONTENT_MIN of a
# verified quote's content words (3+ letters, not a stopword, not a number).
# Calibrated on five hand-verified cases: carriers scored 0.36-0.52,
# non-carriers 0.13 and 0.20. Raw chunks keep the verbatim rule only -- a
# neighbouring raw chunk of the same order shares the topic's vocabulary
# without carrying the rule. Both counts are reported; the verbatim one keeps
# every historical file comparable.
CONTENT_MIN = 0.30
_STOP = frozenset(
    "של על את עם לא אם או כל גם רק יש אין זה זו הוא היא הם הן אני אתה כי מה מי "
    "איך מתי אבל אשר כדי לפי בין עד בכל לכל ואת שלא אלא ידי ואם ולא".split())
_PUNCT = ".,;:!?()[]{}'„“”‘’׳״-–—/|" + chr(34)
_PUNCT_TABLE = str.maketrans({c: " " for c in _PUNCT})


def _content_words(s: str) -> set[str]:
    out: set[str] = set()
    for w in (s or "").translate(_PUNCT_TABLE).split():
        if len(w) >= 3 and w not in _STOP and not w.isdigit():
            out.add(w)
    return out


def content_overlap(quotes: list[str], text: str) -> float:
    """The largest share of any single quote's content words that `text`
    carries. A quote with fewer than three content words vouches for nothing."""
    tw = _content_words(text)
    best = 0.0
    for q in quotes:
        qw = _content_words(q)
        if len(qw) < 3:
            continue
        best = max(best, len(qw & tw) / len(qw))
    return best


def content_hit(quotes: list[str], text: str) -> bool:
    return content_overlap(quotes, text) >= CONTENT_MIN


def _norm(s: str) -> str:
    s = re.sub(r'["״׳\'“”‘’]', "", s or "")
    return re.sub(r"\s+", " ", s).strip()


def _chunk_key(c: dict) -> tuple:
    return (c.get("doc_id"), c.get("section"), c.get("clause"))


def _runs(q: str, k: int = 6) -> list[str]:
    w = q.split()
    if len(w) < k:
        return [q] if q else []
    return [" ".join(w[i:i + k]) for i in range(len(w) - k + 1)]


def targets() -> list[dict]:
    """Arbitration rows the corpus can answer, with their answering evidence."""
    rows: list[dict] = []
    for path in ADJ:
        if path.exists():
            rows.extend(json.loads(path.read_text(encoding="utf-8")))
    by_doc: dict[str, list[dict]] = {}
    for c in vs._get_corpus():
        by_doc.setdefault(c["doc_id"], []).append(c)

    out = []
    for r in rows:
        if not (r.get("verdict") or "").startswith("ANSWERED_IN_CORPUS"):
            continue
        if not (r.get("verified_quotes") and r.get("doc_id")):
            continue
        quotes = [_norm(q) for q in r["verified_quotes"] if _norm(q)]
        # raw chunks carrying a full quote — for the rank diagnostic
        ans = {
            _chunk_key(c)
            for c in by_doc.get(r["doc_id"], [])
            if any(q in _norm(c["text"]) for q in quotes)
        }
        if not ans:
            # a quote no indexed chunk contains cannot be served by ANY
            # retrieval — counting it would charge retrieval with
            # chunking's sins
            continue
        out.append({"id": r["id"], "q": r["question"],
                    "role": r.get("role") or "soldier",
                    "doc_id": r["doc_id"], "answering": ans,
                    "quotes": quotes,
                    "runs": [run for q in quotes for run in _runs(q)]})
    return out


def _global_ranking(question: str, role: str) -> list[dict]:
    """The unwindowed ranking, same scorer the window is cut from."""
    doc_ids = [d["document_id"] for d in backend._docs_for_role(role)
               if d.get("document_id")]
    return vs.retrieve(question, n_results=RANK_POOL, max_per_doc=RANK_POOL,
                       top_doc_depth=RANK_POOL, doc_ids=doc_ids,
                       boost_docs=set())


def _free_window(question: str, role: str) -> list[dict]:
    """The ranking plus the free extensions only — see the module docstring."""
    win = backend.retrieve_for_role(question, role, route=set(), widen=False)
    hyde = backend.RETRIEVE_HYDE
    backend.RETRIEVE_HYDE = False
    try:
        return backend.widen_context(win, question, role, route=set())
    finally:
        backend.RETRIEVE_HYDE = hyde


def run(out_path: Path | None = None) -> dict:
    ts = targets()
    per = []
    for t in ts:
        win = _free_window(t["q"], t["role"])
        win_docs = {c["doc_id"] for c in win}
        served = [_norm(c["text"]) for c in win if c["doc_id"] == t["doc_id"]]
        sect = any(q in s for q in t["quotes"] for s in served) or \
               any(run in s for run in t["runs"] for s in served)
        # the content rule, on the served CURATED clauses of the answering
        # order only (raw chunks stay verbatim -- see CONTENT_MIN)
        curated = [c for c in win
                   if c["doc_id"] == t["doc_id"] and "key-facts" in (c.get("section") or "")]
        overlap = max((content_overlap(t["quotes"], (c.get("clause") or "") + " " + c["text"])
                       for c in curated), default=0.0)
        sect_content = bool(sect or overlap >= CONTENT_MIN)

        ranked = _global_ranking(t["q"], t["role"])
        doc_rank = sect_rank = None
        for i, c in enumerate(ranked, 1):
            if doc_rank is None and c["doc_id"] == t["doc_id"]:
                doc_rank = i
            if sect_rank is None and _chunk_key(c) in t["answering"]:
                sect_rank = i
            if doc_rank and sect_rank:
                break

        per.append({
            "id": t["id"], "doc_id": t["doc_id"],
            "doc_in_window": t["doc_id"] in win_docs,
            "sect_in_window": sect,
            "sect_content": sect_content, "sect_overlap": round(overlap, 2),
            "doc_rank": doc_rank, "sect_rank": sect_rank,
            "window_words": sum(len(c["text"].split()) for c in win),
            "window_docs": len(win_docs),
        })

    n = len(per)
    agg = {
        "n": n,
        "doc_in_window": sum(p["doc_in_window"] for p in per),
        "sect_in_window": sum(p["sect_in_window"] for p in per),
        "sect_in_window_content": sum(p["sect_content"] for p in per),
        "sect_rank_top8": sum(1 for p in per if p["sect_rank"] and p["sect_rank"] <= 8),
        "sect_rank_top25": sum(1 for p in per if p["sect_rank"] and p["sect_rank"] <= 25),
        "sect_rank_median": sorted((p["sect_rank"] or RANK_POOL + 1) for p in per)[n // 2],
        "avg_words": round(sum(p["window_words"] for p in per) / n),
        "avg_docs": round(sum(p["window_docs"] for p in per) / n, 1),
    }
    safe_print(f"[sectprobe] n={n}  doc-in-window {agg['doc_in_window']}/{n}  "
               f"sect-in-window {agg['sect_in_window']}/{n}  "
               f"(content rule: {agg['sect_in_window_content']}/{n})")
    safe_print(f"[sectprobe] sect-rank: top8 {agg['sect_rank_top8']}  "
               f"top25 {agg['sect_rank_top25']}  median {agg['sect_rank_median']}")
    safe_print(f"[sectprobe] window: {agg['avg_words']} words, {agg['avg_docs']} docs")
    if out_path:
        out_path.write_text(json.dumps({"agg": agg, "per": per}, ensure_ascii=False, indent=1),
                            encoding="utf-8")
        safe_print(f"[sectprobe] -> {out_path}")
    return agg


if __name__ == "__main__":
    run(Path(sys.argv[1]) if len(sys.argv) > 1 else None)
