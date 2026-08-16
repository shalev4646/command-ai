# -*- coding: utf-8 -*-
"""Structural tests for the metrics device-id column.

The contract under test is narrow and deliberate: device_id is LOG-ONLY. It
must reach the log, and it must not touch quota behaviour in any way -- that
is the whole reason this change was safe to ship days before the pilot.

Run: venv\\Scripts\\python.exe tests\\test_metrics_device.py
Prints only ASCII (cp1252 console pitfall)."""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import metrics


def _fresh_store():
    """Reset the process-wide counters -- _store() is shared across cases."""
    s = metrics._store()
    s["global_count"] = 0
    s["session_counts"] = {}
    s["questions"].clear()
    s["feedback"].clear()
    return s


def _log_to_temp(fn, **kwargs):
    """Run a log_* call with the JSONL sink redirected to a temp file, and
    return the parsed record. Without this the tests would append to the real
    storage/metrics_log.jsonl and pollute the pilot's own data."""
    real = metrics._JSONL_PATH
    with tempfile.TemporaryDirectory() as d:
        metrics._JSONL_PATH = Path(d) / "log.jsonl"
        try:
            fn(**kwargs)
            lines = metrics._JSONL_PATH.read_text(encoding="utf-8").splitlines()
        finally:
            metrics._JSONL_PATH = real
    assert len(lines) == 1, "expected exactly one record, got %d" % len(lines)
    return json.loads(lines[0])


def test_columns_are_append_only():
    """The real invariant, restated.

    This used to assert `_QUESTION_COLUMNS[-1] == "device"` and had been
    failing ever since `refused` was appended AFTER device -- which is exactly
    what the rule permits. The test was pinning a snapshot ("device is last")
    instead of the property that makes the snapshot safe ("nothing is ever
    inserted before an existing column"), so a correct, deliberate change read
    as a regression and the case stopped being believed.

    _append_to_sheet writes rows positionally against a header created on the
    tab's first-ever use, so a mid-list insert shifts every future row against
    live pilot data. Growth at the end is free; growth anywhere else is not.
    """
    # frozen prefixes: the column order as it existed when each tab's header
    # was first written. Everything after these may grow, in order, forever.
    frozen_q = ["ts", "session", "role", "question", "search_query", "doc_ids",
                "input_tokens", "cache_read", "cache_write", "output_tokens",
                "cost_usd", "latency_s", "answer_preview"]
    frozen_f = ["ts", "session", "role", "verdict", "question", "comment",
                "answer_preview", "doc_ids"]
    assert metrics._QUESTION_COLUMNS[:len(frozen_q)] == frozen_q, (
        "a column was inserted into the frozen prefix of the questions tab"
    )
    assert metrics._FEEDBACK_COLUMNS[:len(frozen_f)] == frozen_f, (
        "a column was inserted into the frozen prefix of the feedback tab"
    )
    for cols in (metrics._QUESTION_COLUMNS, metrics._FEEDBACK_COLUMNS):
        assert cols.count("device") == 1
        assert len(set(cols)) == len(cols), "duplicate column name"


def test_quota_still_keys_on_session_only():
    """The guarantee that made this change safe: reserve() never saw a device."""
    _fresh_store()
    for _ in range(metrics.USER_DAILY_LIMIT):
        assert metrics.reserve("sess-A") == "ok"
    assert metrics.reserve("sess-A") == "user", "per-session limit must still bite"
    # A second tab is still a fresh quota. That is UNCHANGED behaviour -- the
    # device column does not close this hole, it only makes it countable.
    assert metrics.reserve("sess-B") == "ok"


def test_global_limit_untouched():
    _fresh_store()
    ok = sum(1 for i in range(metrics.GLOBAL_DAILY_LIMIT + 5)
             if metrics.reserve("sess-%d" % i) == "ok")
    assert ok == metrics.GLOBAL_DAILY_LIMIT, "global cap moved: %d" % ok
    assert metrics.reserve("sess-late") == "global"


def test_device_reaches_the_question_log():
    _fresh_store()
    rec = _log_to_temp(
        metrics.log_question,
        session_id="sess-1", device_id="abc123def456", role="soldier",
        question="q", answer="a", sources=None, usage=None, latency_s=1.0,
    )
    assert rec["device"] == "abc123def456"
    assert rec["session"] == "sess-1", "session must survive alongside device"


