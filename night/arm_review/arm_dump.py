# -*- coding: utf-8 -*-
"""Stage-6 reading dump for the Opus 5 arm (free): all 72 rows in ruler order, three files of 24, each row with the
question, the adjudication, the base answer and its stage-6 verdict, and the arm's final answer (plus the rejected
pass when the production choice kept the first). Also, for every quoted string of the arm not found verbatim in
the window, the window passages that carry its rarest words — paraphrase or invention is then read, not guessed.
    python night/arm_review/arm_dump.py opus5v2  -> night/out/arm_review/arm_dump_{1,2,3}.txt, arm_quote_misses.txt"""
import json
import re
import sys
from pathlib import Path

RUN = Path(__file__).resolve().parents[2]
OUT = RUN / "night" / "out"
HERE = Path(__file__).parent
sys.path.insert(0, str(RUN))
sys.path.insert(0, str(HERE))
from review_view import check, corpus_text, norm  # noqa: E402
from night.model_arm import PROTECTED, ANSWER_SIDE  # noqa: E402

tag = sys.argv[1]
BASE = "final161v2"
rows = lambda p: {json.loads(l)["id"]: json.loads(l) for l in open(p, encoding="utf-8") if l.strip()}
arm = rows(OUT / f"probe_{tag}.jsonl")
base_sheet = {x["id"]: x for x in json.loads((OUT / f"review_sheet_{BASE}.json").read_text(encoding="utf-8"))}
base_review = json.loads((OUT / f"review_grade-{BASE}.json").read_text(encoding="utf-8"))
order = list(base_sheet)
corpus = corpus_text()
DUMP = OUT / "arm_review"
DUMP.mkdir(parents=True, exist_ok=True)


def block(i: int, rid: str) -> str:
    x, r = base_sheet[rid], arm[rid]
    br = base_review.get(rid) or {}
    tags = ("PROTECTED " if rid in PROTECTED else "") + ("ANSWER-SIDE " if rid in ANSWER_SIDE else "")
    miss = [q for q, in_w, _ in check(r.get("answer") or "", r.get("sent_user_content") or "", corpus) if not in_w]
    out = [f"\n{'#' * 90}\n### {i:02d} {rid} [{x['role']}] {tags}adj={x['verdict']} doc={x.get('doc')} parts={x.get('parts')}",
           f"Q: {x['q']}"]
    for q in x.get("quotes") or []:
        out.append(f"ADJ-QUOTE: {q[:400]}")
    out.append(f"--- BASE 4.8 stage-6: {br.get('verdict')} ({br.get('answered_parts')}) {br.get('note', '')}")
    out.append((x.get("answer") or "")[:900])
    out.append(f"--- ARM kept={r.get('kept')} stop={r.get('stop_reason')} sources={r.get('sources')}")
    out.append(r.get("answer") or "(EMPTY)")
    other = r.get("first_answer") if r.get("kept") == "second" else r.get("second_answer")
    if r.get("kept") == "kept_ruling" and other:
        out.append(f"--- ARM rejected second pass: {other[:700]}")
    for q in miss:
        out.append(f"!! QUOTE NOT VERBATIM IN WINDOW: {q[:200]}")
    return "\n".join(out)


for part in range(3):
    ids = order[part * 24:(part + 1) * 24]
    (DUMP / f"arm_dump_{part + 1}.txt").write_text("\n".join(block(part * 24 + k, rid) for k, rid in enumerate(ids)), encoding="utf-8")

# the six: window passages around the rarest words of each missed quote
lines = []
for rid in order:
    r = arm[rid]
    win = r.get("sent_user_content") or ""
    for q, in_w, where in check(r.get("answer") or "", win, corpus):
        if in_w:
            continue
        words = [w for w in re.findall(r"[\u05d0-\u05ea]{4,}", q)]
        lines.append(f"\n=== {rid}: {q[:220]}\n    corpus-found-in: {where or 'NOT FOUND'}")
        wn = norm(win)
        hits = []
        for w in sorted(set(words), key=len, reverse=True)[:8]:
            for m in re.finditer(re.escape(w), win):
                s, e = max(0, m.start() - 160), min(len(win), m.end() + 160)
                hits.append((m.start(), win[s:e].replace("\n", " ")))
        seen = set()
        for pos, ctx in sorted(hits)[:10]:
            key = pos // 200
            if key in seen:
                continue
            seen.add(key)
            lines.append(f"    window@{pos}: …{ctx}…")
(DUMP / "arm_quote_misses.txt").write_text("\n".join(lines), encoding="utf-8")
print("written", [f"arm_dump_{k}.txt" for k in (1, 2, 3)], "arm_quote_misses.txt")
