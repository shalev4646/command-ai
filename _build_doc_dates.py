# -*- coding: utf-8 -*-
"""Build storage/doc_dates.json — per-order publication/version dates.

For every document backend.load_documents() knows about, scan its source PDF
(first 3 pages + last page; all pages as a fallback) with PyMuPDF for the
order's own version markers — "נוסח פקודה זה פורסם במאגר הפקודות... בתאריך",
"תאריכי עדכון הפקודה", "הפקודה עודכנה בתאריך", "תוקף סעיפים... מיום",
corner edition stamps ("יולי69") — normalize the LATEST defensible date to
ISO YYYY-MM-DD, and record which marker matched. The UI reads the result via
doc_dates.py to show an honest "נוסח מיום X" per order.

Rerun after every reingest (newly ingested orders have no entry until you do):
    python _build_doc_dates.py

Honesty rules (deliberate, don't "fix" without checking the PDFs):
- Only text near a version marker is harvested; dates inside clause content
  (payment dates, war references) are ignored, so the max can't be poisoned.
- Several scanned PDFs have broken font CMaps: digits extract as OTHER digits
  (e.g. 1986 -> "6218", 2003 -> "7003"). Reconstructing those is guessing, so
  a document whose version marker carries an unreadable year is left null —
  including its weaker fallbacks, because a readable-but-old corner stamp on
  a doc with an unreadable newer amendment would lie about freshness.
- Purely REVERSED digit runs (RTL visual order: 2006 -> "6002") are the one
  mangling that is recoverable; when used, the day is dropped (a reversed
  2-digit day is ambiguous) and the marker says "digits-reversed".
- Two-digit years: YY <= 27 -> 20YY, else 19YY (a 2069 publication date is
  indefensible). Bare corner stamps "יוני18" with YY <= 27 are skipped
  entirely: there the number could just as well be a day of month.
- Month-only markers become YYYY-MM-01 and the marker says "month-precision".
"""
import datetime
import json
import re
import sys
from pathlib import Path

import fitz  # PyMuPDF

sys.path.insert(0, str(Path(__file__).parent))
from backend import load_documents
from common import safe_print

ROOT = Path(__file__).parent
PDF_DIR = ROOT / "pdf-ldf_law"
OUT_PATH = ROOT / "storage" / "doc_dates.json"

MIN_YEAR = 1948
TODAY = datetime.date.today()

MONTHS = {
    "ינואר": 1, "פברואר": 2, "מרס": 3, "מרץ": 3, "מארס": 3, "אפריל": 4,
    "מאי": 5, "יוני": 6, "יולי": 7, "אוגוסט": 8, "ספטמבר": 9,
    "אוקטובר": 10, "נובמבר": 11, "דצמבר": 12,
    # abbreviated forms used in old corner stamps ("ספט' 49")
    "ינו": 1, "פבר": 2, "אפר": 4, "יונ": 6, "יול": 7, "אוג": 8,
    "ספט": 9, "אוק": 10, "נוב": 11, "דצמ": 12,
}
_MONTH_RE = "|".join(sorted(MONTHS, key=len, reverse=True))

# Lines that mark a version event. Final letters (ם/ן) are distinct
# codepoints, hence the doubled forms. Bare "בתאריך" is NOT a marker — it
# appears in clause content ("ישולם בתאריך...").
KEYWORD = re.compile(
    r"תאריכי עדכון|עדכון הפקודה|נוסח פקודה|(?<![א-ת])פורס[מם]|עודכנ|עודכן"
    r"|תוקף|תוקן|תוקנ|בוטל|מהדורה|בחוזר תיקונים"
)
# Footer anchors whose date list may spill over many following short lines.
BLOCK_ANCHOR = re.compile(r"פורסם ב\s*מאגר|תאריכי עדכון|עדכון הפקודה")
# A line that can belong to such a spilled date list (dates, bare day
# numbers, or the bare punctuation separating the entries).
DATEY_LINE = re.compile(
    r"^[\s.,:;()'\"-]*(ב?(?:%s)['\s]*\d{0,4}|\d{1,4}|בתאר\s*יך\s*\d{0,2})?"
    r"[\s.,:;()'\"-]*$" % _MONTH_RE
)

