# -*- coding: utf-8 -*-
"""Recovery for the arm's grading batch when the grading process died after submitting it (30.09: the
Haiku batch of opus5v2 ended with 72/72 succeeded, the process exited before writing the file or settling
its reservation). Reads the ended batch named in night/out/batch_grade-<tag>.json, rebuilds the rows exactly
as night.grade.grade_file does (same row order: the final rows that have an answer and a cached part list),
settles the open ledger reservation, writes grades_grade-<tag>.jsonl, and finishes with model_arm's
complete_grades so refused/empty rows come back as failures. Free: no model is called.
    python night/arm_review/collect_grades.py opus5v2"""
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from night import config as C  # noqa: E402
from night import grade as G  # noqa: E402
from night import model_arm as M  # noqa: E402
from night.ledger import Ledger, cost_usd  # noqa: E402

tag = sys.argv[1] if len(sys.argv) > 1 else "opus5v2"
label = f"grade-{tag}"
out = C.OUT / f"grades_{label}.jsonl"
if out.exists():
    raise SystemExit(f"[collect_grades] {out.name} already on disk — nothing to recover")
ticket = json.loads((C.OUT / f"batch_grade-{tag}.json").read_text(encoding="utf-8"))
import backend  # noqa: E402  the API client (a GET of results — free)

final = C.OUT / f"probe_{tag}.jsonl"
rows = [r for r in C.read_jsonl(final) if r.get("answer")]
parts = json.loads(G.PARTS.read_text(encoding="utf-8"))
rows = [r for r in rows if str(r["id"]) in parts]
b = backend.client.messages.batches.retrieve(ticket["batch_id"])
if b.processing_status != "ended":
    raise SystemExit(f"[collect_grades] batch {ticket['batch_id']} is {b.processing_status} — try later")
actual, got = 0.0, {}
for res in backend.client.messages.batches.results(ticket["batch_id"]):
    i = int(res.custom_id[1:])
    if res.result.type != "succeeded":
        continue
    m = res.result.message
    actual += cost_usd(G.MODEL, input_tokens=m.usage.input_tokens, output_tokens=m.usage.output_tokens, batch=True)
    try:
        got[i] = json.loads("".join(bl.text for bl in m.content if bl.type == "text"))
    except json.JSONDecodeError:
        pass
for i, r in enumerate(rows):
    g = got.get(i)
    if not g:
        r["grade"] = None
        continue
    want = len(parts[str(r["id"])])
    flags = (g.get("answered") or [])[:want]
    flags += [False] * (want - len(flags))
    r["grade"] = {"level": g.get("level"), "reason": g.get("reason"), "answered_parts": sum(1 for f in flags if f),
                  "unanswered_parts": sum(1 for f in flags if not f), "answered": flags, "parts": parts[str(r["id"])]}
ledger = Ledger(C.LEDGER)
ledger._merge_disk()
open_rids = [e["id"] for e in ledger._state["entries"] if e.get("label") == f"grade-{label}" and e.get("actual") is None]
if open_rids:
    ledger.settle(open_rids[-1], actual)
C.write_jsonl(out, rows)
print(f"[collect_grades] {label}: {len(got)}/{len(rows)} graded, ${actual:.3f}; reservation settled: {open_rids[-1] if open_rids else 'none open'}; "
      f"ledger ${ledger.spent:.2f}")
# the arm's finishing step (night.model_arm.cmd_grade, after night.grade)
full = C.read_jsonl(final)
done = M.complete_grades(full, rows, parts)
C.write_jsonl(out, done)
print(f"[collect_grades] {out.name}: {len(done)} rows, levels {dict(Counter((r.get('grade') or {}).get('level', 'ungraded') for r in done))}, "
      f"{sum(1 for r in done if (r.get('grade') or {}).get('reason', '').startswith('no usable'))} written back as failures")
