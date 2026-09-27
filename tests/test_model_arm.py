# -*- coding: utf-8 -*-
"""The model arm (night/model_arm.py, night/MODEL_ARM_CRITERION.md) — no model, no API.

What it pins:
  * the first pass is the base's delivery with only the model changed: the
    request shape equals night.probe's, and every user turn is the recorded
    `sent_user_content`, verbatim, in ruler order;
  * a refusal of the arm's model (stop_reason "refusal", content empty or
    partial) is recorded on the row and comes back from grading as a failure
    — never dropped from the denominator the way night.grade drops an empty
    answer;
  * the kept answer is the app's choice (app.py, "The second search"),
    RETRIEVE_SECOND_PASS_KEEP_RULING included — on final161v2 that choice
    differs from what night/final_arm graded on exactly rs055 and rs058;
  * the criterion's counts.

    venv\\Scripts\\python.exe tests\\test_model_arm.py
"""
import sys
from pathlib import Path
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import backend  # noqa: E402  imported first: the arm module must not change this process's model
from night import model_arm as M  # noqa: E402
from night import probe  # noqa: E402


def _usage(i=0, o=0):
    return NS(input_tokens=i, output_tokens=o, cache_creation_input_tokens=0, cache_read_input_tokens=0)


def _msg(texts, stop, model="claude-opus-5", details=None, usage=None):
    return NS(content=[NS(type="text", text=t) for t in texts], model=model, stop_reason=stop,
              stop_details=details, usage=usage or _usage(100, 50))


def test_importing_the_arm_does_not_switch_this_process_model():
    assert backend.MODEL == "claude-opus-4-8"


def test_request_params_equal_probe_except_the_model():
    def fake_stream(q, history, role, profile, first_answer=None):
        return iter(()), [], "USER-CONTENT", {}
    real = backend.stream_ai_answer
    backend.stream_ai_answer = fake_stream
    try:
        reqs, meta = probe.build_requests([{"id": "x", "q": "שאלה", "role": "soldier"}])
    finally:
        backend.stream_ai_answer = real
    got = dict(reqs[0]["params"])
    assert got["model"] == backend.MODEL
    assert M.request_params("soldier", "USER-CONTENT", model=got["model"]) == got
    assert M.request_params("soldier", "USER-CONTENT")["model"] == "claude-opus-5"


def test_p1_requests_are_the_recorded_turns_in_ruler_order():
    reqs, meta = M.p1_requests()
    base = M._base_rows("_p1")
    qs = M.FA._questions(M.BASE_TAG)
    assert len(reqs) == len(qs) == 72
    for i, (req, m, q) in enumerate(zip(reqs, meta, qs)):
        assert req["custom_id"] == f"p{i}" and m["id"] == q["id"]
        p = req["params"]
        assert p["model"] == "claude-opus-5"
        assert p["messages"] == [{"role": "user", "content": base[q["id"]]["sent_user_content"]}]
        assert "answer" not in m and m["sent_user_content"] == base[q["id"]]["sent_user_content"]


def test_the_guard_refuses_a_backend_on_another_model():
    try:
        M._backend()
    except SystemExit:
        return
    raise AssertionError("_backend() accepted a backend answering with the base model")


def test_a_refusal_before_output_is_an_empty_failing_row():
    details = NS(model_dump=lambda: {"type": "refusal", "category": "cyber", "explanation": None})
    row, usd = M.answer_row({"id": "rs001"}, _msg([], "refusal", details=details, usage=_usage(0, 0)))
    assert row["answer"] == "" and row["refusal_stop"] and not row["truncated"]
    assert row["stop_reason"] == "refusal" and row["stop_details"]["category"] == "cyber"
    assert usd == 0.0                      # declined before output is not billed


def test_a_refusal_mid_stream_keeps_the_partial_text_and_the_flag():
    row, _ = M.answer_row({"id": "rs001"}, _msg(["**פסיקה:** מותר", " בתנאים"], "refusal"))
    assert row["answer"] == "**פסיקה:** מותר בתנאים" and row["refusal_stop"]


def test_an_ordinary_answer_and_a_truncated_one():
    row, usd = M.answer_row({"id": "rs002"}, _msg(["תשובה"], "end_turn", usage=_usage(1_000_000, 0)))
    assert row["answer"] == "תשובה" and not row["refusal_stop"] and not row["truncated"]
    assert abs(usd - 2.5) < 1e-9           # $5/MTok input, batch half
    row, _ = M.answer_row({"id": "rs002"}, _msg(["תשו"], "max_tokens"))
    assert row["truncated"] and not row["refusal_stop"]