# Date patterns. (?<!\d)/(?!\d) guards keep years from being carved out of
# longer digit runs (order numbers like 2015050).
N1 = re.compile(r"(?<![\d.])(\d{1,2})[./](\d{1,2})[./](\d{2,4})(?![\d.])")     # dd.mm.yy(yy)
H1 = re.compile(r"(?<!\d)(\d{1,2})\s*ב\s*(%s)\s*,?\s*(\d{4})(?!\d)" % _MONTH_RE)   # day month year
H1B = re.compile(r"(?<!\d)(\d{1,2})\s*ב\s*(%s)\s*,?\s*(\d{2})(?![\d.])" % _MONTH_RE)  # day month yy
H2 = re.compile(r"(?<!\d)(\d{4})\s*ב?(%s)\s*(\d{1,2})?(?!\d)" % _MONTH_RE)         # year month [day] (RTL jumble)
H3 = re.compile(r"ב(%s)\s*,?\s*(\d{4})(?!\d)" % _MONTH_RE)                         # month year
H3B = re.compile(r"(?<![א-ת])(%s)\s*,?\s*(\d{4})(?!\d)" % _MONTH_RE)               # month year, no ב ("אפריל 2022")
H4 = re.compile(r"(?<![א-ת])ב?(%s)['׳]?\s*(\d{2})(?![\d.])" % _MONTH_RE)           # month yy (corner stamps)
STANDALONE_MY = re.compile(r"^[\s.,']*ב?(%s)\s*(\d{4})(?!\d)[\s.,']*$" % _MONTH_RE)
STANDALONE_YMD = re.compile(r"^[\s.,']*(\d{4})\s*ב?(%s)\s*(\d{1,2})?[\s.,']*$" % _MONTH_RE)
H4_LINE = re.compile(r"^[\s.,']*ב?(%s)['׳]?\s*(\d{2})[\s.,']*$" % _MONTH_RE)

# Corrupted-digit telltales on a version-marker window: a 4-digit run that is
# implausible even reversed, or digit runs eaten into dots ("1.22", ".532",
# "53.0"). The shapes are chosen to NOT match order/reference numbers
# (8.0109, 501.50, 521.221, 32.2516) or real dd.mm.yyyy dates.
# (?<![\d.]) so the fraction of an order number ("לפקודה31.0203", 'הפ"ע3.0501')
# is never mistaken for a corrupt year.
GARBAGE_4 = re.compile(r"(?<![\d.])\d{4}(?!\d)")
GARBAGE_DOT = re.compile(r"(?<![\d.])(?:\.\d{2,3}|\d{1,2}\.\d{2}|\d{2}\.\d)(?![\d.])")


def _pivot2(yy: int) -> int:
    return 2000 + yy if yy <= 27 else 1900 + yy


def _plausible(y: int) -> bool:
    return MIN_YEAR <= y <= TODAY.year


def _year4(tok: str):
    """(year, was_reversed) for a 4-digit token, or (None, False).

    Direct reading wins; a reversed reading is accepted only when the direct
    one is impossible (RTL visual-order artifact, e.g. "6002" -> 2006).
    """
    y = int(tok)
    if _plausible(y):
        return y, False
    rev = int(tok[::-1])
    if _plausible(rev):
        return rev, True
    return None, False


def _mk(y: int, m: int, d: int | None, raw: str, marker: str):
    """Validated candidate dict, or None."""
    precision = "day"
    if d is None:
        d, precision = 1, "month"
    try:
        date = datetime.date(y, m, d)
    except ValueError:
        return None
    if not (_plausible(y) and date <= TODAY):
        return None
    if precision == "month":
        marker += ";month-precision"
    return {"date": date, "raw": " ".join(raw.split()), "marker": marker}


