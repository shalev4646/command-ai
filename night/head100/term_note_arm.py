# -*- coding: utf-8 -*-
"""ANSWER_TERM_NOTE — the paid answer-side test (night/TERM_NOTE_CRITERION.md).

14 questions — the 12 locked homonym phrasings (night/head100/homonym_held.json)
plus the two contaminated held rows (hW3a, hR1db; held_tuned.json) — composed
twice in ONE batch: arm 0 (ANSWER_TERM_NOTE=0) and arm 1 (=1). First pass only,
production flags, RETRIEVE_HOMONYMS off in both. Grading with night.grade per
arm, then the manual reading of all 28 answers (stage 6).

    venv\\Scripts\\python.exe -m night.head100.term_note_arm dry      # free: sample + price
    venv\\Scripts\\python.exe -m night.head100.term_note_arm p1       # PAID (batch)
    venv\\Scripts\\python.exe -m night.head100.term_note_arm grade    # PAID (cents)
    venv\\Scripts\\python.exe -m night.head100.term_note_arm report   # free

If the session dies mid-batch: python -m night.collect probe-termnote_p1
(the collector writes the mixed file; `grade` splits it by arm).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from common import safe_print  # noqa: E402
from night import config as C  # noqa: E402

HERE = Path(__file__).resolve().parent
TAG = "termnote"
FIXED_USD, PER_KWORD_USD, COMPOSE_USD = 0.010, 0.0115, 0.016


def sample() -> list[dict]:
    hom = json.loads((HERE / "homonym_held.json").read_text(encoding="utf-8"))["rows"]
    tuned = {r["id"] for r in json.loads((HERE / "held_tuned.json").read_text(encoding="utf-8"))["tuned"]}
    targets = {r["id"]: r for r in json.loads((HERE / "targets.json").read_text(encoding="utf-8"))}
    rows = [{"id": r["id"], "q": r["question"], "clean_q": r["question"], "role": r["role"], "source": "homonym_held",
             "band": "homonym", "target_doc": r["doc_id"], "leaked": bool(r.get("leaked"))} for r in hom]
    rows += [{"id": i, "q": targets[i]["question"], "clean_q": targets[i]["question"], "role": targets[i]["role"],
              "source": "held_tuned", "band": "tuned", "target_doc": targets[i]["doc_id"], "leaked": False}
             for i in sorted(tuned)]
    return rows


def _need_key() -> None:
    if not (os.environ.get("ANTHROPIC_API_KEY") or (ROOT / ".env").exists()):
        raise SystemExit("[termnote] no API key in this tree")


def cmd_dry() -> int:
    import backend
    rows = sample()
    hyde, backend.RETRIEVE_HYDE = backend.RETRIEVE_HYDE, False
    words = []
    try:
        for r in rows:
            win = backend.retrieve_for_role(r["q"], r["role"], route=set(), widen=False)
            win = backend.widen_context(win, r["q"], r["role"], route=set())
            words.append(sum(len(c["text"].split()) for c in win))
    finally:
        backend.RETRIEVE_HYDE = hyde
    n = len(rows)
    per = sum(FIXED_USD + PER_KWORD_USD * w / 1000 for w in words)
    total = 2 * per + 2 * n * COMPOSE_USD + 0.10
    safe_print(f"[termnote] dry: {n} questions x 2 arms, window mean {sum(words)//n} words (max {max(words)}); "
               f"answers ~${2*per:.2f} + compose ~${2*n*COMPOSE_USD:.2f} + grading ~$0.10 = ~${total:.2f}  "
               f"(RETRIEVE_HOMONYMS={os.environ.get('RETRIEVE_HOMONYMS', '0')}, ANSWER_TERM_NOTE arms 0/1)")
    return 0


def cmd_p1() -> int:
    _need_key()
    import backend
    from night.ledger import Ledger
    from night.probe import build_requests, run_batch
    out = C.OUT / f"probe_{TAG}_p1.jsonl"
    if out.exists():
        safe_print(f"[termnote] {out.name} already on disk — refusing to pay twice."); return 1
    from storage import glossary as _g
    if getattr(_g, "RETRIEVE_HOMONYMS", False):
        raise SystemExit("[termnote] RETRIEVE_HOMONYMS must be off in both arms (criterion)")
    rows = sample()
    reqs, meta = [], []
    for arm in (0, 1):
        backend.ANSWER_TERM_NOTE = arm
        r_, m_ = build_requests([{**r, "id": f"{r['id']}@{arm}"} for r in rows])
        for i, req in enumerate(r_):
            req["custom_id"] = f"p{len(reqs) + i}"
        reqs += r_
        meta += [{**m, "arm": arm} for m in m_]
    backend.ANSWER_TERM_NOTE = 0
    noted = sum(1 for m in meta if m["arm"] == 1 and "הבהרת מונחים" in json.dumps(
        [r for r in reqs if r["custom_id"] == f"p{meta.index(m)}"][0]["params"]["messages"][0]["content"], ensure_ascii=False))
    safe_print(f"[termnote] composed {len(reqs)} requests ({len(rows)} x 2 arms); arm 1 carries a term note in {noted}/{len(rows)}")
    run_batch(reqs, meta, Ledger(C.LEDGER), out, f"probe-{TAG}_p1")
    return 0


def cmd_grade() -> int:
    _need_key()
    from night.grade import grade_file
    from night.ledger import Ledger
    rows = C.read_jsonl(C.OUT / f"probe_{TAG}_p1.jsonl")
    for arm in (0, 1):
        part = [{**r, "id": r["id"].split("@")[0]} for r in rows if r.get("arm") == arm]
        out = C.OUT / f"probe_{TAG}_arm{arm}.jsonl"
        C.write_jsonl(out, part)
        grade_file(out, Ledger(C.LEDGER), f"grade-{TAG}_arm{arm}")
    return 0


def cmd_report() -> int:
    for arm in (0, 1):
        rows = C.read_jsonl(C.OUT / f"grades_grade-{TAG}_arm{arm}.jsonl")
        lv: dict[str, int] = {}
        for r in rows:
            k = (r.get("grade") or {}).get("level", "ungraded"); lv[k] = lv.get(k, 0) + 1
        tgt_in = sum(1 for r in rows if r.get("target_doc") and r["target_doc"] in (r.get("sources") or []))
        safe_print(f"[termnote] arm {arm}: {lv} | target order among sources {tgt_in}/{len(rows)} | "
                   f"leaked rows {sum(1 for r in rows if r.get('leaked'))}")
    safe_print("[termnote] criterion is read by hand on all 28 answers (TERM_NOTE_CRITERION.md): "
               "0 confident-and-wrong in arm 1; both tuned rows land the right sense or declare ambiguity; "
               "no answer right in arm 0 and wrong in arm 1. Report 12 and 12-without-leaked separately.")
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "dry"
    raise SystemExit({"dry": cmd_dry, "p1": cmd_p1, "grade": cmd_grade, "report": cmd_report}[cmd]())
