# -*- coding: utf-8 -*-
"""What a conversation replays to the model (backend._replay_history) — no
model, no API.

Measured on production 2026-09-17: the third question of a conversation cost
$0.31 because the history it replayed carried the two earlier questions WITH
their retrieved excerpts (~11K tokens each since the blocks). Six exchanges
would replay 60K+ tokens — a $0.40 cache write after any five-minute gap.

The rule pinned here: older user turns are replayed as the bare question
(the excerpts are stripped at the _CONTEXT_HEADER the app composed them
with), the MOST RECENT exchange keeps its excerpts (a follow-up usually
points at them), assistant turns are untouched, and the whole-exchange trim
still applies. Bare turns never change between requests, so the cached
prefix over older history stays byte-stable.

    venv\\Scripts\\python.exe tests\\test_history_replay.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import backend

H = backend._CONTEXT_HEADER


def _user(q, ctx):
    return {"role": "user", "content": f"{q}\n\n{H}\n{ctx}"}


def _hist(n):
    out = []
    for i in range(1, n + 1):
        out.append(_user(f"שאלה {i}", f"[X | פקודה | סעיף {i}]\nקטע מפקודה מספר {i} " * 20))
        out.append({"role": "assistant", "content": f"תשובה {i}"})
    return out


def test_older_user_turns_are_replayed_bare():
    past = backend._replay_history(_hist(3))
    assert past[0]["content"] == "שאלה 1", past[0]
    assert past[2]["content"] == "שאלה 2", past[2]
    assert H not in past[0]["content"] and H not in past[2]["content"]


def test_the_most_recent_exchange_keeps_its_excerpts():
    past = backend._replay_history(_hist(3))
    assert past[4]["content"].startswith("שאלה 3\n\n" + H), past[4]["content"][:80]
    assert "קטע מפקודה מספר 3" in past[4]["content"]


def test_assistant_turns_are_untouched():
    past = backend._replay_history(_hist(3))
    assert [m["content"] for m in past if m["role"] == "assistant"] == ["תשובה 1", "תשובה 2", "תשובה 3"]


def test_a_single_exchange_is_replayed_whole():
    past = backend._replay_history(_hist(1))
    assert len(past) == 2 and H in past[0]["content"]


def test_the_whole_exchange_trim_still_applies():
    past = backend._replay_history(_hist(8))          # 16 messages > _HISTORY_MAX
    assert len(past) <= backend._HISTORY_MAX
    assert past[0]["role"] == "user", "history must open with a user turn"


def test_a_turn_without_excerpts_is_left_alone():
    hist = [{"role": "user", "content": "שאלה חופשית"}, {"role": "assistant", "content": "תשובה"},
            _user("שאלה 2", "קטע"), {"role": "assistant", "content": "תשובה 2"}]
    past = backend._replay_history(hist)
    assert past[0]["content"] == "שאלה חופשית"


def test_empty_history_stays_empty():
    assert backend._replay_history(None) == [] and backend._replay_history([]) == []


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
    print("all history-replay tests passed")
