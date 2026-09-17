# -*- coding: utf-8 -*-
"""The two prompt flags of סבב 4 (night/PLAN_ROUND4.md, שלבים 1.3 and 3.3),
both OFF: ANSWER_V2 caps what a refusal may say after its first sentence, and
SCOPE_UNIT_ROUTE lists „פקודות הקבע של היחידה" among rule 2א's frameworks.
OFF must be the historical prompt byte for byte; ON is what the 1.c arm runs.

    venv\\Scripts\\python.exe tests\\test_answer_v2.py
"""
import importlib
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import backend
import scope_routes

V1 = "מותר להוסיף אחריה מה כן קיים בקטעים (כלל שחל רק על הקשר אחר או צר יותר), תוך ציון מפורש שההקשר שונה."


def test_off_is_the_historical_rule_two():
    assert not backend.ANSWER_V2 and not scope_routes.SCOPE_UNIT_ROUTE, "both ship off"
    assert V1 in backend._COMMON_RULES
    assert backend._REFUSAL_FOLLOWUP_V2 not in backend._COMMON_RULES
    assert "{REFUSAL_FOLLOWUP}" not in backend._COMMON_RULES
    for p in backend.SYSTEM_PROMPTS.values():
        assert V1 in p and "{REFUSAL_FOLLOWUP}" not in p


def test_the_v2_follow_up_caps_the_refusal_to_one_same_topic_sentence():
    v2 = backend.refusal_followup(True)
    assert v2 != V1 and "{" not in v2
    assert "משפט אחד" in v2 and "באותו נושא" in v2
    assert backend.refusal_followup(False) == V1


def test_the_unit_route_exists_only_behind_the_flag():
    off = scope_routes.build_routes(False)
    on = scope_routes.build_routes(True)
    assert "unit_standing_orders" not in off
    assert set(on) - set(off) == {"unit_standing_orders"}
    keys = list(on)
    assert keys.index("unit_standing_orders") == keys.index("no_source") - 1, keys
    assert scope_routes.ROUTES == scope_routes.build_routes(scope_routes.SCOPE_UNIT_ROUTE)
    r = on["unit_standing_orders"]
    assert r["label"] == "פקודות הקבע של היחידה"
    for cite in ("33.0401", "61.0104", "21.0113"):
        assert cite in r["basis"], cite


def test_the_prompt_block_shows_the_unit_route_without_its_key():
    on = scope_routes.build_routes(True)
    block = scope_routes.prompt_block(on)
    assert "פקודות הקבע של היחידה" in block and "unit_standing_orders" not in block
    assert scope_routes.prompt_block() == scope_routes.prompt_block(scope_routes.ROUTES)
    assert "פקודות הקבע של היחידה —" not in scope_routes.prompt_block(), "off = no unit line"


def test_the_flags_reach_the_personas_when_set():
    """Reload backend with both flags on, then restore — the personas are
    built at import time, so this is the only honest end-to-end check."""
    env = {k: os.environ.get(k) for k in ("ANSWER_V2", "SCOPE_UNIT_ROUTE")}
    try:
        os.environ["ANSWER_V2"] = "1"
        os.environ["SCOPE_UNIT_ROUTE"] = "1"
        importlib.reload(scope_routes)
        importlib.reload(backend)
        for p in backend.SYSTEM_PROMPTS.values():
            assert backend._REFUSAL_FOLLOWUP_V2 in p and V1 not in p
            assert "פקודות הקבע של היחידה —" in p
            assert scope_routes.MARK_OUT_OF_SCOPE in p and "{REFUSAL_FOLLOWUP}" not in p
    finally:
        for k, v in env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        importlib.reload(scope_routes)
        importlib.reload(backend)
    assert V1 in backend._COMMON_RULES and "unit_standing_orders" not in scope_routes.ROUTES


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
    print("all answer-v2 tests passed")
