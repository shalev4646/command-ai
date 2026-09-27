# -*- coding: utf-8 -*-
"""The final test on the frozen ruler — night/FINAL_RULER_CRITERION.md.

All 72 real-style questions (97 parts) re-answered under the EXACT production
configuration: every [env] key of fly.toml is loaded into the environment before
backend is imported (HyDE and the router live, two passes), so the arm is the
deployed system, not a lookalike. Compared, paired, against grade-blocks6.

    venv\\Scripts\\python.exe -m night.final_arm dry    final159   # FREE: windows -> price
    venv\\Scripts\\python.exe -m night.final_arm p1     final159   # PAID (batch): first pass, all 72
    venv\\Scripts\\python.exe -m night.final_arm p2     final159   # PAID (batch): second pass where p1 declared a gap
    venv\\Scripts\\python.exe -m night.final_arm grade  final159   # PAID (cents): night.grade on the final answers
    venv\\Scripts\\python.exe -m night.final_arm report final159   # FREE: paired table vs blocks6

Recovery if a session dies mid-batch: python -m night.collect probe-<tag>_p1 (or _p2).
Each paid step refuses to run twice (its output file on disk = already bought).
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _fly_env() -> dict[str, str]:
    """The [env] table of fly.toml — the deployed flags, verbatim."""
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


FLY_ENV = _fly_env()
os.environ.update(FLY_ENV)            # before backend is imported anywhere

from common import safe_print  # noqa: E402
from night import config as C  # noqa: E402

QUESTIONS = C.OUT / "realstyle_questions.json"            # the frozen ruler
QUESTIONS_V2 = C.OUT / "realstyle_v2_questions.json"      # ruler v2 (27.09): same ids/parts, soldiers' wording
BASE = "grade-blocks6"
FIXED_USD, PER_KWORD_USD = 0.010, 0.0115     # Opus batch answer ≈ fixed + per 1k window words (18.09)
COMPOSE_USD = 0.010                          # HyDE + router per composed request (Haiku, not batched)
GRADE_USD = 0.15
P2_RATE_BLOCKS6 = 63 / 72                    # share that declared a gap in blocks6


def _questions(tag: str = "") -> list[dict]:
    """A tag ending in `v2` runs ruler v2 (night/FINAL_RULER_V2_CRITERION.md); any other, the frozen ruler."""
    path = QUESTIONS_V2 if tag.endswith("v2") else QUESTIONS
    return json.loads(path.read_text(encoding="utf-8"))


def _need_key() -> None:
    if not (os.environ.get("ANTHROPIC_API_KEY") or (ROOT / ".env").exists()):
        raise SystemExit("[final] no API key in this tree — paid steps run only from the tree with .env")


def _flag_line() -> str:
    import backend
    return (f"HYDE={int(backend.RETRIEVE_HYDE)} ROUTER_SLOTS={backend.RETRIEVE_ROUTER_SLOTS} "
            f"SECOND_PASS={backend.RETRIEVE_SECOND_PASS} DOC_BLOCKS={os.environ.get('RETRIEVE_DOC_BLOCKS')} "
            f"TERM_NOTE={os.environ.get('ANSWER_TERM_NOTE')} HOMONYMS={os.environ.get('RETRIEVE_HOMONYMS')} "
            f"QUOTELESS={os.environ.get('RETRIEVE_QUOTELESS')} window={backend.MAX_CONTEXT_CHUNKS}/"
            f"{backend.RETRIEVE_MAX_PER_DOC}/{backend.RETRIEVE_TOP_DOC_DEPTH}")


def cmd_dry(tag: str) -> int:
    """Free: the free window (HyDE off, router replay-free) of all 72 -> words -> dollars."""
    import backend
    qs = _questions(tag)
    hyde, backend.RETRIEVE_HYDE = backend.RETRIEVE_HYDE, False
    words = []
    try:
        for r in qs:
            win = backend.retrieve_for_role(r["q"], r["role"], route=set(), widen=False)
            win = backend.widen_context(win, r["q"], r["role"], route=set())
            words.append(sum(len(c["text"].split()) for c in win))
    finally:
        backend.RETRIEVE_HYDE = hyde
    n = len(qs)
    p1 = sum(FIXED_USD + PER_KWORD_USD * w / 1000 for w in words)
    srt = sorted(words)
    safe_print(f"[final] {tag} dry — questions: {(QUESTIONS_V2 if tag.endswith('v2') else QUESTIONS).name} — flags: {_flag_line()}")
    safe_print(f"[final] {n} questions, free-window words mean {sum(words)//n}, median {srt[n//2]}, max {max(words)}")
    for rate in (P2_RATE_BLOCKS6, 1.0):
        p2 = rate * p1
        compose = COMPOSE_USD * n * (1 + rate)
        total = p1 + p2 + compose + GRADE_USD
        safe_print(f"[final]   second pass on {rate:.1%}: pass1 ${p1:.2f} + pass2 ${p2:.2f} + compose ${compose:.2f} "
                   f"+ grading ${GRADE_USD:.2f} = ${total:.2f}")
    return 0


def cmd_pass(which: str, tag: str) -> int:
    _need_key()
    import backend
    from night.ledger import Ledger
    from night.probe import build_requests, run_batch
    out = C.OUT / f"probe_{tag}_{which}.jsonl"
    if out.exists():
        safe_print(f"[final] {out.name} already on disk — refusing to pay twice."); return 1
    rows = _questions(tag)
    if which == "p2":
        first = {r["id"]: r for r in C.read_jsonl(C.OUT / f"probe_{tag}_p1.jsonl")}
        if len(first) != len(rows):
            safe_print(f"[final] first pass has {len(first)} rows, expected {len(rows)} — collect it first."); return 1
        rows = [{**r, "first_answer": first[r["id"]]["answer"]} for r in rows
                if first.get(r["id"], {}).get("answer") and backend.lacked_from(first[r["id"]]["answer"])]
    safe_print(f"[final] {tag} {which}: {len(rows)} requests — flags: {_flag_line()}")
    reqs, meta = build_requests(rows)
    run_batch(reqs, meta, Ledger(C.LEDGER), out, f"probe-{tag}_{which}")
    return 0


def _final_rows(tag: str) -> list[dict]:
    p1 = {r["id"]: r for r in C.read_jsonl(C.OUT / f"probe_{tag}_p1.jsonl")}
    p2 = {r["id"]: r for r in C.read_jsonl(C.OUT / f"probe_{tag}_p2.jsonl")}
    out = []
    for i, r in p1.items():
        row = {**r, "first_answer": r.get("answer")}
        if i in p2 and p2[i].get("answer"):
            row.update({"answer": p2[i]["answer"], "sources": p2[i].get("sources"),
                        "context_words": p2[i].get("context_words"), "second_pass": True})
        out.append(row)
    return out


def cmd_grade(tag: str) -> int:
    _need_key()
    from night.grade import grade_file
    from night.ledger import Ledger
    final = C.OUT / f"probe_{tag}.jsonl"
    if not final.exists():
        rows = _final_rows(tag)
        C.write_jsonl(final, rows)
        safe_print(f"[final] {final.name}: {len(rows)} rows ({sum(1 for r in rows if r.get('second_pass'))} with a second pass)")
    grade_file(final, Ledger(C.LEDGER), f"grade-{tag}")
    return 0


def cmd_report(tag: str) -> int:
    base = {r["id"]: r for r in C.read_jsonl(C.OUT / f"grades_{BASE}.jsonl")}
    arm = {r["id"]: r for r in C.read_jsonl(C.OUT / f"grades_grade-{tag}.jsonl")}
    def lv(r): return ((r or {}).get("grade") or {}).get("level", "ungraded")
    changed, new_src = [], []
    for i in sorted(base):
        b, a = base[i], arm.get(i)
        if lv(b) != lv(a):
            changed.append((i, lv(b), lv(a)))
        if a and set(a.get("sources") or []) - set(b.get("sources") or []):
            new_src.append(i)
    count = lambda d: {k: sum(1 for r in d.values() if lv(r) == k) for k in sorted({lv(r) for r in d.values()})}
    safe_print(f"[final] {BASE}: {count(base)}")
    safe_print(f"[final] grade-{tag}: {count(arm)}")
    safe_print(f"[final] rows whose grade changed ({len(changed)}), all read in stage 6:")
    for i, b, a in changed:
        safe_print(f"   {i}: {b} -> {a}")
    safe_print(f"[final] rows whose sources gained an order vs blocks6 ({len(new_src)}) — read for confident-and-wrong: {new_src}")
    words = [r.get("context_words") or 0 for r in arm.values()]
    safe_print(f"[final] mean context words {sum(words)//max(1,len(words))}; second passes {sum(1 for r in arm.values() if r.get('second_pass'))}")
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "dry"
    tag = sys.argv[2] if len(sys.argv) > 2 else "final159"
    fn = {"dry": lambda: cmd_dry(tag), "p1": lambda: cmd_pass("p1", tag), "p2": lambda: cmd_pass("p2", tag),
          "grade": lambda: cmd_grade(tag), "report": lambda: cmd_report(tag)}[cmd]
    raise SystemExit(fn())
