# -*- coding: utf-8 -*-
"""metrics.estimate_cost prices a cache write by its TTL.

Why (19.09): SYSTEM_CACHE_TTL=1h bills the 5,244-token system prompt at 2x input
on a cold question, and the log priced every write at 1.25x — the 18.09
production question read $0.124 in the dashboard and cost $0.144. The 1-hour
share now rides in the usage dict; a dict without it prices exactly as before.

    venv\\Scripts\\python.exe tests\\test_cost_estimate.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import metrics

# the 18.09 production question, as the API billed it
PROD = {"input_tokens": 14077, "output_tokens": 806, "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 5316, "cache_creation_1h_input_tokens": 5316}


def test_a_one_hour_write_is_priced_at_twice_input():
    assert abs(metrics.estimate_cost(PROD) - 0.14370) < 1e-4, metrics.estimate_cost(PROD)


def test_a_usage_dict_without_the_breakdown_prices_as_it_always_did():
    legacy = {k: v for k, v in PROD.items() if k != "cache_creation_1h_input_tokens"}
    assert abs(metrics.estimate_cost(legacy) - 0.12376) < 1e-4, metrics.estimate_cost(legacy)


def test_a_mixed_write_prices_each_share_at_its_own_rate():
    # turn two of a chat after a cold hour: system prompt at 1h, history at 5m
    usage = {"input_tokens": 1000, "output_tokens": 0, "cache_read_input_tokens": 0,
             "cache_creation_input_tokens": 20000, "cache_creation_1h_input_tokens": 5000}
    want = (1000 * 5 + 15000 * 6.25 + 5000 * 10) / 1e6
    assert abs(metrics.estimate_cost(usage) - want) < 1e-6


def test_the_one_hour_share_can_never_exceed_the_write():
    usage = {"cache_creation_input_tokens": 100, "cache_creation_1h_input_tokens": 999}
    assert abs(metrics.estimate_cost(usage) - 100 * 10 / 1e6) < 1e-9


def test_no_usage_is_free():
    assert metrics.estimate_cost(None) == 0.0 and metrics.estimate_cost({}) == 0.0


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok {name}")
