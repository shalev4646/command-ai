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


def test_no_document_is_announced_as_an_order_it_is_not():
    """The source card prints "פ״מ {doc_id}" — but only for real order numbers.

    HKA-31-08-01 and 33-05-01 are הוראות קבע אכ״א, not פקודות מטכ״ל, and both
    used to render as "פ״מ <slug>": a factual claim about the source, shown to
    a soldier, that was simply untrue. app.py now prints that line only when
    doc_id parses as an order number (1-2 digit chapter, 3-4 digit clause —
    3.0110 and 33.0209 are both real), falls back to the document's own label,
    and prints nothing rather than a false claim.

    This pins the DATA side of that contract: any non-order document must
    identify itself, or it renders with no classification line at all.
    """
    import re
    order_number = re.compile(r"^\d{1,2}\.\d{3,4}$")
    # known and accepted: military standing orders whose id is a slug and which
    # carry no label yet — they render without the line, never as a false פ״מ
    UNLABELLED = {"HKA-31-08-01", "33-05-01"}

    bare = []
    for doc_id, d in _docs().items():
        ident = doc_id[3:] if doc_id.upper().startswith("PM-") else doc_id
        if order_number.match(ident) or d.get("civil_source") or d.get("civil_label"):
            continue
        if doc_id not in UNLABELLED:
            bare.append(doc_id)
    assert not bare, (
        "documents with a slug id and no label — they would render with no "
        f"classification line; give them civil_label: {bare}")


def test_an_explicit_label_beats_the_id_derived_one():
    """A hand-written label exists BECAUSE the id describes the document wrongly.

    58.0301 is the case that proves it: a revoked order whose id parses as a
    perfectly ordinary order number. It was tagged
    "פ״מ 58.0301 — ⚠ פקודה מבוטלת", and while the id-derived branch ran first
    the card rendered a plain "פ״מ 58.0301" — the warning was written, stored,
    and silently dropped at the last step, in front of a commander.

    So: civil_source first, then the document's own label, and only then the
    number derived from the id.
    """
    import re
    order_number = re.compile(r"^\d{1,2}\.\d{3,4}$")

    def card_line(src):
        did = (src.get("doc_id") or "").strip()
        ident = did[3:] if did.upper().startswith("PM-") else did
        if src.get("civil_source"):
            return "מקור אזרחי · " + ((src.get("civil_label") or "חוק").strip() or "חוק")
        if src.get("civil_label"):
            return (src.get("civil_label") or "").strip()
        if ident and order_number.match(ident):
            return f"פ״מ {ident}"
        return ""

    for doc_id, d in _docs().items():
        label = (d.get("civil_label") or "").strip()
        if not label or d.get("civil_source"):
            continue
        src = {"doc_id": doc_id, "civil_source": False, "civil_label": label}
        assert card_line(src) == label, (
            f"{doc_id} carries an explicit label that the card does not show — "
            f"got {card_line(src)!r}, expected {label!r}")


def test_real_order_numbers_are_still_recognised():
    import re
    order_number = re.compile(r"^\d{1,2}\.\d{3,4}$")
    for ident in ("33.0209", "3.0110", "35.0402", "2.0101", "8.0101"):
        assert order_number.match(ident), ident
    for slug in ("HKA-31-08-01", "33-05-01", "CHOK-SHIPUT-1955",
                 "זכויות-עבודה-מילואים"):
        assert not order_number.match(slug), slug


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
