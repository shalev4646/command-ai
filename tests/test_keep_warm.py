# -*- coding: utf-8 -*-
"""Server keep-warm (2026-09-11): the heavy state warms from process start,
not inside the first session, and a probe keeps it resident."""
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import backend  # noqa: E402


def test_dockerfile_launches_through_the_warm_launcher():
    src = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    m = re.search(r'^CMD\s+(\[.*\])\s*$', src, re.M)
    assert m and '"python", "run_server.py"' in m.group(1)
    assert '"--server.fileWatcherType", "none"' in m.group(1), "the watcher stays off"


def test_launcher_starts_the_warm_thread_before_streamlit():
    src = (ROOT / "run_server.py").read_text(encoding="utf-8")
    assert src.index("backend.start_keep_warm(") < src.index("from streamlit.web import cli")
    assert 'sys.argv = ["streamlit", "run", "app.py"] + sys.argv[1:]' in src
    assert 'CAI_KEEP_WARM_SEC' in src and 'CAI_WARM_DELAY_SEC' in src


def test_warm_all_covers_what_the_first_session_pays_and_never_raises():
    src = (ROOT / "backend.py").read_text(encoding="utf-8")
    i = src.index("def warm_all()")
    body = src[i:src.index("def start_keep_warm", i)]
    for part in ("ensure_pdfs_ingested", "warm_index", "load_documents", "get_suggested_questions"):
        assert part in body, part
    # functional: a failing part is reported and the rest still runs
    calls = []
    saved = (backend.ensure_pdfs_ingested, backend.warm_index, backend.load_documents, backend.get_suggested_questions)
    try:
        backend.ensure_pdfs_ingested = lambda: calls.append("ingest")
        def boom():
            raise RuntimeError("no model here")
        backend.warm_index = boom
        backend.load_documents = lambda: calls.append("docs")
        backend.get_suggested_questions = lambda: calls.append("suggest")
        out = backend.warm_all()
    finally:
        (backend.ensure_pdfs_ingested, backend.warm_index, backend.load_documents, backend.get_suggested_questions) = saved
    assert calls == ["ingest", "docs", "suggest"]
    assert str(out["index"]).startswith("error: RuntimeError")
    assert isinstance(out["total_ms"], int)


def test_keep_warm_runs_once_per_process_and_reports_done():
    saved = backend.warm_all
    try:
        backend.warm_all = lambda: {"total_ms": 7}
        backend._warm_state.update(started=False, done=False, runs=0, last_ms=None, error=None)
        assert backend.start_keep_warm(interval_sec=0, delay_sec=0) is True
        assert backend.start_keep_warm(interval_sec=0, delay_sec=0) is False, "one thread per process"
        for _ in range(100):
            if backend._warm_state["done"]:
                break
            time.sleep(0.02)
        assert backend._warm_state["done"] and backend._warm_state["last_ms"] == 7
    finally:
        backend.warm_all = saved


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("PASS", name)
