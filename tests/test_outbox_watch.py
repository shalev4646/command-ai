# -*- coding: utf-8 -*-
"""night.outbox_watch: counts and ages of pending Sheet rows, never their text;
exit 1 only when a row is older than the threshold. No SSH (--local).

    venv\\Scripts\\python.exe tests\\test_outbox_watch.py
Prints only ASCII."""
import json
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from night import outbox_watch as W

NOW = datetime(2026, 9, 26, 22, 0, 0)


def _line(minutes_ago, tab="questions", attempts=1):
    return json.dumps({"row_id": "x", "tab": tab, "attempts": attempts, "row": ["SECRET QUESTION TEXT"],
                       "queued": (NOW - timedelta(minutes=minutes_ago)).isoformat(timespec="seconds")})


def test_empty_queue_is_healthy():
    s = W.summarize([], NOW)
    assert s == {"pending": 0, "oldest_minutes": 0.0, "max_attempts": 0, "by_tab": {}}


def test_the_oldest_row_and_the_tabs_are_counted():
    s = W.summarize([_line(5), _line(95, "feedback", 7), "not json"], NOW)
    assert s["pending"] == 2 and s["oldest_minutes"] == 95.0 and s["max_attempts"] == 7
    assert s["by_tab"] == {"questions": 1, "feedback": 1}


def test_exit_code_follows_the_threshold_and_no_text_is_printed():
    import io
    from contextlib import redirect_stdout
    real_now = W.datetime
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "outbox.jsonl"
        p.write_text(_line(10) + "\n", encoding="utf-8")

        class _DT(datetime):
            @classmethod
            def now(cls, tz=None):
                return NOW
        W.datetime = _DT
        try:
            buf = io.StringIO()
            with redirect_stdout(buf):
                ok = W.main(["--local", str(p), "--max-minutes", "30"])
                stuck = W.main(["--local", str(p), "--max-minutes", "5"])
        finally:
            W.datetime = real_now
    assert ok == 0 and stuck == 1
    assert "SECRET" not in buf.getvalue(), "counts and ages only"


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
