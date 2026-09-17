# -*- coding: utf-8 -*-
"""The answering model as a measurement knob (ANSWER_MODEL) and the ledger's
knowledge of Sonnet 5's price — no model, no API.

Why (17.09): the measured price of a question is $0.20-0.25 and 75% of it is
input; the only lever that changes the order of magnitude is the model
($2/$10 per MTok against Opus's $5/$25). Nobody has measured what Sonnet 5
loses on the frozen ruler, so it runs as a paired arm first. That needs the
model to be selectable per process WITHOUT touching production: the default
stays Opus 4.8 byte for byte, and the ledger must price the arm right (an
unknown model prices as Opus — the safe side — which would overstate a
Sonnet arm 2.5x and refuse it for lack of ceiling).

    venv\\Scripts\\python.exe tests\\test_answer_model.py
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import backend
from night.ledger import PRICES, cost_usd


def test_the_default_model_is_opus_4_8():
    assert backend.MODEL == "claude-opus-4-8"


def test_answer_model_env_selects_the_model_for_the_process():
    env = {**os.environ, "ANSWER_MODEL": "claude-sonnet-5", "ANTHROPIC_API_KEY": "",
           "PYTHONIOENCODING": "utf-8"}
    out = subprocess.run([sys.executable, "-c", "import backend; print(backend.MODEL)"],
                         cwd=ROOT, env=env, capture_output=True, text=True, timeout=180)
    assert out.stdout.strip().splitlines()[-1] == "claude-sonnet-5", out.stdout[-300:] + out.stderr[-300:]


def test_the_ledger_prices_sonnet_5():
    assert PRICES["claude-sonnet-5"] == (2.00, 10.00)
    assert abs(cost_usd("claude-sonnet-5", input_tokens=1_000_000, output_tokens=0) - 2.00) < 1e-9
    assert abs(cost_usd("claude-sonnet-5", input_tokens=0, output_tokens=1_000_000) - 10.00) < 1e-9
    # batch halves it, as for every model
    assert abs(cost_usd("claude-sonnet-5", input_tokens=1_000_000, output_tokens=0, batch=True) - 1.00) < 1e-9


def test_an_unknown_model_still_prices_as_opus():
    assert abs(cost_usd("claude-never-heard", input_tokens=1_000_000, output_tokens=0) - 5.00) < 1e-9


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
    print("all answer-model tests passed")
