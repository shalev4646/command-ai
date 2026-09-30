# -*- coding: utf-8 -*-
"""The model arm's two parameters (night/model_arm.configure, --model / --base) — no model, no API.

Until 30.09 the arm's model and the finished run whose deliveries it re-sends were constants
(claude-opus-5 on final161v2). The next arm (an answer-opening flag in the system prompt — the manager,
30.09) runs on recorded deliveries too: on Opus 4.8 against final161v2, or on Opus 5 against opus5v2,
by the user's choice of model. What this pins:
  * the defaults are the opus5v2 arm, exactly — same model, same base, and the first pass they compose
    is the delivery probe_opus5v2_p1.jsonl recorded, byte for byte;
  * --model changes the model of the requests and nothing else;
  * --base re-sends another finished run's deliveries: opus5v2's are final161v2's, verbatim, and the
    answer-side fields an arm's rows carry (model, stop reason, usage) never ride along as delivery;
  * the command line: options anywhere, the positional arguments as before; an arm cannot take its
    base's tag.

    venv\\Scripts\\python.exe tests\\test_model_arm_params.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import backend  # noqa: E402,F401  imported first, as in test_model_arm: the arm must not switch this process's model
from night import config as C  # noqa: E402
from night import model_arm as M  # noqa: E402


def _restore():
    M.configure(model=M.DEFAULT_ARM_MODEL, base=M.DEFAULT_BASE_TAG)


def _turn(req) -> str:
    return req["params"]["messages"][0]["content"]


def test_the_defaults_are_the_opus5v2_arm():
    assert (M.ARM_MODEL, M.BASE_TAG) == ("claude-opus-5", "final161v2") == (M.DEFAULT_ARM_MODEL, M.DEFAULT_BASE_TAG)
    assert M.BASE_USAGE == C.OUT / "usage_final161v2.json"
    assert M.request_params("soldier", "x")["model"] == "claude-opus-5"


def test_the_default_first_pass_is_what_opus5v2_recorded():
    reqs, meta = M.p1_requests()
    rec = {r["id"]: r for r in C.read_jsonl(C.OUT / "probe_opus5v2_p1.jsonl")}
    assert len(reqs) == len(rec) == 72
    for req, m in zip(reqs, meta):
        r = rec[m["id"]]
        assert req["params"]["model"] == "claude-opus-5" == r["model"]
        assert _turn(req) == r["sent_user_content"] and m["role"] == r["role"]


def test_model_changes_only_the_model():
    first, _ = M.p1_requests()
    M.configure(model="claude-opus-4-8")
    try:
        assert M.ARM_MODEL == "claude-opus-4-8" and M.BASE_TAG == "final161v2"
        assert M.request_params("soldier", "x")["model"] == "claude-opus-4-8"
        reqs, _ = M.p1_requests()
        assert len(reqs) == len(first) == 72
        for a, b in zip(first, reqs):
            pa, pb = dict(a["params"]), dict(b["params"])
            assert (pa.pop("model"), pb.pop("model")) == ("claude-opus-5", "claude-opus-4-8")
            assert pa == pb and a["custom_id"] == b["custom_id"]
    finally:
        _restore()
    assert M.request_params("soldier", "x")["model"] == "claude-opus-5"


def test_base_resends_another_finished_run():
    first, first_meta = M.p1_requests()
    M.configure(base="opus5v2")
    try:
        assert M.BASE_TAG == "opus5v2" and M.ARM_MODEL == "claude-opus-5"
        assert M.BASE_USAGE == C.OUT / "usage_opus5v2.json"
        assert M._order() == [q["id"] for q in M.FA._questions("final161v2")]
        reqs, meta = M.p1_requests()
        # opus5v2 delivered final161v2's user turns verbatim, so an arm on either base sends the same 72
        assert [_turn(r) for r in reqs] == [_turn(r) for r in first]
        # and what the base's MODEL said or cost is not part of a delivery
        for m, m0 in zip(meta, first_meta):
            assert not (set(m) & set(M.ANSWER_SIDE_FIELDS)), set(m) & set(M.ANSWER_SIDE_FIELDS)
            assert m == m0
    finally:
        _restore()
    assert (M.ARM_MODEL, M.BASE_TAG) == ("claude-opus-5", "final161v2")


def test_the_command_line():
    assert M._parse(["m"]) == ("dry", "opus5v2", [], None, None, {})
    assert M._parse(["m", "p1", "t"]) == ("p1", "t", [], None, None, {})
    assert M._parse(["m", "collect", "t", "p2", "--model", "claude-opus-4-8"]) == ("collect", "t", ["p2"], "claude-opus-4-8", None, {})
    assert M._parse(["m", "--base=opus5v2", "dry", "t", "--model=claude-opus-5"]) == ("dry", "t", [], "claude-opus-5", "opus5v2", {})
    assert M._parse(["m", "p1", "t", "--flag", "A=1", "--flag=B=0"])[5] == {"A": "1", "B": "0"}
    assert M.unchanged_roles({"soldier": "x", "reserve": "y"}, {"soldier": "x", "reserve": "z"}, ["soldier", "reserve"]) == ["soldier"]
    try:
        M._parse(["m", "p1", "t", "--model"])
    except SystemExit:
        pass
    else:
        raise AssertionError("an option without a value must be refused")


def test_an_arm_cannot_take_its_bases_tag():
    M._check_tag("opus5v2")
    try:
        M._check_tag("final161v2")
    except SystemExit:
        pass
    else:
        raise AssertionError("an arm tagged as its own base must be refused")
    M.configure(base="opus5v2")
    try:
        M._check_tag("final161v2")
        try:
            M._check_tag("opus5v2")
        except SystemExit:
            pass
        else:
            raise AssertionError("an arm tagged as its own base must be refused")
    finally:
        _restore()


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
    print("all model-arm parameter tests passed")
