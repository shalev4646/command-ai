# -*- coding: utf-8 -*-
"""Read the machine's per-question cost lines right after a live question.

The app appends one JSON record per answered question to
/app/storage/metrics_log.jsonl on the Fly machine (metrics.log_question). The
22.09 live check read it over SSH by hand; this is that read as a command, so
it runs the moment an answer lands and the number goes into the log verbatim.

    venv\\Scripts\\python.exe -m night.live_cost                 # last 2 records
    venv\\Scripts\\python.exe -m night.live_cost --mark          # remember the newest ts (before asking)
    venv\\Scripts\\python.exe -m night.live_cost --wait 180      # poll until a record newer than the mark lands
    venv\\Scripts\\python.exe -m night.live_cost --local storage/metrics_log.jsonl --n 3   # no SSH, parse a file

Read-only: `flyctl ssh console -C "tail -n N <file>"` — nothing on the machine
changes, no model is called, no money moves. The cost is re-derived from the
token counts with the 5-minute-cache formula the 22.09 check confirmed
(input $5/M, output $25/M, cache write $6.25/M, cache read $0.50/M) so a drift
between the logged cost_usd and the tokens is visible on the spot.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from common import safe_print  # noqa: E402

APP = "commandai"
REMOTE = "/app/storage/metrics_log.jsonl"
MARK = ROOT / "night" / "out" / "live_cost_mark.txt"
RATES = {"input_tokens": 5.0, "output_tokens": 25.0, "cache_write": 6.25, "cache_read": 0.50}  # USD per 1M


def fetch(n: int, local: str | None) -> list[str]:
    if local:
        lines = Path(local).read_text(encoding="utf-8").splitlines()
        return lines[-max(60, 8 * n):]
    # feedback rows share the file, so read a generous tail and filter below
    cmd = ["flyctl", "ssh", "console", "-a", APP, "-C", f"tail -n {max(60, 8 * n)} {REMOTE}"]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90)
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip().startswith("{")]
    # flyctl on Windows ends with "Error: The handle is invalid." after the
    # output has already arrived — the exit code lies, the lines do not
    if not lines:
        raise SystemExit(f"[live_cost] ssh returned no records ({proc.returncode}): "
                         f"{(proc.stderr or proc.stdout).strip()[:300]}")
    return lines


def records(lines: list[str]) -> list[dict]:
    out = []
    for ln in lines:
        try:
            r = json.loads(ln)
        except json.JSONDecodeError:
            continue
        if "cost_usd" in r and "question" in r:  # question records only (feedback rows have no cost)
            out.append(r)
    return out


def recomputed(r: dict) -> float:
    return sum(float(r.get(k, 0) or 0) * rate / 1e6 for k, rate in RATES.items())


def show(r: dict) -> None:
    safe_print(f"ts {r.get('ts')} · session {str(r.get('session', ''))[:12]} · role {r.get('role')} · doc_ids {r.get('doc_ids')}")
    safe_print(f"input_tokens {r.get('input_tokens')} · cache_read {r.get('cache_read')} · cache_write {r.get('cache_write')} · "
               f"output_tokens {r.get('output_tokens')} · cost_usd {r.get('cost_usd')} · latency_s {r.get('latency_s')} · refused {r.get('refused')}")
    calc = recomputed(r)
    logged = float(r.get("cost_usd") or 0)
    flag = "" if abs(calc - logged) < 0.0015 else "   ⚠ differs from the logged cost_usd"
    safe_print(f"recomputed (5m-cache formula) ${calc:.5f}{flag}")
    safe_print(f"question: {r.get('question')}")
    prev = (r.get("answer_preview") or "").replace("\n", " ⏎ ")
    safe_print(f"answer_preview: {prev[:400]}{'…' if len(prev) > 400 else ''}")
    safe_print("")


def newest_ts(recs: list[dict]) -> str:
    return max((r.get("ts") or "" for r in recs), default="")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=2, help="records to read from the tail")
    ap.add_argument("--local", help="parse this JSONL instead of the machine (no SSH)")
    ap.add_argument("--mark", action="store_true", help="store the newest ts as the baseline and exit")
    ap.add_argument("--wait", type=int, default=0, metavar="SECONDS",
                    help="poll every 10s until a record newer than the mark appears (then print only the new ones)")
    args = ap.parse_args()

    if args.mark:
        recs = records(fetch(args.n, args.local))
        ts = newest_ts(recs)
        MARK.parent.mkdir(parents=True, exist_ok=True)
        MARK.write_text(ts, encoding="utf-8")
        safe_print(f"[live_cost] mark = {ts or '(empty log)'} ({len(recs)} records seen)")
        return 0

    mark = MARK.read_text(encoding="utf-8").strip() if MARK.exists() else ""
    deadline = time.time() + args.wait
    while True:
        recs = records(fetch(args.n, args.local))
        new = [r for r in recs if (r.get("ts") or "") > mark] if mark else recs
        if new or not args.wait or time.time() >= deadline:
            break
        time.sleep(10)
    if args.wait and not new:
        safe_print(f"[live_cost] no record newer than mark {mark} after {args.wait}s"); return 1
    shown = new[-args.n:] if args.wait else recs[-args.n:]
    safe_print(f"[live_cost] {len(shown)} record(s){' newer than ' + mark if mark and args.wait else ''}:\n")
    for r in shown:
        show(r)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
