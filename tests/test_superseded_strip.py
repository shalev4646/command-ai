# -*- coding: utf-8 -*-
"""The revoked-order warning has to reach the SCREEN, not just the data.

This test exists because the same warning already failed twice on the way out:
once when the source card preferred the id-derived "פ״מ 58.0301" over the
label that carried it, and once when it turned out the card only opens behind
a "הצג סעיף מקור" click, so a commander reading the answer never saw it. Both
times the data was correct and the screen was not — which is exactly what a
data-level assertion cannot catch.

So this one renders the real app.py through Streamlit's AppTest with a seeded
answer and asserts on the warning a person would actually read.

    venv\\Scripts\\python.exe tests\\test_superseded_strip.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# app.py's _startup_ingest ingests every un-ingested PDF THROUGH THE PAID API;
# app.py binds the name at import, so patching the module attribute first is
# what keeps this test free. Same for persisting the embedding cache, which is
# shared with whatever else is running against this checkout.
import backend
backend.ensure_pdfs_ingested = lambda *a, **k: None
backend.warm_index = lambda *a, **k: 0
import storage.vector_store as vs
vs._save_emb_cache = lambda: None

import metrics
metrics.log_question = lambda **kw: None      # never touch Sheets from a test

from streamlit.testing.v1 import AppTest

ANSWER = ("**פסיקה:** מותר בתנאים\n"
          "**מקור:** פ\"מ 58.0301 — הובלת מטען חורג\n\n"
          "מטען החורג מרוחב הרכב טעון אישור בכתב.")


def _run(sources):
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=120)
    at.session_state["role"] = "commander"
    at.session_state["conversation_history"] = []
    at.session_state["messages"] = [
        {"role": "user", "content": "מה הכללים להובלת מטען חורג"},
        {"role": "assistant", "content": ANSWER, "sources": sources},
    ]
    at.run()
    return at


def _source(**over):
    base = {"doc_id": "58.0301", "title": "הובלת מטען חורג", "civil_source": False,
            "civil_label": "", "source_file": "", "clause": None, "highlight": "",
            "superseded": False, "superseded_note": ""}
    base.update(over)
    return base


def _warnings(at):
    return " | ".join(w.value for w in at.warning)


def test_a_revoked_source_puts_a_warning_in_the_answer():
    at = _run([_source(superseded=True,
                       superseded_note="יש לוודא מול היחידה מה מסדיר את הנושא כיום")])
    text = _warnings(at)
    assert "בוטלה" in text, f"no revocation warning rendered. warnings={text!r}"
    assert "58.0301" in text, f"the warning does not name the order: {text!r}"
    assert "יש לוודא מול היחידה" in text, f"the note was dropped: {text!r}"


def test_the_warning_names_the_order_without_a_click():
    # the whole point: it is in the answer, not behind "הצג סעיף מקור"
    at = _run([_source(superseded=True)])
    assert "בוטלה" in _warnings(at)
    # and a default sentence stands in when the corpus gives no note
    assert "אינם בתוקף" in _warnings(at)


def test_an_order_in_force_gets_no_warning():
    at = _run([_source(superseded=False)])
    assert "בוטל" not in _warnings(at), _warnings(at)


def test_no_sources_at_all_is_safe():
    at = _run([])
    assert "בוטל" not in _warnings(at)
    assert not at.exception


def test_several_revoked_orders_read_as_plural():
    at = _run([_source(superseded=True),
               _source(doc_id="58.0302", title="הובלה אחרת", superseded=True)])
    text = _warnings(at)
    assert "בוטלו" in text, text
    assert "58.0301" in text and "58.0302" in text, text


if __name__ == "__main__":
    failed = []
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("ok", name)
            except AssertionError as e:
                failed.append(name)
                print("FAIL", name, "->", e)
    print("all superseded-strip tests passed" if not failed else f"FAILED: {failed}")
    raise SystemExit(1 if failed else 0)
