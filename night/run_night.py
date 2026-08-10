"""Chain the remaining stages so the night finishes unattended.

Each stage is skipped when its artifact already exists, so a crash at 4am
resumes rather than restarting — and re-running this file is always safe.
The ledger's hard ceiling is the backstop: if a stage would cross $10 it
raises before the call goes out, the chain stops, and everything produced so
far is already on disk.
"""
from __future__ import annotations

import traceback

from night import config as C
from night.ledger import Ledger, BudgetExceeded


def stage(name: str, artifact, fn) -> bool:
    if artifact is not None and artifact.exists() and artifact.stat().st_size > 0:
        C.log(f"[night] {name}: already done ({artifact.name}), skipping")
        return True
    C.log(f"[night] ===== {name} =====")
    try:
        fn()
        return True
    except BudgetExceeded as e:
        C.log(f"[night] {name}: STOPPED ON BUDGET — {e}")
        return False
    except Exception:
        C.log(f"[night] {name}: FAILED\n{traceback.format_exc()}")
        return False


def main() -> None:
    ledger = Ledger(C.LEDGER)
    C.log(f"[night] starting with ${ledger.remaining():.2f} of budget left")

    from night import genq, run_sweep, probe, grade, report_night

    if not stage("generate questions", C.QUESTIONS, lambda: genq.generate(ledger)):
        return
    if not stage("retrieval sweep", C.SWEEP, run_sweep.main):
        return
    if not stage("paid probe", C.PROBE_BASE, probe.baseline):
        C.log("[night] probe did not complete — the free findings still stand")
    else:
        stage("grade answers", C.OUT / "grades_baseline.jsonl",
              lambda: grade.grade_file(C.PROBE_BASE, Ledger(C.LEDGER), "baseline"))

    stage("morning report", None, report_night.build)
    C.log(Ledger(C.LEDGER).summary())


if __name__ == "__main__":
    main()