def test_the_production_choice():
    ruling = "**פסיקה:** זכאי בתנאים — להשלמת שינה.\n\n**טרם במאגר:** שינה ביום."
    refusal = "המידע לא קיים בפקודות שסופקו.\n\n**טרם במאגר:** שינה ביום."
    keep = backend.RETRIEVE_SECOND_PASS_KEEP_RULING
    try:
        backend.RETRIEVE_SECOND_PASS_KEEP_RULING = 1
        assert M.production_choice(ruling, None) == "no_gap"
        assert M.production_choice(ruling, {"answer": None, "error": "errored"}) == "second_failed"
        assert M.production_choice(ruling, {"answer": "  \n"}) == "second_empty"
        assert M.production_choice(ruling, {"answer": refusal}) == "kept_ruling"
        assert M.production_choice(refusal, {"answer": ruling}) == "second"
        backend.RETRIEVE_SECOND_PASS_KEEP_RULING = 0
        assert M.production_choice(ruling, {"answer": refusal}) == "second"
    finally:
        backend.RETRIEVE_SECOND_PASS_KEEP_RULING = keep


def test_final161v2_graded_the_retry_where_production_kept_the_ruling():
    p1, p2 = M._base_rows("_p1"), M._base_rows("_p2")
    keep = backend.RETRIEVE_SECOND_PASS_KEEP_RULING
    try:
        backend.RETRIEVE_SECOND_PASS_KEEP_RULING = 1
        kept = sorted(i for i in p1 if M.production_choice(p1[i]["answer"], p2.get(i)) == "kept_ruling")
    finally:
        backend.RETRIEVE_SECOND_PASS_KEEP_RULING = keep
    assert kept == ["rs055", "rs058"], kept


def test_final_rows_take_the_answer_fields_of_the_kept_pass():
    first = {"a": {"id": "a", "answer": "**פסיקה:** מותר.", "sources": ["X"], "stop_reason": "end_turn"},
             "b": {"id": "b", "answer": "", "sources": ["Y"], "stop_reason": "refusal", "refusal_stop": True}}
    second = {"a": {"id": "a", "answer": "**פסיקה:** אסור.", "sources": ["Z"], "stop_reason": "end_turn",
                    "refusal_stop": False}}
    rows = {r["id"]: r for r in M.final_rows(first, second, ["a", "b"])}
    assert rows["a"]["kept"] == "second" and rows["a"]["answer"] == "**פסיקה:** אסור." and rows["a"]["sources"] == ["Z"]
    assert rows["a"]["first_answer"] == "**פסיקה:** מותר."
    assert rows["b"]["kept"] == "no_gap" and rows["b"]["answer"] == "" and rows["b"]["refusal_stop"]


def test_grading_writes_refusals_and_empty_answers_back_as_failures():
    parts = {"a": ["p1"], "b": ["p1", "p2"], "c": ["p1"], "d": ["p1"]}
    final = [{"id": "a", "answer": "תשובה"},
             {"id": "b", "answer": "", "stop_reason": "refusal", "refusal_stop": True},
             {"id": "c", "answer": "חלקי", "stop_reason": "refusal", "refusal_stop": True},
             {"id": "d", "answer": "תשובה"}]
    graded = [{"id": "a", "answer": "תשובה", "grade": {"level": "full", "answered_parts": 1}},
              {"id": "c", "answer": "חלקי", "grade": {"level": "full", "answered_parts": 1}}]
    out = {r["id"]: r for r in M.complete_grades(final, graded, parts)}
    assert set(out) == {"a", "b", "c", "d"}
    assert out["a"]["grade"]["level"] == "full"
    assert out["b"]["grade"]["level"] == "refused" and out["b"]["grade"]["unanswered_parts"] == 2
    assert out["c"]["grade"]["level"] == "refused" and out["c"]["grade_haiku"]["level"] == "full"
    assert out["d"]["grade"] is None       # the grader failed on it: shown as ungraded, read in stage 6


def test_the_criterion_counts():
    a48, n24 = M.row_sets()
    assert len(a48) == 48 and len(n24) == 24 and not set(a48) & set(n24)
    assert set(M.PROTECTED) <= set(a48) and len(set(M.PROTECTED)) == 35
    assert set(M.ANSWER_SIDE) <= set(a48)
    review = {i: {"verdict": "full"} for i in a48}
    review.update({i: {"verdict": "gap_ok"} for i in n24})
    assert M.judge(review, a48, n24)["passed"]
    review["rs055"] = {"verdict": "borderline"}
    review["rs001"] = {"verdict": "invented", "invented": True}
    review["rs007"] = {"verdict": "full", "confident_wrong": True}
    j = M.judge(review, a48, n24)
    assert j["drops"] == ["rs055"] and j["invented"] == ["rs001"] and j["confident_wrong"] == ["rs007"]
    assert len(j["full48"]) == 47 and not j["passed"]
    del review["rs000"]
    assert "rs000" in M.judge(review, a48, n24)["missing"]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
    print("all model-arm tests passed")
