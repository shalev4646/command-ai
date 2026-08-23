# -*- coding: utf-8 -*-
"""Refuse to build an image out of a working tree nobody meant to ship.

The Dockerfile is `COPY . .` and `.dockerignore` excludes only
storage/chroma_db/, so `fly deploy` snapshots the disk EXACTLY as it stands —
including a corpus file some other process moved aside a minute ago, and
including edits nobody committed. Production then comes up healthy: the
machine boots fine without two orders, /healthz returns 200, the retrieval
gate never runs, and the only symptom is soldiers getting "המידע לא קיים" for
a whole family of questions.

That happened for real on 2026-08-24: a parallel session moved two json_store
files to /tmp to build a measurement's "before" arm. Nothing was broken and
nothing was wrong — but for those minutes, any deploy would have shipped a
corpus short חוק השיפוט הצבאי and קובץ-הקריאה למילואים.

HEAD is the source of truth, never a pinned number: the corpus grows, and a
constant here would have to be chased every time it does.

    venv\\Scripts\\python.exe _deploy_gate.py          # check, exit 1 on failure
    venv\\Scripts\\python.exe _deploy_gate.py --quiet  # only failures
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import os

# Lives at the repo root in normal use; CAI_ROOT lets it be exercised from
# elsewhere without being copied into the tree.
ROOT = Path(os.environ.get("CAI_ROOT", Path(__file__).resolve().parent))
CORPUS = "storage/json_store"
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def git(*args: str) -> str:
    out = subprocess.run(["git", "-C", str(ROOT), "-c", "core.quotepath=false", *args],
                         capture_output=True, text=True, encoding="utf-8")
    return out.stdout


def head_corpus_files() -> set[str]:
    return {ln for ln in git("ls-tree", "-r", "--name-only", "HEAD", CORPUS + "/").split("\n")
            if ln.endswith(".json")}


def disk_corpus_files() -> set[str]:
    return {f"{CORPUS}/{p.name}" for p in (ROOT / CORPUS).glob("*.json")}


def main(quiet: bool = False) -> int:
    failures: list[str] = []
    notes: list[str] = []

    head, disk = head_corpus_files(), disk_corpus_files()
    missing = sorted(head - disk)
    added = sorted(disk - head)

    # 1. a corpus file in HEAD that is not on disk WILL be missing from the image
    if missing:
        failures.append(
            f"{len(missing)} corpus file(s) in HEAD are not on disk — the image would ship "
            f"without them:\n" + "\n".join(f"      - {m}" for m in missing[:10]))

    # 2. a tracked corpus file edited but not committed ships in its edited state
    dirty = [ln[3:] for ln in git("status", "--porcelain", CORPUS + "/").split("\n")
             if ln[:2] in (" M", "MM", "AM")]
    if dirty:
        failures.append(
            f"{len(dirty)} corpus file(s) modified but not committed — the image would ship "
            f"the uncommitted version of:\n" + "\n".join(f"      - {d}" for d in dirty[:10]))

    # 3. untracked additions are legitimate (the night pipeline writes here), but
    #    they DO ship, so say so out loud rather than silently including them
    if added:
        notes.append(f"{len(added)} corpus file(s) on disk are not in HEAD and will ship anyway:\n"
                     + "\n".join(f"      + {a}" for a in added[:10]))

    # 4. same class, wider: any uncommitted code change is baked into the image
    code_dirty = [ln[3:] for ln in git("status", "--porcelain").split("\n")
                  if ln[:2] in (" M", "MM", "AM") and not ln[3:].startswith(CORPUS)
                  and ln[3:].endswith(".py")]
    if code_dirty:
        notes.append(f"{len(code_dirty)} uncommitted .py file(s) will ship as-is:\n"
                     + "\n".join(f"      ~ {c}" for c in code_dirty[:10]))

    # 5. a measurement in flight means the disk is deliberately not what it seems
    lock = ROOT / "night" / "out" / "MEASURING.lock"
    if lock.exists():
        failures.append("night/out/MEASURING.lock exists — a session is mid-measurement and may "
                        "have moved corpus files aside on purpose. Wait for it to clear.")

    if not quiet:
        print(f"corpus: {len(disk)} on disk, {len(head)} in HEAD")
        for n in notes:
            print(f"  note: {n}")
    for f in failures:
        print(f"  FAIL: {f}")

    if failures:
        print("\nDEPLOY GATE FAILED — do not build an image from this tree.")
        return 1
    if not quiet:
        print("\ndeploy gate passed — the tree is what it claims to be.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(quiet="--quiet" in sys.argv))
