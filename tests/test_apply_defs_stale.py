# -*- coding: utf-8 -*-
"""An add-mode def never loses a clause to the merge without a sign — no model, no API.

apply_defs merges a block-for-existing-order def into the order's existing block: live clauses first, and a
def clause whose heading is already there is skipped. So a review fix applied to a corpus that already holds an
earlier version of the def would keep the OLD text in silence — 3.0501's C5 fixes on the v163 corpus, caught by
hand in v164 (29.09). apply_defs now refuses such an order and names the clauses; the same text again (an
idempotent re-apply) and a replaced block pass.

    venv\\Scripts\\python.exe tests\\test_apply_defs_stale.py
"""
import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from night.recurate import apply_defs as A  # noqa: E402

LIVE = {"id": "key-facts", "title": "עיקרי הפקודה", "clauses": [{"number": "מי זכאי", "text": "נוסח ישן"}]}


def _section(text: str) -> dict:
    return {"id": "key-facts", "clauses": [{"number": "מי זכאי", "text": text}, {"number": "סעיף חדש", "text": "חדש"}]}


def test_a_heading_with_other_text_is_stale():
    assert A.stale_clauses([LIVE], _section("נוסח מתוקן")) == ["מי זכאי"]


def test_the_same_text_again_is_an_idempotent_reapply():
    assert A.stale_clauses([LIVE], _section("נוסח ישן")) == []


def test_a_replaced_block_is_not_consulted():
    assert A.stale_clauses([], _section("נוסח מתוקן")) == []


def test_another_section_id_is_not_consulted():
    other = dict(LIVE, id="key-facts-nodigits")
    assert A.stale_clauses([other], _section("נוסח מתוקן")) == []


def test_main_refuses_the_order_and_writes_nothing():
    doc = {"document_id": "X-1", "title": "פקודה", "raw_text": "מי זכאי נוסח ישן", "sections": [LIVE]}
    defn = {"document_id": "X-1", "mode": "block-for-existing-order", "clauses": _section("נוסח מתוקן")["clauses"]}
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        store = t / "doc.json"
        store.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        (t / "defs").mkdir()
        (t / "defs" / "X-1.json").write_text(json.dumps(defn, ensure_ascii=False), encoding="utf-8")
        saved = A.doc_path, sys.argv
        A.doc_path, sys.argv = (lambda did: store), ["apply_defs", str(t / "defs"), "--write"]
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                rc = A.main()
        finally:
            A.doc_path, sys.argv = saved
        assert rc == 1
        assert "REFUSED" in out.getvalue() and "STALE" in out.getvalue()
        assert json.loads(store.read_text(encoding="utf-8")) == doc


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
    print("all apply-defs stale-merge tests passed")
