# -*- coding: utf-8 -*-
"""Read-only access to per-order publication/version dates.

storage/doc_dates.json is generated offline by _build_doc_dates.py (rerun it
after a reingest); this module is the runtime face the UI uses to show an
honest "נוסח מיום X" badge. A document with no confidently extractable date
maps to None — callers should simply not render a badge for it.

Never raises: a missing/corrupt file or an unknown document_id is None.
"""
import json
from functools import lru_cache
from pathlib import Path

_DATES_FILE = Path(__file__).parent / "storage" / "doc_dates.json"


@lru_cache(maxsize=1)
def _load() -> dict:
    """{document_id: {"date", "raw", "marker"}} — cached for the process
    lifetime (the file only changes when _build_doc_dates.py is rerun)."""
    try:
        data = json.loads(_DATES_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def date_for(document_id: str | None) -> str | None:
    """ISO "YYYY-MM-DD" for the order's latest defensible version date."""
    entry = _load().get(document_id or "")
    if not isinstance(entry, dict):
        return None
    date = entry.get("date")
    return date if isinstance(date, str) and date else None


def display_date(document_id: str | None) -> str | None:
    """The same date as "DD.MM.YYYY" for the RTL UI, or None."""
    iso = date_for(document_id)
    if not iso:
        return None
    try:
        y, m, d = iso.split("-")
        return f"{d}.{m}.{y}"
    except ValueError:
        return None


def badge(document_id: str | None) -> str | None:
    """Badge text for the UI: "DD.MM.YYYY", or "MM.YYYY" when the source
    marker only carried month precision (the builder tags those
    ";month-precision" — showing an invented day would overstate what the
    PDF actually says)."""
    entry = _load().get(document_id or "")
    if not isinstance(entry, dict):
        return None
    iso = entry.get("date")
    if not (isinstance(iso, str) and iso):
        return None
    try:
        y, m, d = iso.split("-")
    except ValueError:
        return None
    if ";month-precision" in (entry.get("marker") or ""):
        return f"{m}.{y}"
    return f"{d}.{m}.{y}"
