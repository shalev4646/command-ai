# -*- coding: utf-8 -*-
"""RETRIEVE_SECOND_PASS_CONTINUE — the second pass as a cached continuation.

night/PLAN_COST.md, lever 1: today the retry resends the whole new window;
with the flag on it continues the first exchange (window-1 read from the
cache, the first answer, then only the chunks window-2 adds). No model, no
API: the Anthropic client is replaced by a recorder and retrieval by fixtures,
and the tests pin the exact request shape — OFF is byte-identical, ON sends
the continuation and only the new passages.

    venv\\Scripts\\python.exe tests\\test_second_pass_continue.py
"""
import os
import sys
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import backend
import scope_routes

Q = "מגיע לי חופשה כשקרוב משפחה מאושפז?"
A = {"doc_id": "35.0402", "title": "חופשות", "section": "key-facts", "clause": "ביטול חופשה",
     "text": "חופשות — עיקרי הפקודה\nסעיף ביטול חופשה: מפקד רשאי לבטל חופשה שאושרה.", "score": 0.6}
B = {"doc_id": "33.0352", "title": "מניעת חופשה", "section": "key-facts", "clause": "מי מוסמך",
     "text": "מניעת חופשה — עיקרי הפקודה\nסעיף מי מוסמך: קצין שיפוט מוסמך למנוע חופשה.", "score": 0.5}
C = {"doc_id": "35.0402", "title": "חופשות", "section": "key-facts", "clause": "אשפוז קרוב",
     "text": "חופשות — עיקרי הפקודה\nסעיף אשפוז קרוב: חייל זכאי לחופשה מיוחדת כשקרוב מדרגה ראשונה אושפז.",
     "score": 0.7}
GAPPED = ("**פסיקה:** לא נמצא.\n\n" + scope_routes.MARK_MISSING
          + " זכאות חייל לחופשה מיוחדת עקב אשפוז קרוב משפחה.")


class _Usage:
    input_tokens = 10
    output_tokens = 5
    cache_creation_input_tokens = 0
    cache_read_input_tokens = 0


class _Final:
    usage = _Usage()
    stop_reason = "end_turn"


class _Stream:
    text_stream = iter(["תשובה."])

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        return _Final()


class _Recorder:
    """backend.client stand-in: records the kwargs of every stream() call."""

    def __init__(self):
        self.calls = []
        self.messages = self

    def stream(self, **kw):
        self.calls.append(kw)
        return _Stream()


@contextmanager
def _harness(flag: int, second_extra: list[dict]):
    names = ("client", "prefetch_hypothetical", "prefetch_route", "_standalone_question", "route_for",
             "retrieve_for_role", "widen_context", "extend_with_lack_clauses", "_sources_from_chunks",
             "RETRIEVE_SECOND_PASS", "RETRIEVE_SECOND_PASS_CONTINUE", "RETRIEVE_HYDE")
    old = {n: getattr(backend, n) for n in names}
    rec = _Recorder()

    def retrieve(query, role, route=None, widen=True, expand_terms=True):
        return [dict(A), dict(B)] if query == Q else [dict(c) for c in second_extra]

    backend.client = rec
    backend.prefetch_hypothetical = lambda q: None
    backend.prefetch_route = lambda q, r: None
    backend._standalone_question = lambda q, h: q
    backend.route_for = lambda *a, **k: set()
    backend.retrieve_for_role = retrieve
    backend.widen_context = lambda chunks, *a, **k: chunks
    backend.extend_with_lack_clauses = lambda chunks, *a, **k: chunks
    backend._sources_from_chunks = lambda chunks: []
    backend.RETRIEVE_SECOND_PASS = 4
    backend.RETRIEVE_SECOND_PASS_CONTINUE = flag
    backend.RETRIEVE_HYDE = False
    try:
        yield rec
    finally:
        for n, v in old.items():
            setattr(backend, n, v)


def _call(**kw):
    gen, sources, sent, usage = backend.stream_ai_answer(Q, None, "soldier", **kw)
    "".join(gen)
    return sent


def test_it_ships_off():
    assert backend.RETRIEVE_SECOND_PASS_CONTINUE == int(os.environ.get("RETRIEVE_SECOND_PASS_CONTINUE", "0"))


def test_off_is_the_historical_request_shape():
    with _harness(0, [C]) as rec:
        sent1 = _call()
        assert rec.calls[-1]["messages"] == [{"role": "user", "content": sent1}], "a bare string user turn"
        assert isinstance(sent1, str) and C["text"] not in sent1
        sent2 = _call(first_answer=GAPPED, first_user_content=sent1)
        assert rec.calls[-1]["messages"] == [{"role": "user", "content": sent2}], "still one bare user turn"
        assert C["text"] in sent2 and backend._CONTINUE_HEADER not in sent2, "the whole new window, resent"


def test_on_the_first_pass_writes_a_breakpoint_and_the_retry_continues_the_exchange():
    with _harness(1, [C]) as rec:
        sent1 = _call()
        first = rec.calls[-1]["messages"]
        assert first == [{"role": "user", "content": [{"type": "text", "text": sent1,
                                                        "cache_control": {"type": "ephemeral"}}]}]
        sent2 = _call(first_answer=GAPPED, first_user_content=sent1)
        msgs = rec.calls[-1]["messages"]
        assert len(msgs) == 3, msgs
        assert msgs[0] == first[0], "window-1 verbatim, with its breakpoint — the cache read"
        assert msgs[1] == {"role": "assistant", "content": GAPPED}
        cont = msgs[2]["content"]
        assert msgs[2]["role"] == "user" and isinstance(cont, str)
        assert cont.startswith(backend._CONTINUE_HEADER) and cont.endswith(backend._CONTINUE_ASK)
        assert C["text"] in cont, "the new passage is sent"
        assert A["text"] not in cont and B["text"] not in cont, "what window-1 carried is not resent"
        assert sent2 == f"{sent1}\n\n{cont}", "history replays one user turn carrying both windows"


def test_on_but_nothing_new_falls_back_to_the_ordinary_second_pass():
    with _harness(1, []) as rec:
        sent1 = _call()
        sent2 = _call(first_answer=GAPPED, first_user_content=sent1)
        msgs = rec.calls[-1]["messages"]
        assert len(msgs) == 1 and msgs[0]["content"] == sent2
        assert backend._CONTINUE_HEADER not in sent2


def test_on_without_the_first_turn_is_the_ordinary_second_pass():
    """A stale app that never passed first_user_content: nothing to continue."""
    with _harness(1, [C]) as rec:
        sent2 = _call(first_answer=GAPPED)
        msgs = rec.calls[-1]["messages"]
        assert len(msgs) == 1 and C["text"] in sent2 and backend._CONTINUE_HEADER not in sent2


def test_a_clean_first_answer_never_continues():
    with _harness(1, [C]) as rec:
        sent1 = _call()
        _call(first_answer="**פסיקה:** זכאי בתנאים — שבעה ימים.", first_user_content=sent1)
        assert len(rec.calls[-1]["messages"]) == 1, "no gap declared, no continuation"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
    print("all second-pass-continue tests passed")
