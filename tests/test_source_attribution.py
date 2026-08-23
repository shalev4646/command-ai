# -*- coding: utf-8 -*-
"""Every document an answer draws on must be citable — including PDF-less ones.

The bug this pins (found 2026-08-24 by a measurement, not by a test): the
source list dropped any document whose PDF was not in pdf-ldf_law/. Three
documents qualified, and for those the app answered from the text and showed
NO source at all — the one thing it promises never to do. It was silent by
construction: no error, no gate, health 200.

The other half matters just as much: "a dead link is worse than no link" is
still true, so a document with no PDF must come back with an EMPTY
source_file. Every render path keys its PDF block off that field
(app.py: `if primary and primary.get("source_file")`), so empty means the
citation shows and no link is drawn.

    venv\\Scripts\\python.exe tests\\test_source_attribution.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import backend
backend.ensure_pdfs_ingested = lambda *a, **k: None   # never ingest (paid)

ROOT = Path(__file__).resolve().parents[1]
# no PDF anywhere: extracted from an HTML original, source_file names the page
PDFLESS = "CHOK-SHIPUT-1955"
# PDF exists, but in pdf-hka/ — the directory the old code never looked in
OTHER_DIR = "HKA-31-08-01"


def _chunk(doc_id, section="1", clause="1", text="טקסט הסעיף לצורך הבדיקה"):
    return {"doc_id": doc_id, "section": section, "clause": clause,
            "text": text, "title": ""}


def _docs():
    return {d["document_id"]: d for d in backend.load_documents() if d.get("document_id")}


def test_resolve_pdf_searches_every_source_directory():
    docs = _docs()
    assert backend.resolve_pdf(docs[OTHER_DIR]["source_file"]) is not None, \
        "the reserve-call handbook's PDF is in pdf-hka/ and must resolve"
    assert backend.resolve_pdf(docs[PDFLESS]["source_file"]) is None, \
        "an HTML-sourced document has no PDF and must resolve to None"
    assert backend.resolve_pdf(None) is None
    assert backend.resolve_pdf("") is None


def test_quarantined_duplicates_are_never_served():
    # _duplicates/ holds repeated downloads; citing one means citing the wrong
    # copy of the order (the 2026-08-17 incident)
    dups = list((ROOT / "pdf-ldf_law" / "_duplicates").glob("*.pdf"))
    if not dups:
        return
    assert backend.resolve_pdf(dups[0].name) is None, \
        f"resolve_pdf served a quarantined duplicate: {dups[0].name}"


def test_a_pdfless_document_is_still_cited():
    got = backend._sources_from_chunks([_chunk(PDFLESS)])
    assert len(got) == 1, "the document the answer quoted was dropped from sources"
    assert got[0]["doc_id"] == PDFLESS
    assert got[0]["title"], "a citation with no title is not a citation"
    # the dead-link guard: no PDF resolved, so no render path may draw a link
    assert got[0]["source_file"] == "", got[0]["source_file"]


def test_a_document_whose_pdf_lives_elsewhere_is_cited_with_its_link():
    got = backend._sources_from_chunks([_chunk(OTHER_DIR)])
    assert len(got) == 1
    assert got[0]["source_file"], "PDF is on disk in pdf-hka/, so it must be linkable"
    assert backend.resolve_pdf(got[0]["source_file"]) is not None


def test_ordinary_orders_are_unchanged():
    # the regression guard: whatever worked before must still carry its link
    docs = _docs()
    ordinary = next(d for d in docs.values()
                    if backend.resolve_pdf(d.get("source_file")) is not None
                    and d["document_id"] not in (PDFLESS, OTHER_DIR))
    got = backend._sources_from_chunks([_chunk(ordinary["document_id"])])
    assert len(got) == 1
    assert got[0]["source_file"] == ordinary["source_file"]
    assert got[0]["doc_id"] == ordinary["document_id"]


def test_unknown_document_ids_yield_no_citation():
    assert backend._sources_from_chunks([_chunk("NO-SUCH-DOC-9999")]) == []


def test_every_loaded_document_can_be_cited():
    # the sweep that would have caught this bug on the day it landed: no
    # document in the store may be uncitable, whether or not it has a PDF
    uncitable = []
    for doc_id in _docs():
        if not backend._sources_from_chunks([_chunk(doc_id)]):
            uncitable.append(doc_id)
    assert not uncitable, f"documents that answer but cannot be cited: {uncitable}"


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
    print("all source-attribution tests passed" if not failed else f"FAILED: {failed}")
    raise SystemExit(1 if failed else 0)
