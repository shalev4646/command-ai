# -*- coding: utf-8 -*-
"""METRICS_OUTBOX — the durable retry queue in front of the Sheet (metrics.py).

Off (unset) is the old path verbatim; a missing folder falls back to it; a
failed send stays queued and goes out when the Sheet heals; a reboot with a
full queue sends everything in order; a row whose confirmation was lost is not
written twice (row_id, the last column). No network: the Sheet is a fake.

    venv\\Scripts\\python.exe tests\\test_metrics_outbox.py
Prints only ASCII."""
import json
import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import metrics

APP = (Path(__file__).resolve().parent.parent / "app.py").read_text(encoding="utf-8")
CONFIG = ({"type": "service_account"}, "https://sheet.example/fake")
RECORD = {"ts": "2026-09-26T20:00:00", "session": "s1", "device": "abcdef012345", "role": "soldier",
          "question": "q", "answer_preview": "a", "refused": False}


class FakeSheet:
    """Rows per tab; `fail` makes every append fail; `write_then_fail` writes the
    row and THEN reports failure (a timeout after Google accepted it)."""

    def __init__(self):
        self.rows, self.fail, self.write_then_fail, self.appends = {}, False, False, 0

    def append(self, tab, columns, row, config):
        self.appends += 1
        if self.fail:
            return False
        self.rows.setdefault(tab, []).append((list(columns), list(row)))
        return not self.write_then_fail

    def has(self, tab, row_id, config):
        return any(r[-1] == row_id for _, r in self.rows.get(tab, []))


@contextmanager
def _env(outbox: str | None, sheet: FakeSheet | None = None, sync_kick: bool = True):
    saved = {k: getattr(metrics, k) for k in
             ("_JSONL_PATH", "_sheets_config", "_append_to_sheet", "_sheet_has_row_id", "_outbox_kick")}
    old_env = os.environ.get(metrics._OUTBOX_ENV)
    with tempfile.TemporaryDirectory() as d:
        metrics._JSONL_PATH = Path(d) / "log.jsonl"
        metrics._sheets_config = lambda: CONFIG
        if sheet is not None:
            metrics._append_to_sheet = sheet.append
            metrics._sheet_has_row_id = sheet.has
        if sync_kick:
            metrics._outbox_kick = lambda box, config: metrics._outbox_flush(box, config)
        if outbox is None:
            os.environ.pop(metrics._OUTBOX_ENV, None)
        else:
            os.environ[metrics._OUTBOX_ENV] = outbox.replace("{tmp}", d)
        try:
            yield Path(d)
        finally:
            for k, v in saved.items():
                setattr(metrics, k, v)
            if old_env is None:
                os.environ.pop(metrics._OUTBOX_ENV, None)
            else:
                os.environ[metrics._OUTBOX_ENV] = old_env


def test_off_is_the_old_path_and_writes_no_queue():
    started = []

    class _T:
        def __init__(self, target=None, args=(), daemon=None):
            started.append((target, args))

        def start(self):
            pass

    real_thread = metrics.threading.Thread
    with _env(None) as d:
        metrics.threading.Thread = _T
        try:
            metrics._persist("questions", metrics._QUESTION_COLUMNS, RECORD)
        finally:
            metrics.threading.Thread = real_thread
        assert len(started) == 1 and started[0][0] is metrics._append_to_sheet, started
        assert started[0][1][1] == metrics._QUESTION_COLUMNS, "off: no row_id column"
        assert not any(p.name.startswith("outbox") for p in d.iterdir())
        assert metrics.outbox_status() is None
        assert metrics.outbox_boot() is False


def test_missing_folder_falls_back_without_crashing():
    started = []
    real_thread = metrics.threading.Thread

    class _T:
        def __init__(self, target=None, args=(), daemon=None):
            started.append(target)

        def start(self):
            pass

    with _env("{tmp}/no_such_dir/outbox.jsonl"):
        metrics.threading.Thread = _T
        try:
            metrics._persist("questions", metrics._QUESTION_COLUMNS, RECORD)
        finally:
            metrics.threading.Thread = real_thread
        assert started == [metrics._append_to_sheet]
        st = metrics.outbox_status()
        assert st is not None and st["available"] is False
        assert metrics.outbox_boot() is False


