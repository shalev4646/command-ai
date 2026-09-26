# -*- coding: utf-8 -*-
"""Daily check that pilot questions are reaching the Sheet (METRICS_OUTBOX).

The outbox on the machine's volume is empty when the Sheet accepts every row.
Rows that sit in it are rows the Sheet has NOT accepted yet, so the age of the
oldest pending row is the one number that says the logging chain is broken —
long before anyone opens the admin page.

    venv\\Scripts\\python.exe -m night.outbox_watch                     # read /data/outbox.jsonl over SSH
    venv\\Scripts\\python.exe -m night.outbox_watch --max-minutes 30
    venv\\Scripts\\python.exe -m night.outbox_watch --local path/to/outbox.jsonl   # no SSH

Read-only: `flyctl ssh console -C "cat <file>"` — nothing on the machine changes,
no model is called, no money moves. Exit code 0 = healthy (empty, or nothing
older than --max-minutes), 1 = rows are stuck, 2 = could not read the queue.
Prints counts and ages only — never a question's text.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime

APP = "commandai"
REMOTE = "/data/outbox.jsonl"


def read(local: str | None) -> list[str]:
    if local:
        with open(local, encoding="utf-8") as f:
            return f.read().splitlines()
    cmd = ["flyctl", "ssh", "console", "-a", APP, "-C", f"cat {REMOTE}"]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90)
    out, err = proc.stdout or "", proc.stderr or ""
    if "No such file" in out + err:
        return []                      # never written = nothing pending
    lines = [ln for ln in out.splitlines() if ln.strip().startswith("{")]
    if lines:
        return lines
    # an empty queue prints nothing; flyctl on Windows also ends with "Error: The
    # handle is invalid." after a good read — any OTHER error is a failed read
    if "Error" in err and "handle is invalid" not in err:
        print(f"[outbox] could not read {REMOTE}: {err.strip()[:200]}")
        raise SystemExit(2)
    return []


def summarize(lines: list[str], now: datetime | None = None) -> dict:
    now = now or datetime.now()
    entries = []
    for ln in lines:
        try:
            entries.append(json.loads(ln))
        except Exception:
            continue
    ages = []
    for e in entries:
        try:
            ages.append((now - datetime.fromisoformat(e.get("queued", ""))).total_seconds() / 60)
        except Exception:
            continue
    tabs: dict[str, int] = {}
    for e in entries:
        tabs[e.get("tab", "?")] = tabs.get(e.get("tab", "?"), 0) + 1
    return {"pending": len(entries), "oldest_minutes": round(max(ages), 1) if ages else 0.0,
            "max_attempts": max((e.get("attempts", 0) for e in entries), default=0), "by_tab": tabs}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--local")
    ap.add_argument("--max-minutes", type=float, default=30.0)
    a = ap.parse_args(argv)
    s = summarize(read(a.local))
    stuck = s["pending"] > 0 and s["oldest_minutes"] > a.max_minutes
    print(f"[outbox] pending {s['pending']} {s['by_tab']} | oldest {s['oldest_minutes']} min | "
          f"max attempts {s['max_attempts']} | {'STUCK' if stuck else 'ok'}")
    return 1 if stuck else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
