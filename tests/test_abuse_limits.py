# -*- coding: utf-8 -*-
"""Flood and brute-force limits.

Neither costs an API call, which is exactly why nothing stopped them: a script
could write to the metrics Sheet forever, and ?admin=1 is a public URL in front
of every question and report the pilot ever wrote.

metrics.py is imported directly - app.py runs _startup_ingest at import and
would buy ingestion through the paid API.

Run: venv/Scripts/python.exe tests/test_abuse_limits.py
"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import metrics  # noqa: E402

APP = (ROOT / "app.py").read_text(encoding="utf-8")


def check(name, cond, detail=""):
    line = ("PASS " if cond else "FAIL ") + name + ((" - " + detail) if detail else "")
    print(line.encode("ascii", "backslashreplace").decode("ascii"))
    return bool(cond)


def main():
    ok = True
    store = metrics._store()
    # a fresh slate that does not disturb a running process's real counters
    store["feedback_counts"] = {}
    store["admin_fails"] = 0

    # ── feedback flooding ──
    sess = "test-session-flood"
    accepted = sum(
        1 for i in range(metrics.FEEDBACK_DAILY_LIMIT + 15)
        if metrics.log_feedback(session_id=sess, role="soldier", verdict="down",
                                question="q", answer="a", sources=None)
    )
    ok &= check("feedback stops at the cap", accepted == metrics.FEEDBACK_DAILY_LIMIT,
                f"accepted={accepted} cap={metrics.FEEDBACK_DAILY_LIMIT}")
    ok &= check("the refusal is reported, not silent",
                metrics.log_feedback(session_id=sess, role="soldier", verdict="report",
                                     question="q", answer="a", sources=None) is False)
    other = metrics.log_feedback(session_id="a-different-tab", role="soldier",
                                 verdict="up", question="q", answer="a", sources=None)
    ok &= check("one flooder does not lock out everyone else", other is True)

    # a flood must not eat the questions the soldier paid for
    before = dict(store["session_counts"])
    metrics.log_feedback(session_id=sess, role="soldier", verdict="up",
                         question="q", answer="a", sources=None)
    ok &= check("feedback never consumes question quota", store["session_counts"] == before)

    # ── a new day clears it ──
    store["day"] = "2000-01-01"
    ok &= check("the cap lifts on a new day",
                metrics.log_feedback(session_id=sess, role="soldier", verdict="up",
                                     question="q", answer="a", sources=None) is True)

    # ── admin brute force ──
    store["admin_fails"] = 0
    ok &= check("the first wrong guess already costs a second",
                metrics.admin_backoff() == 1.0, str(metrics.admin_backoff()))
    delays = []
    for _ in range(6):
        metrics.note_admin_attempt(False)
        delays.append(metrics.admin_backoff())
    ok &= check("wrong guesses stall exponentially", delays[:3] == [2.0, 4.0, 8.0], str(delays[:3]))
    ok &= check("the stall is capped, not unbounded", max(delays) == float(metrics._ADMIN_FAIL_CAP),
                str(max(delays)))
    ok &= check("the counter is process-wide, not per tab",
                metrics._store() is store and store["admin_fails"] == 6)
    metrics.note_admin_attempt(True)
    ok &= check("a correct password clears the stall", metrics.admin_backoff() == 1.0)

    # ── the gate itself ──
    ok &= check("password compared in constant time", "hmac.compare_digest(entered, pw)" in APP)
    ok &= check("plain == comparison is gone", "entered == pw" not in APP)
    gate = APP.split("def _render_admin()")[1][:2000]
    ok &= check("a wrong guess actually sleeps", "time.sleep(metrics.admin_backoff())" in gate)

    # ── input ceilings ──
    ok &= check("the question cap is enforced server-side, not just on the widget",
                '(q or "")[:_MAX_QUESTION_CHARS]' in APP)
    ok &= check("the chat box carries the same ceiling",
                "max_chars=_MAX_QUESTION_CHARS" in APP)
    ok &= check("every free-text field is bounded", APP.count("max_chars=") >= 6,
                f"count={APP.count('max_chars=')}")

    # Run the REAL queue_question against a huge paste. app.py cannot be
    # imported (its import buys ingestion), so lift the function's source and
    # execute that - asserting on a slice written here would only test itself.
    src = APP[APP.index("def queue_question(q: str):"):]
    src = src[:src.index(chr(10) + "def ", 1)]
    cap = int(APP.split("_MAX_QUESTION_CHARS = ")[1].split(chr(10))[0])
    import types
    ns = {"st": types.SimpleNamespace(session_state=types.SimpleNamespace()),
          "_MAX_QUESTION_CHARS": cap}
    exec(compile(src, "queue_question", "exec"), ns)
    ns["queue_question"]("א" * 500_000)
    got = len(ns["st"].session_state.pending_question)
    ok &= check("a 500k paste is clamped before it can reach the API",
                got == cap, f"stored={got} cap={cap}")
    ns["queue_question"](None)
    ok &= check("a None question does not explode the clamp",
                ns["st"].session_state.pending_question == "")

    store["feedback_counts"] = {}
    store["admin_fails"] = 0
    print("\n" + ("ALL PASS" if ok else "FAILURES ABOVE"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
