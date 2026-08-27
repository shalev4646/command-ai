# -*- coding: utf-8 -*-
r"""A measurement must refuse to run on a degraded retrieval pipeline.

2026-08-27. The `FULL_BLOCKS=2` arm was composed and would have been submitted
while the API credit balance was exhausted. The log recorded, one hundred times:

    [backend] hypothetical failed: ... credit balance is too low
    [backend] document router failed: ... credit balance is too low
    [probe] composed 100/100
    [probe] probe-fb2: submitting 100 requests

Both failures are caught by design — production must answer even when a helper
call dies, and `_route_docs`' own docstring says "a flaky API call costs
relevance, never an answer". That is right for a soldier and catastrophic for a
measurement: the arm would have carried NO hypothetical and NO routing, and its
comparison against `heldout_post` would have attributed the whole difference to
FULL_BLOCKS. A number that looks clean and means nothing is worse than no
number, and this repo's own plan opens with the rule — "fallback שקט = שקר
במדידה: אם קריאות נופלות, לדווח ולבטל, לא להמשיך".

Only luck stopped it: `messages.batches.create` needs credit too, so the submit
itself failed and nothing was spent.

`RETRIEVE_STRICT=1` makes the two helpers RAISE instead of degrading. Production
never sets it; the night harness does, so a run on a broken pipeline dies at the
first question instead of composing a plausible lie.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import backend


class _Boom:
    """A client whose every call fails the way an exhausted balance does."""
    class messages:
        @staticmethod
        def create(**_):
            raise RuntimeError("credit balance is too low")

    def with_options(self, **_):
        return self


def _with_broken_client(fn):
    real, backend.client = backend.client, _Boom()
    try:
        return fn()
    finally:
        backend.client = real


def test_production_still_degrades_quietly():
    """A soldier's question must survive a dead helper call."""
    backend.RETRIEVE_STRICT = False
    assert _with_broken_client(lambda: backend._route_docs("שאלה כלשהי", "soldier")) == set()
    assert _with_broken_client(lambda: backend._hyde_call("שאלה כלשהי")) == ""


def test_strict_mode_refuses_to_pretend_the_router_ran():
    backend.RETRIEVE_STRICT = True
    try:
        _with_broken_client(lambda: backend._route_docs("שאלה כלשהי", "soldier"))
    except backend.RetrievalDegraded:
        pass
    else:
        raise AssertionError("router swallowed the failure under RETRIEVE_STRICT")
    finally:
        backend.RETRIEVE_STRICT = False


def test_strict_mode_refuses_to_pretend_the_hypothetical_ran():
    backend.RETRIEVE_STRICT = True
    try:
        _with_broken_client(lambda: backend._hyde_call("שאלה כלשהי"))
    except backend.RetrievalDegraded:
        pass
    else:
        raise AssertionError("hypothetical swallowed the failure under RETRIEVE_STRICT")
    finally:
        backend.RETRIEVE_STRICT = False


def test_strict_is_off_unless_asked():
    """Production must never inherit it by accident."""
    assert os.environ.get("RETRIEVE_STRICT", "0") != "1" or backend.RETRIEVE_STRICT


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
    print("all strict-retrieval tests passed")
