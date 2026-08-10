"""Shared paths, constants, and I/O helpers for the overnight run.

Every stage writes JSONL and reads the previous stage's JSONL, so a crash at
4am resumes instead of restarting. Hebrew goes to disk as UTF-8 explicitly —
Windows stdout defaults to cp1252 and raises on Hebrew (see common.safe_print).
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "night" / "out"
OUT.mkdir(parents=True, exist_ok=True)

# stage artifacts
QUESTIONS = OUT / "questions.jsonl"      # 1: generated questions
ROUTES = OUT / "routes.json"             # 2a: question -> routed doc_ids (paid once, reused all night)
SWEEP = OUT / "sweep.jsonl"              # 2b: retrieval result + band per question
CLUSTERS = OUT / "clusters.json"         # 3a: failures grouped into work items
FIXES = OUT / "fixes.jsonl"              # 3b: proposed fixes with verdicts
PROBE_BASE = OUT / "probe_baseline.jsonl"   # 4a: paid Opus answers, before
PROBE_AFTER = OUT / "probe_after.jsonl"     # 4b: paid Opus answers, after
GRADES = OUT / "grades.jsonl"            # 4c: 4-way labels
LEDGER = OUT / "ledger.json"
LOG = OUT / "night.log"

REPORT = ROOT / "REPORT.md"
SHOPPING_LIST = ROOT / "SHOPPING_LIST.md"

ROLES = ("soldier", "commander", "reserve")

# --- retrieval banding -------------------------------------------------------
# Bands are set from the observed score distribution, not guessed: sweep.py
# calibrates them against the 133 golden cases (known-good retrievals) so
# "green" means "scores like a case we know answers correctly".
BAND_GREEN = "green"     # strong retrieval — target present, high score
BAND_YELLOW = "yellow"   # plausible but not decisive — where paid probing buys the most
BAND_RED = "red"         # nothing crossed threshold — a gap

# --- question generation -----------------------------------------------------
# Sized to what is left after curation over-ran its estimate ($4.59 of the $10
# ceiling). The router is the constraint, not generation: every question costs
# $0.00125 to route even in batch, and routing is what makes the sweep faithful.
N_BLIND = 500            # written with no corpus access — the honest coverage metric
N_INSIDE_OUT = 3         # questions per order (98 orders); reachability, not coverage
UGLY_FRACTION = 0.25     # typos, slang, fragments, OCR-mangled order numbers

# --- paid sampling -----------------------------------------------------------
N_PROBE = 100            # baseline sample from the yellow band; ±10% at n=100
N_CONTROL = 15           # unchanged questions re-run after, to measure sampling flip-rate


def write_jsonl(path: Path, rows) -> int:
    """Overwrite `path` with `rows`. Returns the count written."""
    n = 0
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
            n += 1
    return n


def append_jsonl(path: Path, row: dict) -> None:
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> list[dict]:
    if not Path(path).exists():
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def log(msg: str) -> None:
    """Timestamp-free append log — the orchestrator stamps stage boundaries.

    Writes to disk as UTF-8 and mirrors to stdout via safe_print, so Hebrew
    survives both a cp1252 console and a piped stdout.
    """
    from common import safe_print
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(msg + "\n")
    safe_print(msg)