def test_a_failed_send_stays_queued_and_goes_out_once_the_sheet_heals():
    sheet = FakeSheet()
    sheet.fail = True
    with _env("{tmp}/outbox.jsonl", sheet) as d:
        box = d / "outbox.jsonl"
        metrics._persist("questions", metrics._QUESTION_COLUMNS, RECORD)
        q = metrics._outbox_read(box)
        assert len(q) == 1 and q[0]["attempts"] == 1, q
        assert sheet.rows == {}
        assert metrics.outbox_status()["pending"] == 1
        sheet.fail = False
        assert metrics._outbox_flush(box, CONFIG) == 1
        assert metrics._outbox_read(box) == []
        cols, row = sheet.rows["questions"][0]
        assert cols == metrics._QUESTION_COLUMNS + ["row_id"] and row[-1] == q[0]["row_id"]
        assert row[cols.index("question")] == "q"


def test_a_reboot_with_a_full_queue_sends_everything_in_order():
    sheet = FakeSheet()
    with _env("{tmp}/outbox.jsonl", sheet) as d:
        box = d / "outbox.jsonl"
        for i in range(3):   # left behind by a previous process
            assert metrics._outbox_add(box, "questions", metrics._QUESTION_COLUMNS, {**RECORD, "question": f"q{i}"})
        assert len(metrics._outbox_read(box)) == 3
        metrics._outbox_loop(box, CONFIG, rounds=1)          # what outbox_boot's thread runs first
        sent = [r[metrics._QUESTION_COLUMNS.index("question")] for _, r in sheet.rows["questions"]]
        assert sent == ["q0", "q1", "q2"], sent
        assert metrics._outbox_read(box) == []


def test_boot_starts_the_retry_loop_once():
    started = []
    real_thread = metrics.threading.Thread

    class _T:
        def __init__(self, target=None, args=(), daemon=None):
            started.append(target)

        def start(self):
            pass

    with _env("{tmp}/outbox.jsonl", FakeSheet()):
        metrics._OUTBOX_BOOTED["done"] = False
        metrics.threading.Thread = _T
        try:
            assert metrics.outbox_boot() is True
            assert metrics.outbox_boot() is False
        finally:
            metrics.threading.Thread = real_thread
            metrics._OUTBOX_BOOTED["done"] = False
        assert started == [metrics._outbox_loop]


def test_a_lost_confirmation_is_not_written_twice():
    sheet = FakeSheet()
    sheet.write_then_fail = True           # Google wrote it, the reply never came
    with _env("{tmp}/outbox.jsonl", sheet) as d:
        box = d / "outbox.jsonl"
        metrics._persist("questions", metrics._QUESTION_COLUMNS, RECORD)
        assert len(sheet.rows["questions"]) == 1
        assert len(metrics._outbox_read(box)) == 1, "unconfirmed: still queued"
        sheet.write_then_fail = False
        assert metrics._outbox_flush(box, CONFIG) == 1
        assert len(sheet.rows["questions"]) == 1, "the second attempt found row_id and did not append"
        assert sheet.appends == 1
        assert metrics._outbox_read(box) == []


def test_row_id_is_the_last_column_and_a_torn_line_is_ignored():
    with _env("{tmp}/outbox.jsonl", FakeSheet(), sync_kick=False) as d:
        box = d / "outbox.jsonl"
        assert metrics._outbox_add(box, "feedback", metrics._FEEDBACK_COLUMNS, RECORD)
        with box.open("a", encoding="utf-8") as f:
            f.write('{"row_id": "torn')      # a crash mid-write
        q = metrics._outbox_read(box)
        assert len(q) == 1
        assert q[0]["columns"][:-1] == metrics._FEEDBACK_COLUMNS and q[0]["columns"][-1] == "row_id"
        assert q[0]["row"][-1] == q[0]["row_id"]


def test_the_privacy_policy_says_where_a_row_waits():
    body = APP.split("_PRIVACY_SECTIONS = [")[1].split("\n]")[0]
    assert "עד שנשמרת בגיליון, השורה מוחזקת זמנית בשרת האחסון (Fly.io)." in body
    assert body.index("Fly.io") > body.index("Google Sheets"), "the sentence belongs to the Sheets line"


def test_app_boots_the_outbox_guarded():
    assert "metrics.outbox_boot()" in APP
    i = APP.index("metrics.outbox_boot()")
    assert "try:" in APP[i - 200:i], "a failure here must never take the app down"


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS", name)
            except AssertionError as e:
                fails += 1
                print("FAIL", name, ascii(str(e))[:300])
    raise SystemExit(1 if fails else 0)
