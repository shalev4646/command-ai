# -*- coding: utf-8 -*-
"""Ingest from prepared page text instead of a PDF's text layer.

Two kinds of source cannot go through `pdf_to_json.ingest`: scanned orders whose
text layer is empty or partial (the FOI responses of 16.09 — Tesseract text in
sources_pending/ocr/, night/ocr.py), and short orders whose text layer carries
scrambled digits while the OCR reads them right (26.09: form 2234 is "3322" in
the layer of 21.0104, "70 אחוז" is "17 אחוז" in 35.0204 — night/wave8/README.md).
This module builds the SAME document shape the PDF path writes, from page texts
that were already read, so everything downstream (load_documents, the curated
blocks, the vector index, the gates) sees no difference.

    doc = build_document(pages, document_id=..., title=..., source_file=..., roles=[...],
                         sections=[...], anchor_questions=[...])
    write_document(doc, out_dir)          # explicit folder; the corpus needs the flag

No model call happens here: the id, title, roles and the curated block come from a
definition written by hand (night/wave8/defs, night/recurate/defs) — the paid
analysis of `pdf_to_json` (metadata + suggested questions) is not repeated, and
`suggested_questions` is derived from the definition's anchor questions.

OFF by default: `INGEST_FROM_TEXT` gates the only write that reaches the corpus
(`write_document` into storage/json_store). Nothing in the app's boot path calls
this module, so with the flag off the running system is byte-identical; a dry run
writes into any other folder without the flag.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
from pathlib import Path

from ingestion import pdf_to_json as P

INGEST_FROM_TEXT = os.environ.get("INGEST_FROM_TEXT", "0") == "1"
ROOT = Path(__file__).resolve().parents[1]
JSON_STORE = ROOT / "storage" / "json_store"
_PAGE_MARK = re.compile(r"^===== עמוד (\d+) =====\s*$", re.M)


def read_ocr_pages(path: Path, keep_pages: list[int] | None = None) -> list[str]:
    """The page texts of a night/ocr.py output file, in page order, optionally
    only the listed pages (1-based). A file without page markers is one page."""
    text = Path(path).read_text(encoding="utf-8")
    parts = _PAGE_MARK.split(text)
    if len(parts) < 3:
        return [text]
    pages: dict[int, str] = {}
    for i in range(1, len(parts) - 1, 2):
        pages[int(parts[i])] = parts[i + 1].strip("\n")
    order = sorted(pages)
    if keep_pages:
        missing = [n for n in keep_pages if n not in pages]
        if missing:
            raise ValueError(f"pages {missing} are not in {Path(path).name}")
        order = list(keep_pages)
    return [pages[n] for n in order]


def build_document(pages: list[str], *, document_id: str, title: str, source_file: str,
                   roles: list[str], sections: list[dict] | None = None,
                   anchor_questions: list[str] | None = None, published: str = "",
                   status_note: str = "", text_source: str = "") -> dict:
    """The document dict the corpus stores, from page texts. Runs the same
    sparse-text gate as the PDF path (so INGEST_SHORT_ORDERS applies here too)."""
    text = P.gate_text(list(pages))
    if not text.strip():
        raise ValueError(f"no text for {document_id}")
    roles = [r for r in roles if r in P._VALID_ROLES] or list(P._VALID_ROLES)
    anchors = [q.strip() for q in (anchor_questions or []) if q and q.strip()]
    doc: dict = {
        "document_id": document_id,
        "title": title,
        "published": published,
        "raw_text": text,
        "source_file": source_file,
        "suggested_questions": {r: anchors[:2] for r in roles} if anchors else {},
        "roles": roles,
        "sections": [dict(s) for s in (sections or [])],
        "anchor_questions": anchors,
        "ingested_from_text": {"date": _dt.date.today().isoformat(), "source": text_source,
                              "pages": len(pages), "chars": len(text)},
    }
    if status_note:
        doc["status_note"] = status_note
    return doc


def document_from_def(defn: dict, text_path: Path) -> dict:
    """A document from a night/wave8-style definition plus its OCR text file:
    keep_pages (or pages_read) selects the pages, the clauses become the block."""
    pages = read_ocr_pages(text_path, defn.get("keep_pages") or defn.get("pages_read"))
    section = {"id": defn.get("section_id") or "key-facts", "title": defn.get("section_title") or "עיקרי הפקודה",
               "clauses": [{"number": c["number"], "text": c["text"]} for c in defn["clauses"]]}
    return build_document(
        pages, document_id=defn["document_id"], title=defn["title"],
        source_file=Path(defn.get("source_pdf") or text_path).name, roles=defn.get("roles") or [],
        sections=[section], anchor_questions=defn.get("anchor_questions"),
        published=defn.get("published") or "", status_note=defn.get("status_note") or "",
        text_source=str(text_path))


def slug_for(title: str) -> str:
    return re.sub(r"[^\w֐-׿-]", "-", title)[:60]


def write_document(doc: dict, out_dir: Path) -> Path:
    """Write the document JSON. Writing into the corpus (storage/json_store)
    requires INGEST_FROM_TEXT=1 and never overwrites an existing document;
    any other folder is a dry run and needs no flag."""
    out_dir = Path(out_dir)
    into_corpus = out_dir.resolve() == JSON_STORE.resolve()
    if into_corpus and not INGEST_FROM_TEXT:
        raise PermissionError("INGEST_FROM_TEXT is off — refusing to write into storage/json_store")
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{slug_for(doc['title'])}.json"
    if into_corpus and (out.exists() or P._existing_doc_for(out_dir, doc["source_file"])):
        raise FileExistsError(f"{out.name} (or a document for {doc['source_file']}) already exists — "
                              "text ingest never overwrites; remove it first on purpose")
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    return out
