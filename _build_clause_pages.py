"""Build storage/clause_pages.json — {doc_id: {clause_key: 1-based PDF page}}.

Rerun this after reingesting (and commit the JSON): the mapping is derived
from the current PDFs + stored raw_text, and goes stale with either. It runs
offline (no API calls) but needs PyMuPDF, which the Streamlit Cloud runtime
must not depend on — the app only READS the JSON (backend.page_for_clause).

Two mapping kinds, keyed by backend.clause_key so runtime lookups can't drift:

* Raw-text windows ("w<n>") — exact arithmetic, no detection: raw_text is
  the "\\n\\n" join of the PDF's non-empty pages (ingestion.extract_text) and
  window n starts at word n*(_CHUNK_WORDS-_OVERLAP_WORDS) (vector_store).
  Emitted only when the stored raw_text still word-matches the PDF — a
  hand-recovered raw_text (scanned orders) makes word offsets meaningless.

* Structured key-facts clauses ("<section>:<clause>") — the clause number
  (or the "(סעיף N)" reference inside a descriptive clause title) is located
  as a numbered heading in the page text. Hebrew orders render headings as
  "N." — RTL extraction yields ".N"/"N." on its own line or a bare "N" line
  followed by a lone "." line; a bare number with no dot is rejected (tables
  of contents and amounts inside tables extract exactly like that). Clause
  numbers ascend through an order's body, so candidate pages are assigned
  monotonically. Annex sections are skipped: their row numbers are table
  positions, not order clauses, and annex headings don't survive extraction
  reliably — those chunks fall back to a page-less PDF link.
"""
import json
import re
import sys
from pathlib import Path

import fitz  # PyMuPDF

sys.path.insert(0, str(Path(__file__).parent))
from backend import clause_key, load_documents
from common import safe_print
from storage.vector_store import _CHUNK_WORDS, _OVERLAP_WORDS

ROOT = Path(__file__).parent
PDF_DIR = ROOT / "pdf-ldf_law"
OUT_PATH = ROOT / "storage" / "clause_pages.json"

# same set _verdict_chip tolerates in app.py — PDF text layers carry them too
_BIDI_MARKS = "‎‏​﻿‪‫‬‭‮⁦⁧⁨⁩"

# "67", "230–231" (a clause range starts at its first clause)
_NUM_RE = re.compile(r"(\d+)(?:\s*[-–־]\s*\d+)?$")
# "(סעיף 44)", "(סעיפים 15-16, 25-26)" inside a descriptive clause title
_REF_RE = re.compile(r"סעי(?:ף|פים)\s*(\d+)")


def _pages_text(doc: "fitz.Document") -> list[str]:
    """Raw per-page text, exactly as ingestion.extract_text reads it."""
    return [page.get_text() for page in doc]


def _page_lines(pages_text: list[str]) -> list[tuple[int, list[str]]]:
    """(1-based page number, normalized text lines) for heading detection."""
    return [
        (pno, [l.strip().strip(_BIDI_MARKS).strip() for l in text.splitlines()])
        for pno, text in enumerate(pages_text, start=1)
    ]


def _heading_pages(page_lines: list[tuple[int, list[str]]], n: int) -> list[int]:
    """Pages where clause n appears as a numbered heading (see module doc)."""
    forms = (f".{n}", f"{n}.")
    bare = str(n)
    hits = []
    for pno, lines in page_lines:
        for i, s in enumerate(lines):
            if s in forms or (s == bare and i + 1 < len(lines) and lines[i + 1] == "."):
                hits.append(pno)
                break
    return hits


