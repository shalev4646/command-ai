# -*- coding: utf-8 -*-
"""night.probe records the first pass it composes — the exact user turn and the
router's shortlist — so a later arm (RETRIEVE_SECOND_PASS_CONTINUE) can continue
that pass without paying for it again. Recording only: the request sent to the
model is byte-identical, and backend.route_for is restored even on failure.

    venv\\Scripts\\python.exe tests\\test_probe_records_first_pass.py
"""
import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import backend
from night import probe

TURN = "שאלה: כמה ימי חופשה?\n\nקטעים מהפקודות:\n[35.0402] ..."
ROUTE = {"35.0402", "33.0302"}


class _Params(dict):
    def __init__(self, **k):
        super().__init__(**k)


class _Request(dict):
    def __init__(self, **k):
        super().__init__(**k)


def _fake_stream(q, history, role, profile, first_answer=None):
    # production calls route_for once per question, by its module-global name
    got = backend.route_for(q, role, raw_question=q)
    assert got == ROUTE
    return iter(()), [{"doc_id": "35.0402"}], TURN, {}


def _run(stream):
    real_stream, real_route = backend.stream_ai_answer, backend.route_for
    backend.stream_ai_answer = stream
    backend.route_for = lambda *a, **k: set(ROUTE)
    try:
        reqs, meta = probe._build_requests([{"id": "x1", "q": "כמה ימי חופשה?", "role": "soldier"}],
                                           _Params, _Request)
        return reqs, meta, backend.route_for
    finally:
        backend.stream_ai_answer, backend.route_for = real_stream, real_route


def test_the_row_carries_the_exact_turn_and_the_route():
    reqs, meta, _ = _run(_fake_stream)
    assert meta[0]["sent_user_content"] == TURN
    assert meta[0]["route"] == sorted(ROUTE)
    assert meta[0]["context_words"] == len(TURN.split())


def test_the_request_is_the_recorded_turn_byte_for_byte():
    reqs, meta, _ = _run(_fake_stream)
    sent = reqs[0]["params"]["messages"][0]["content"]
    assert sent == meta[0]["sent_user_content"] == TURN
    assert reqs[0]["params"]["messages"] == [{"role": "user", "content": TURN}], "nothing else changed in the request"


def test_route_for_is_restored_even_when_composition_fails():
    def marker(*a, **k):           # a stub: the real router must never be called offline
        return set(ROUTE)

    def _boom(*a, **k):
        backend.route_for("q", "soldier")
        raise RuntimeError("composition failed")

    real_stream, real_route = backend.stream_ai_answer, backend.route_for
    backend.stream_ai_answer, backend.route_for = _boom, marker
    try:
        try:
            probe._build_requests([{"id": "x", "q": "q", "role": "soldier"}], _Params, _Request)
            assert False, "the failure must propagate"
        except RuntimeError:
            pass
        assert backend.route_for is marker, "the wrapper must not leak past the call"
    finally:
        backend.stream_ai_answer, backend.route_for = real_stream, real_route


def test_production_still_calls_route_for_by_name():
    """The recorder wraps backend.route_for; if stream_ai_answer stopped calling it
    by that name, the route column would silently go empty."""
    assert "route_for(" in inspect.getsource(backend.stream_ai_answer)


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as e:
                fails += 1
                print(f"FAIL {name}: {e}")
    raise SystemExit(1 if fails else 0)
