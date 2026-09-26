# -*- coding: utf-8 -*-
"""ingestion.text_ingest: a document from prepared page text — same shape as the
PDF path, same sparse-text gate, no model call; the only write into the corpus
is gated by INGEST_FROM_TEXT (off by default) and never overwrites.

    venv\\Scripts\\python.exe tests\\test_text_ingest.py
"""
import json
import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from ingestion import pdf_to_json as P
from ingestion import text_ingest as T

FULL = "טקסט של עמוד פקודה מלא עם הרבה מילים בעברית. " * 40      # > 900 chars
SHORT = ("בלמ\"ס פקודות מטכ\"ל. מפרעת חיול - שירות חובה. 1. כל חייל בשירות חובה זכאי, עם חיולו, "
         "לקבל מפרעה על חשבון שכרו, בשיעור של 70 אחוז משכר טוראי בשירות חובה. 2. תשלום המפרעה יבוצע ביחידת הקליטה.")
OCR_FILE = "===== עמוד 1 =====\nמכתב לוואי\n\n===== עמוד 2 =====\n" + FULL + "\n\n===== עמוד 3 =====\n" + FULL + "\n"
DEF = {"document_id": "99.9999", "title": "פקודת בדיקה", "source_pdf": "x/test-order.pdf", "keep_pages": [2, 3],
       "roles": ["soldier"], "published": "בדיקה", "status_note": "בעדכון",
       "clauses": [{"number": "שאלה א", "text": "תשובה א."}, {"number": "שאלה ב", "text": "תשובה ב."}],
       "anchor_questions": ["שאלה אחת?", "שאלה שנייה?", "שאלה שלישית?"]}


@contextmanager
def _flags(from_text: bool, short: bool = False):
    old = (T.INGEST_FROM_TEXT, P.INGEST_SHORT_ORDERS)
    T.INGEST_FROM_TEXT, P.INGEST_SHORT_ORDERS = from_text, short
    try:
        yield
    finally:
        T.INGEST_FROM_TEXT, P.INGEST_SHORT_ORDERS = old


def test_ships_off():
    assert T.INGEST_FROM_TEXT == (os.environ.get("INGEST_FROM_TEXT", "0") == "1")


def test_pages_are_read_by_marker_and_keep_pages_cuts_the_cover_letter():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "x.ocr.txt"
        p.write_text(OCR_FILE, encoding="utf-8")
        assert len(T.read_ocr_pages(p)) == 3
        assert T.read_ocr_pages(p, [2, 3])[0].startswith("טקסט של עמוד")
        assert T.read_ocr_pages(p, [1])[0] == "מכתב לוואי"
        try:
            T.read_ocr_pages(p, [9])
            assert False, "a missing page must be an error"
        except ValueError:
            pass
        q = Path(d) / "plain.txt"
        q.write_text("בלי סימוני עמוד", encoding="utf-8")
        assert T.read_ocr_pages(q) == ["בלי סימוני עמוד"]


def test_the_document_has_the_corpus_shape_and_the_same_gate():
    doc = T.build_document([FULL, FULL], document_id="1.0001", title="כותרת", source_file="a.pdf",
                           roles=["soldier", "bogus"], sections=[{"id": "key-facts", "title": "t", "clauses": []}],
                           anchor_questions=["ש1?", "ש2?", "ש3?"])
    for k in ("document_id", "title", "published", "raw_text", "source_file", "suggested_questions", "roles",
              "sections", "anchor_questions"):
        assert k in doc, k
    assert doc["roles"] == ["soldier"], "unknown roles are dropped like the PDF path does"
    assert doc["suggested_questions"] == {"soldier": ["ש1?", "ש2?"]}
    assert doc["raw_text"] == FULL + "\n\n" + FULL
    assert doc["ingested_from_text"]["pages"] == 2
    # the sparse-text gate is the PDF path's own: a short page is rejected unless INGEST_SHORT_ORDERS
    with _flags(False, short=False):
        try:
            T.build_document([SHORT], document_id="2", title="t", source_file="b.pdf", roles=["soldier"])
            assert False, "sparse text must be rejected with the short-order rule off"
        except ValueError:
            pass
    with _flags(False, short=True):
        assert T.build_document([SHORT], document_id="2", title="t", source_file="b.pdf", roles=["soldier"])["raw_text"] == SHORT


def test_document_from_def_uses_keep_pages_and_the_clauses():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "x.ocr.txt"
        p.write_text(OCR_FILE, encoding="utf-8")
        doc = T.document_from_def(DEF, p)
        assert doc["document_id"] == "99.9999" and doc["source_file"] == "test-order.pdf"
        assert "מכתב לוואי" not in doc["raw_text"], "the cover letter page is cut"
        assert [c["number"] for c in doc["sections"][0]["clauses"]] == ["שאלה א", "שאלה ב"]
        assert doc["status_note"] == "בעדכון" and doc["published"] == "בדיקה"


def test_writes_need_the_flag_only_for_the_corpus_and_never_overwrite():
    doc = T.build_document([FULL], document_id="1.0001", title="כותרת בדיקה", source_file="a.pdf", roles=["soldier"])
    with tempfile.TemporaryDirectory() as d:
        with _flags(False):
            out = T.write_document(doc, Path(d))          # a dry-run folder needs no flag
            assert json.loads(out.read_text(encoding="utf-8"))["document_id"] == "1.0001"
            try:
                T.write_document(doc, T.JSON_STORE)
                assert False, "the corpus must be refused with the flag off"
            except PermissionError:
                pass
    assert not (T.JSON_STORE / f"{T.slug_for('כותרת בדיקה')}.json").exists()


def test_one_pdf_may_hold_two_instructions_but_one_instruction_is_written_once():
    """26.09: the FOI answer hka-32-02-10_and_32-02-27 carries two instructions, split
    by keep_pages; both keep the PDF's name as source_file. The duplicate guard must
    let the second in and still refuse the same instruction twice."""
    a = T.build_document([FULL], document_id="32-A", title="הוראה א", source_file="two.pdf", roles=["soldier"])
    b = T.build_document([FULL], document_id="32-B", title="הוראה ב", source_file="two.pdf", roles=["soldier"])
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "a.json").write_text(json.dumps(a, ensure_ascii=False), encoding="utf-8")
        assert T.conflicting_doc(Path(d), b) is None, "a second instruction from the same PDF is not a duplicate"
        assert T.conflicting_doc(Path(d), dict(a, title="כותרת אחרת"))["document_id"] == "32-A",             "the same instruction from the same PDF is a duplicate even under another title"


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as e:
                fails += 1
                print(f"FAIL {name}: {e}")
    raise SystemExit(1 if fails else 0)
