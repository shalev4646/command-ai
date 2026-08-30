# -*- coding: utf-8 -*-
"""Run every tests/test_*.py as its own process and summarise.

The suite is plain-assert scripts (no pytest in the venv); each file is
self-contained. Two guards keep a run unpaid: the app-importing files stub
backend.ensure_pdfs_ingested BEFORE importing app, and this runner blanks
ANTHROPIC_API_KEY in the child environment -- python-dotenv never overrides
an existing variable, so even a machine with a live .env cannot spend from
here. A file that genuinely needs the API has no place in this suite.

Run: venv\\Scripts\\python.exe tests\\run_all.py
Prints only ASCII (cp1252 console pitfall). Exit 0 iff every file passed
AND the run left no diff under the corpus stores -- the 2026-08-17 lesson:
corpus damage from a test run is invisible in test output and shows up only
as a git diff under storage/.
"""
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Paths a test run must never dirty. Checked via git so the check is exactly
# the one a human would run after the 2026-08-17 incident.
_GUARDED = ("storage/json_store", "storage/embedding_cache.npz")


def _ascii(line: str) -> str:
    return line.encode("ascii", "replace").decode()


def _guarded_diff() -> str:
    try:
        proc = subprocess.run(
            ["git", "status", "--porcelain", "--", *_GUARDED],
            cwd=ROOT, capture_output=True, text=True, timeout=30,
        )
        return proc.stdout.strip()
    except Exception:
        return ""  # no git (bare checkout) -- the guard is best-effort


def main() -> int:
    files = sorted((ROOT / "tests").glob("test_*.py"))
    env = dict(os.environ, PYTHONIOENCODING="utf-8", ANTHROPIC_API_KEY="")
    pre_diff = _guarded_diff()
    failures = []
    t0 = time.time()
    for f in files:
        start = time.time()
        try:
            proc = subprocess.run(
                [sys.executable, str(f)], cwd=ROOT, env=env,
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=600,
            )
            code, out = proc.returncode, proc.stdout + "\n" + proc.stderr
        except subprocess.TimeoutExpired:
            code, out = 1, "timed out after 600s"
        status = "PASS" if code == 0 else "FAIL"
        print(f"{status} {f.name} ({time.time() - start:.1f}s)", flush=True)
        if code != 0:
            failures.append(f.name)
            for line in out.strip().splitlines()[-15:]:
                print("   | " + _ascii(line))
    post_diff = _guarded_diff()
    if post_diff != pre_diff:
        failures.append("corpus-drift")
        print("FAIL corpus-drift - the run dirtied guarded storage paths:")
        for line in post_diff.splitlines():
            print("   | " + _ascii(line))
    print(f"{len(files) - sum(1 for x in failures if x != 'corpus-drift')}"
          f"/{len(files)} files passed ({time.time() - t0:.0f}s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