def _window_pages(raw_text: str, pages_text: list[str]) -> tuple[dict, bool]:
    """{clause_key: page} for every raw-text window, or ({}, False) when the
    stored raw_text no longer word-matches the PDF's extraction (hand-recovered
    text, an updated PDF) — wrong offsets would deep-link to random pages."""
    stored = raw_text.split()
    if not stored:
        return {}, True
    # mirror ingestion.extract_text: empty pages are dropped from the join,
    # so word offsets only advance over non-empty pages
    per_page = [(pno, text.split()) for pno, text in enumerate(pages_text, start=1)]
    extracted = [w for _, words in per_page for w in words]
    if extracted != stored:
        return {}, False
    starts = []  # (first word offset of page, page number)
    off = 0
    for pno, words in per_page:
        if words:
            starts.append((off, pno))
            off += len(words)
    pages: dict[str, int] = {}
    step = _CHUNK_WORDS - _OVERLAP_WORDS
    i = n = 0
    while i < len(stored):
        page = next(p for start, p in reversed(starts) if start <= i)
        pages[clause_key(f"chunk{n}", str(n))] = page
        n += 1
        i += step
    return pages, True


def _structured_targets(doc: dict) -> list[tuple[str, int]]:
    """(clause_key, clause number) for every locatable key-facts clause."""
    targets = []
    for section in doc.get("sections", []):
        sec_id = str(section.get("id", ""))
        if sec_id.startswith("annex"):
            continue  # annex row numbers are table positions, not clauses
        for clause in section.get("clauses", []):
            number = str(clause.get("number", "")).strip()
            if not number:
                continue
            m = _NUM_RE.fullmatch(number) or _REF_RE.search(number)
            if m:
                targets.append((clause_key(sec_id, number), int(m.group(1))))
    return targets


def build() -> dict:
    result: dict[str, dict[str, int]] = {}
    win_found = win_total = st_found = st_total = 0
    for doc in load_documents():
        doc_id = doc.get("document_id")
        source_file = doc.get("source_file") or ""
        pdf_path = PDF_DIR / source_file
        if not doc_id or not source_file.lower().endswith(".pdf") or not pdf_path.exists():
            # HTML-sourced orders etc. — the UI never PDF-links these, so
            # they don't count against coverage either
            safe_print(f"~ {doc_id}: אין PDF במאגר ({source_file or '—'}) — מדלג")
            continue
        n_windows = -(-len((doc.get("raw_text") or "").split()) // (_CHUNK_WORDS - _OVERLAP_WORDS))
        # denominator counts EVERY structured clause (annex rows and
        # descriptive titles included) — each can surface as a primary
        # source whose PDF link then opens page-less
        n_clauses = sum(len(s.get("clauses", [])) for s in doc.get("sections", []))
        targets = _structured_targets(doc)
        win_total += n_windows
        st_total += n_clauses

        with fitz.open(str(pdf_path)) as fdoc:
            pages_text = _pages_text(fdoc)
        page_lines = _page_lines(pages_text)

        pages, raw_ok = _window_pages(doc.get("raw_text") or "", pages_text)
        win_found += len(pages)

        # body clause numbers ascend page-monotonically; a candidate before
        # the previous clause's page is a table/TOC echo, not the heading
        found_n = 0
        floor = 1
        for key, n in sorted(targets, key=lambda t: t[1]):
            cand = [p for p in _heading_pages(page_lines, n) if p >= floor]
            if cand:
                pages[key] = floor = cand[0]
                found_n += 1
        st_found += found_n

        note = "" if raw_ok else " (raw_text לא תואם את ה-PDF — בלי עמודי חלונות)"
        safe_print(f"✓ {doc_id}: חלונות {len(pages) - found_n}/{n_windows},"
                   f" סעיפים {found_n}/{n_clauses}{note}")
        if pages:
            result[doc_id] = pages

    OUT_PATH.write_text(
        json.dumps(result, ensure_ascii=False, indent=1, sort_keys=True),
        encoding="utf-8",
    )
    safe_print(f"\nנכתב {OUT_PATH.name}: {sum(len(v) for v in result.values())} מיפויים"
               f" ({len(result)} פקודות) | חלונות {win_found}/{win_total},"
               f" סעיפים מובנים {st_found}/{st_total}")
    return result


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    build()
