# -*- coding: utf-8 -*-
"""RETRIEVE_RESERVE_CUE (backend._scope_role, night/stage2/CRITERION.md item ה): a soldier
whose question names reserve service is scoped to the soldier's orders plus the reserve-tagged
ones. Off — the default in code — every scope is the role itself, byte-identical.

    venv\\Scripts\\python.exe tests\\test_reserve_cue.py
"""
import os
import sys
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import backend

CUED = ("משחררים אותי מהמילואים, מגיע לי משהו?",
        "אני בסדיר ואחרי השחרור יקראו לי למילואים?",
        "מה קורה למילואימניק שלא מגיע?",
        'חייל בשמ"פ נפצע, מה עושים?',
        "קיבלתי צו 8, אני חייב להתייצב?",
        "הקפיצו אותי בצו שמונה")
PLAIN = ("כמה ימי חופשה מגיעים לי בשנה?",
         "צריך למלא טופס מילוי מקום?",
         "מה זה צו הצבה?")


@contextmanager
def _flag(on: bool):
    old = backend.RETRIEVE_RESERVE_CUE
    backend.RETRIEVE_RESERVE_CUE = on
    try:
        yield
    finally:
        backend.RETRIEVE_RESERVE_CUE = old


def test_off_by_default_in_code():
    if os.environ.get("RETRIEVE_RESERVE_CUE") is None:
        assert backend.RETRIEVE_RESERVE_CUE is False


def test_off_every_scope_is_the_role_itself():
    with _flag(False):
        for role in ("soldier", "reserve", "commander", None):
            for q in CUED + PLAIN:
                assert backend._scope_role(role, q) is role, (role, q)


def test_on_only_a_soldier_with_a_reserve_word_widens():
    with _flag(True):
        for q in CUED:
            assert backend._scope_role("soldier", q) == backend.SCOPE_SOLDIER_RESERVE, q
        for q in PLAIN:
            assert backend._scope_role("soldier", q) == "soldier", q
        for role in ("reserve", "commander", None):
            assert backend._scope_role(role, CUED[0]) is role
        # idempotent: stream_ai_answer passes the token down, the helpers resolve again
        tok = backend.SCOPE_SOLDIER_RESERVE
        assert backend._scope_role(tok, CUED[0]) == tok and backend._scope_role(tok, PLAIN[0]) == tok


def test_the_widened_scope_is_soldier_plus_reserve_never_commander_only():
    ids = lambda role: {d["document_id"] for d in backend._docs_for_role(role) if d.get("document_id")}
    both = ids(backend.SCOPE_SOLDIER_RESERVE)
    assert both == ids("soldier") | ids("reserve")
    assert "35.0206" in both and "35.0206" not in ids("soldier")      # rs007's order
    assert not (ids("commander") - ids("soldier") - ids("reserve")) & both


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
    print("all reserve-cue tests passed")
