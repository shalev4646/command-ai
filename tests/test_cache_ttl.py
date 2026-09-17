# -*- coding: utf-8 -*-
"""The system prompt's prompt-cache TTL (SYSTEM_CACHE_TTL) — no model, no API.

Measured 17.09 on production: a two-pass question pays a 5,316-token cache
WRITE of the system prompt on its first pass ($0.03 of $0.25) whenever no
request shared the prefix in the previous five minutes. A one-hour entry
costs 2x to write and 0.1x to read, so it pays off exactly when questions
arrive 5-60 minutes apart (claude-api reference, "Choosing the TTL"); with
denser traffic both TTLs read, with sparser traffic the hour costs more. The
knob therefore ships OFF in code (5-minute default, byte-identical request)
and is turned on in fly.toml by the user's decision.

Two rules pinned here: the history breakpoint never carries a TTL (a longer
TTL must precede shorter ones, and history is the later block), and only the
two documented values are ever sent to the API.

    venv\\Scripts\\python.exe tests\\test_cache_ttl.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import backend


def _with(value):
    old = backend.SYSTEM_CACHE_TTL
    backend.SYSTEM_CACHE_TTL = value
    try:
        return backend._system_cache_control()
    finally:
        backend.SYSTEM_CACHE_TTL = old


def test_off_is_the_historical_five_minute_marker():
    assert _with("") == {"type": "ephemeral"}


def test_one_hour_is_sent_when_configured():
    assert _with("1h") == {"type": "ephemeral", "ttl": "1h"}


def test_five_minutes_may_be_named_explicitly():
    assert _with("5m") == {"type": "ephemeral", "ttl": "5m"}


def test_an_undocumented_value_falls_back_to_the_default():
    # the API would 400 on it, and a 400 here breaks every answer
    assert _with("2h") == {"type": "ephemeral"}
    assert _with("60") == {"type": "ephemeral"}


def test_the_history_breakpoint_never_carries_a_ttl():
    # the 1-hour entry must precede any 5-minute entry: system first, history
    # (5-minute) after it — so the history marker stays the bare default
    assert backend._history_cache_control() == {"type": "ephemeral"}


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
    print("all cache-ttl tests passed")