def _marker_name(window: str) -> str:
    for pat, name in (
        ("תאריכי עדכון", "update-dates-list"),
        ("עדכון הפקודה", "update-dates-list"),
        ("עודכנ", "updated-line"), ("עודכן", "updated-line"),
        ("תוקן", "amended-clause"), ("תוקנ", "amended-clause"),
        ("בוטל", "cancelled-clause"),
        ("בחוזר תיקונים", "tikkunim-circular"),
        ("תוקף", "tokef-clause"),
        ("פורס", "published-line"),
        ("נוסח פקודה", "published-line"),
        ("מהדורה", "edition-line"),
    ):
        if pat in window:
            return name
    return "marker-line"


# A number right after "סעיף/סעיפים" is a clause number, not a day of month
# ("עודכן סעיף22 במרץ2019" is a March-2019 update of clause 22, day unknown).
_CLAUSE_NO = re.compile(r"סעי[פף](?:ים)?(?:\s*(?:קטן|משנה))?\s*$")


def _day_or_none(window: str, m: re.Match, group: int):
    if _CLAUSE_NO.search(window[: m.start(group)]):
        return None
    return int(m.group(group))


def _harvest(window: str, marker: str, with_2digit: bool):
    """All valid date candidates in a version-marker window."""
    out = []
    for m in N1.finditer(window):
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        y = _pivot2(y) if y < 100 else y
        if 1 <= mo <= 12:
            c = _mk(y, mo, d, m.group(0), marker)
            if c:
                out.append(c)
    for m in H1.finditer(window):
        y, rev = _year4(m.group(3))
        if y:
            d = None if rev else _day_or_none(window, m, 1)
            c = _mk(y, MONTHS[m.group(2)], d,
                    m.group(0), marker + (";digits-reversed" if rev else ""))
            if c:
                out.append(c)
    for m in H2.finditer(window):
        y, rev = _year4(m.group(1))
        d = m.group(3)
        if y:
            c = _mk(y, MONTHS[m.group(2)], None if (rev or not d) else int(d),
                    m.group(0), marker + (";digits-reversed" if rev else ""))
            if c:
                out.append(c)
    for m in H3.finditer(window):
        y, rev = _year4(m.group(2))
        if y:
            c = _mk(y, MONTHS[m.group(1)], None, m.group(0),
                    marker + (";digits-reversed" if rev else ""))
            if c:
                out.append(c)
    for m in H3B.finditer(window):
        y, rev = _year4(m.group(2))
        if y:
            c = _mk(y, MONTHS[m.group(1)], None, m.group(0),
                    marker + (";digits-reversed" if rev else ""))
            if c:
                out.append(c)
    if with_2digit:
        for m in H1B.finditer(window):
            c = _mk(_pivot2(int(m.group(3))), MONTHS[m.group(2)],
                    _day_or_none(window, m, 1),
                    m.group(0), marker + ";2digit-year")
            if c:
                out.append(c)
        for m in H4.finditer(window):
            yy = int(m.group(2))
            if yy >= 28:  # <=27 could equally be a day of month — skip
                c = _mk(1900 + yy, MONTHS[m.group(1)], None, m.group(0),
                        marker + ";2digit-year")
                if c:
                    out.append(c)
    return out


def _window_is_garbage(window: str) -> bool:
    for m in GARBAGE_4.finditer(window):
        y, _ = _year4(m.group(0))
        if y is None:
            return True
    return bool(GARBAGE_DOT.search(window))


def _scan_lines(lines: list[str]):
    """(marker_candidates, fallback_candidates, garbage_flag) for one page/text."""
    t1, t2, garbage = [], [], False
    for i, line in enumerate(lines):
        if "סיווג" in line:  # classification re-stamps aren't content updates
            continue
        if KEYWORD.search(line):
            # a neighboring classification line must neither contribute dates
            # nor poison this window's garbage check
            window = " ".join(ln for ln in lines[i:i + 5] if "סיווג" not in ln)
            if BLOCK_ANCHOR.search(line):
                # footer date lists spill over many short lines (one order
                # carries 18 amendment dates, one line each)
                j, block = i + 1, [line]
                while j < len(lines) and len(block) < 80 and DATEY_LINE.match(lines[j]):
                    block.append(lines[j])
                    j += 1
                window = " ".join(block) + " " + window
            if _window_is_garbage(window):
                garbage = True
            t1 += _harvest(window, _marker_name(window), with_2digit=True)
            continue
        # weaker, keyword-less forms: standalone footer/corner stamps
        if STANDALONE_MY.match(line) or STANDALONE_YMD.match(line):
            t2 += _harvest(line, "standalone-stamp", with_2digit=False)
        elif H4_LINE.match(line):
            t2 += _harvest(line, "corner-stamp", with_2digit=True)
        elif len(line) <= 20 and re.search(_MONTH_RE, line):
            joined = " ".join(lines[max(0, i - 1):i + 2])
            t2 += _harvest(joined, "header-window", with_2digit=False)
    return t1, t2, garbage


