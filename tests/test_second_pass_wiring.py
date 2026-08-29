# -*- coding: utf-8 -*-
"""The second search must actually fire in the LIVE answer path.

backend.stream_ai_answer has carried the `first_answer` machinery since the
measurement (15→23 full answers, p=0.008; control arm 17 — commit 2a3051e),
but app.py never passed it, so production kept answering from the first
window only. These tests render the real app.py through AppTest with a fake
stream_ai_answer and assert the wiring end to end:

  - a gap-declaring first answer triggers exactly one more call, carrying the
    first answer as `first_answer`;
  - the KEPT message is the second answer (the one the measurement graded),
    with the second call's sources and api_content;
  - a clean first answer, or the flag being off, means one call only — the
    gate is what keeps the second call off successful answers;
  - a second call that dies keeps the complete first answer and shows no error.

    venv\\Scripts\\python.exe tests\\test_second_pass_wiring.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# app.py's _startup_ingest ingests every un-ingested PDF THROUGH THE PAID API;
# app.py binds the names at import, so patching the module attributes first is
# what keeps this test free. Same for persisting the embedding cache.
import backend
backend.ensure_pdfs_ingested = lambda *a, **k: None
backend.warm_index = lambda *a, **k: 0
import storage.vector_store as vs
vs._save_emb_cache = lambda: None

import metrics
metrics.reserve = lambda *a, **k: "ok"
metrics.refund = lambda *a, **k: None
metrics.log_question = lambda **kw: None      # never touch Sheets from a test

import scope_routes
from streamlit.testing.v1 import AppTest

GAPPED = ("**פסיקה:** לא נמצא\n\n"
          "בפקודות שסופקו אין מענה.\n\n"
          + scope_routes.MARK_MISSING
          + " זכאות חייל לחופשה מיוחדת עקב אשפוז קרוב משפחה.\n\n"
          "מה שנובע עבורך: לפנות למשקית ת\"ש.")
CLEAN = ("**פסיקה:** זכאי\n**מקור:** פ\"מ 35.0402\n\n"
         "חייל זכאי לחופשה מיוחדת של שבעה ימים.")
SECOND = ("**פסיקה:** זכאי\n**מקור:** פ\"מ 35.0402 — חופשות\n\n"
          "נמצא בחיפוש המורחב: החייל זכאי לחופשה מיוחדת עקב אשפוז.")

SRC1 = [{"doc_id": "33.0352", "title": "מניעת חופשה", "civil_source": False,
         "civil_label": "", "source_file": "", "clause": None, "highlight": "",
         "superseded": False, "superseded_note": ""}]
SRC2 = [{"doc_id": "35.0402", "title": "חופשות", "civil_source": False,
         "civil_label": "", "source_file": "", "clause": None, "highlight": "",
         "superseded": False, "superseded_note": ""}]


def _fake(first_text, second_text=SECOND, second_raises=False):
    """A stream_ai_answer stand-in honoring the real 4-tuple contract."""
    calls = []

    def fake(question, history=None, role="soldier", profile=None, first_answer=None):
        calls.append({"question": question, "first_answer": first_answer})
        if first_answer is None:
            text, sources, api = first_text, SRC1, "api-first"
        else:
            if second_raises:
                raise RuntimeError("second call died")
            text, sources, api = second_text, SRC2, "api-second"
        usage = {}

        def gen():
            yield text
            usage.update({"input_tokens": 10, "output_tokens": 20,
                          "truncated": False})
        return gen(), sources, api, usage

    return fake, calls


def _run(question="מגיע לי חופשה כשקרוב משפחה מאושפז?"):
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=120)
    at.session_state["role"] = "soldier"
    at.session_state["conversation_history"] = []
    at.session_state["messages"] = []
    at.session_state["pending_question"] = question
    at.run()
    return at


def _last_assistant(at):
    msgs = [m for m in at.session_state["messages"] if m["role"] == "assistant"]
    assert msgs, "no assistant message was stored"
    return msgs[-1]


def test_a_gap_triggers_the_second_search_and_its_answer_is_kept():
    fake, calls = _fake(GAPPED)
    backend.stream_ai_answer = fake
    backend.RETRIEVE_SECOND_PASS = 4
    at = _run()
    assert len(calls) == 2, f"expected first+second call, got {len(calls)}"
    assert calls[0]["first_answer"] is None
    assert calls[1]["first_answer"] == GAPPED, "the second call must carry the first answer verbatim"
    msg = _last_assistant(at)
    assert msg["content"] == SECOND, "the kept message must be the SECOND answer — that is what the measurement graded"
    assert msg["sources"] == SRC2, "sources must match the answer they produced"
    user = [m for m in at.session_state["messages"] if m["role"] == "user"][-1]
    assert user.get("api_content") == "api-second", (
        "history must replay the second call's sent content — the kept answer was produced from it")


def test_a_clean_answer_is_never_researched():
    """The gate. A successful answer must never buy a second Opus call."""
    fake, calls = _fake(CLEAN)
    backend.stream_ai_answer = fake
    backend.RETRIEVE_SECOND_PASS = 4
    at = _run()
    assert len(calls) == 1, f"a clean answer bought {len(calls)} calls"
    assert _last_assistant(at)["content"] == CLEAN


def test_the_flag_off_means_one_call_even_on_a_gap():
    """Production before the env lands, and the rollback path."""
    fake, calls = _fake(GAPPED)
    backend.stream_ai_answer = fake
    backend.RETRIEVE_SECOND_PASS = 0
    at = _run()
    assert len(calls) == 1, f"flag off but {len(calls)} calls were made"
    assert _last_assistant(at)["content"] == GAPPED


def test_a_dead_second_call_keeps_the_first_answer():
    """The second pass is opportunistic: the first answer is complete, paid
    for, and on screen — an API failure on the retry must not replace it
    with an error notice."""
    fake, calls = _fake(GAPPED, second_raises=True)
    backend.stream_ai_answer = fake
    backend.RETRIEVE_SECOND_PASS = 4
    at = _run()
    assert len(calls) == 2
    msg = _last_assistant(at)
    assert msg["content"] == GAPPED, "the first answer must survive a dead second call"
    assert not msg.get("error"), "a kept answer must not be flagged as an error"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
    print("all second-pass wiring tests passed")