def test_device_reaches_the_feedback_log():
    _fresh_store()
    rec = _log_to_temp(
        metrics.log_feedback,
        session_id="sess-1", device_id="abc123def456", role="soldier",
        verdict="up", question="q", answer="a", sources=None,
    )
    assert rec["device"] == "abc123def456"


def test_device_is_optional():
    """A caller that has no device (or an older cached build) must not crash."""
    _fresh_store()
    rec = _log_to_temp(
        metrics.log_question,
        session_id="sess-1", role="soldier", question="q", answer="a",
        sources=None, usage=None, latency_s=1.0,
    )
    assert rec["device"] == "", "missing device must log an empty cell, not fail"


def test_dashboard_counts_devices_not_tabs():
    """Two tabs, one device -- the number the pilot actually needs."""
    s = _fresh_store()
    today = metrics.date.today().isoformat()
    s["day"] = today
    for sess in ("sess-A", "sess-B"):
        s["questions"].appendleft(
            {"ts": today + "T10:00:00", "session": sess, "device": "dev-1",
             "cost_usd": 0.0})
    s["questions"].appendleft(
        {"ts": today + "T10:00:00", "session": "sess-C", "device": "dev-2",
         "cost_usd": 0.0})
    d = metrics.dashboard_data()
    assert d["devices_today"] == 2, "got %r" % d["devices_today"]


def test_dashboard_ignores_other_days_and_blanks():
    s = _fresh_store()
    today = metrics.date.today().isoformat()
    s["day"] = today
    s["questions"].appendleft({"ts": "2020-01-01T10:00:00", "session": "s",
                               "device": "old-dev", "cost_usd": 0.0})
    s["questions"].appendleft({"ts": today + "T10:00:00", "session": "s",
                               "device": "", "cost_usd": 0.0})
    d = metrics.dashboard_data()
    assert d["devices_today"] == 0, "got %r" % d["devices_today"]


def _swap_config(present):
    """Replace _sheets_config for the status cases and return the original.

    Needed because st.secrets is unavailable outside a Streamlit runtime, so
    the real _sheets_config always reports "no config" here -- which is the
    one branch these tests must NOT be stuck in."""
    real = metrics._sheets_config
    metrics._sheets_config = (
        (lambda: ({"client_email": "x"}, "http://sheet")) if present else (lambda: None))
    return real


def test_status_not_configured_without_secrets():
    s = _fresh_store()
    real = _swap_config(False)
    try:
        s["sheets_status"] = "not_configured"
        assert metrics.dashboard_data()["sheets_status"] == "not_configured"
        # a stale "ok" left over from before the config was lost must not be
        # reported as a live connection
        s["sheets_status"] = "ok"
        assert metrics.dashboard_data()["sheets_status"] == "not_configured"
    finally:
        metrics._sheets_config = real


def test_status_configured_before_any_write():
    """The exact bug being fixed: configured-but-idle read as not-configured,
    which is the normal state of a machine that has just been deployed."""
    s = _fresh_store()
    real = _swap_config(True)
    try:
        s["sheets_status"] = "not_configured"  # the value _store() boots with
        assert metrics.dashboard_data()["sheets_status"] == "configured"
    finally:
        metrics._sheets_config = real


def test_status_preserves_ok_and_error():
    s = _fresh_store()
    real = _swap_config(True)
    try:
        for want in ("ok", "error"):
            s["sheets_status"] = want
            got = metrics.dashboard_data()["sheets_status"]
            assert got == want, "%s became %s" % (want, got)
    finally:
        metrics._sheets_config = real


def test_check_sheets_reports_missing_config():
    real = _swap_config(False)
    try:
        ok, msg = metrics.check_sheets()
        assert ok is False
        assert msg.strip(), "a failed probe must explain itself"
    finally:
        metrics._sheets_config = real


def test_check_sheets_reports_a_bad_key_instead_of_raising():
    """A broken service account must surface as a message, not a traceback --
    the probe runs inside the admin page and must never take it down."""
    real = _swap_config(True)
    try:
        ok, msg = metrics.check_sheets()
        assert ok is False
        assert msg.strip()
    finally:
        metrics._sheets_config = real


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted({k: v for k, v in globals().items() if k.startswith("test_")}.items()):
        try:
            fn()
            print("PASS " + name)
        except AssertionError as e:
            fails += 1
            print("FAIL " + name + ": " + str(e).encode("ascii", "replace").decode())
    sys.exit(1 if fails else 0)