def _pages_lines(doc, page_ids):
    for pi in page_ids:
        text = doc[pi].get_text()
        yield [ln.strip() for ln in text.splitlines() if ln.strip()]


def extract_pdf(pdf_path: Path):
    doc = fitz.open(str(pdf_path))
    try:
        n = len(doc)
        first = sorted({p for p in (0, 1, 2, n - 1) if 0 <= p < n})
        t1, t2, garbage = [], [], False
        for lines in _pages_lines(doc, first):
            a, b, g = _scan_lines(lines)
            t1 += a
            t2 += b
            garbage = garbage or g
        if not t1 and not t2 and not garbage and n > 4:
            for lines in _pages_lines(doc, range(n)):
                a, b, g = _scan_lines(lines)
                t1 += a
                t2 += b
                garbage = garbage or g
    finally:
        doc.close()
    return t1, t2, garbage


def pick(t1, t2, garbage):
    """Latest defensible candidate. A garbled version marker anywhere in the
    doc disqualifies everything that leans on 2-digit years or keyword-less
    stamps: on a corrupted-digit doc those may themselves be mis-mapped, and
    an old-but-readable corner stamp must not masquerade as the version date
    of a doc whose real amendment year is unreadable. Full 4-digit,
    plausibility-checked dates keep counting."""
    if garbage:
        pool = [c for c in t1 if "2digit-year" not in c["marker"]]
    else:
        pool = t1 + t2
    if not pool:
        return None
    return max(pool, key=lambda c: (c["date"], "month-precision" not in c["marker"]))


def main() -> None:
    docs = load_documents()
    result, dated = {}, 0
    rows = []
    for d in docs:
        doc_id = d.get("document_id")
        if not doc_id:
            continue
        src = d.get("source_file") or ""
        entry = {"date": None, "raw": None, "marker": None}
        pdf = PDF_DIR / src
        if src.lower().endswith(".pdf") and pdf.exists():
            t1, t2, garbage = extract_pdf(pdf)
        elif d.get("raw_text"):
            # .html-sourced orders: same markers over the stored text (clean,
            # logical-order lines — no PDF to scan)
            lines = [ln.strip() for ln in d["raw_text"].splitlines() if ln.strip()]
            a, b, garbage = _scan_lines(lines)
            t1, t2 = a, []  # raw_text is clause-level: keyword lines only
        else:
            t1, t2, garbage = [], [], False
        best = pick(t1, t2, garbage)
        if best:
            entry = {"date": best["date"].isoformat(), "raw": best["raw"][:120],
                     "marker": best["marker"]}
            dated += 1
        elif garbage:
            entry["marker"] = None  # unreadable digits — honest null
        result[doc_id] = entry
        rows.append((doc_id, (d.get("title") or "")[:38], entry))

    OUT_PATH.write_text(
        json.dumps(dict(sorted(result.items())), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    safe_print(f"{'doc_id':<12} {'date':<11} {'marker':<40} raw / title")
    for doc_id, title, e in sorted(rows, key=lambda r: (r[2]['date'] is None, r[0])):
        safe_print(f"{doc_id:<12} {e['date'] or '-':<11} {(e['marker'] or '-'):<40} "
                   f"{(e['raw'] or '-')[:44]}  | {title}")
    safe_print(f"\nWrote {OUT_PATH}: {dated}/{len(result)} docs dated")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    main()
