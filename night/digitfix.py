# -*- coding: utf-8 -*-
"""Repair an order's digits by POSITION, not by mapping.

Why (18.09.2026). `night/digits.py` flags 180 of 294 orders as digit-untrustworthy;
16 of the 46 orders the adjudications name as answering are among them, and so
are all 7 orders that have no curated block at all. The damage sits in the PDF's
own character map: the glyphs on the page are right, the codepoints behind them
lie. `night/unscramble.py` tried to invert that as one permutation of the ten
digits and was rejected on 8 of 8 (17.08) — correctly, because the map is NOT
invertible: in 35.0314 clause 12 is stored as "19", clause 13 as "18", the year
83 as 38, and two different clause numbers are both stored as "11". Two digits
collapsing onto one cannot be undone by any table. Re-downloading does nothing
(the site's PDF is byte-identical, verified by SHA-256), and RTL run-reversal
explains only 26 of the 180.

What survives intact is the POSITION of every word: `page.get_text("words")`
returns the exact bounding box of each digit-bearing word, and the boxes are
right even where the text is wrong (verified visually on 35.0314 — box 4 sits on
the printed "12" that the text layer calls "19"). So: crop each box off the
rendered page, read only the digits inside it, and substitute them in place.
Every box is read on its own, which is exactly what handles the non-injective
case, and nothing is ever inverted.

Gates (a wrong number in the right place is the worst outcome, so refusal is
the default):
  * per word: the reading must have the same digit-run structure as the text
    layer (same number of runs, same length each) — "1.1.38" -> "1.1.83" passes,
    "0116116" -> "61.0110" is refused and left alone
  * per page: every digit word must be located in the page text sequentially at
    a word boundary, or the page is refused whole
  * per document: the regenerated text must equal the stored raw_text before
    patching (same extraction as ingestion/pdf_to_json.extract_text), and the
    plausible-year share must not fall
  * outside this file: a visual spot-check against the page image, then
    tests/run_all.py, night.routed_sectprobe, night.gate

    python -m night.digitfix 35.0314                      # build crops + sheets, alignment report; no API
    python -m night.digitfix 35.0314 --readings r.json    # dry: apply readings through the gates, write nothing
    python -m night.digitfix 35.0314 --read               # paid (~$0.01 a sheet): Haiku reads the sheets
    python -m night.digitfix 35.0314 --readings r.json --apply   # write raw_text + digits_fixed to storage
"""
from __future__ import annotations

import argparse
import base64
import json
import re
import sys
from datetime import date
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from night import config as C
from night.digits import year_share

RUN = re.compile(r"\d+")
SHEET_ITEMS = 40          # crops per contact sheet
CROP_DPI = 250
CROP_MARGIN = 2.0         # points around the word box
ITEM_H = 24.0             # crop height on the sheet, points
SHEET_W = 720.0
MODEL = "claude-haiku-4-5"
OUT_DIR = C.OUT / "digitfix"

READ_PROMPT = """בתמונה שורות. בכל שורה תווית באנגלית (כמו p1w4) ולידה גזיר קטן מעמוד של פקודה צבאית.
לכל תווית, העתק בדיוק את התווים שבגזיר שלידה: ספרות, נקודות, מקפים, פסיקים. אותיות עבריות אפשר להשמיט.
אל תתקן ואל תנחש. גזיר שאינו קריא — כתוב "?".
החזר JSON בלבד, בלי הסבר: {"p1w4": "12", "p1w2": "83", ...}"""


# --------------------------------------------------------------------------
# pure functions (tests/test_digitfix.py)

def digit_runs(s: str) -> list[str]:
    return RUN.findall(s or "")


def runs_compatible(a: str, b: str) -> bool:
    """Same number of digit runs, each of the same length."""
    ra, rb = digit_runs(a), digit_runs(b)
    return len(ra) == len(rb) and all(len(x) == len(y) for x, y in zip(ra, rb))


def substitute(word: str, reading: str | None) -> tuple[str, str]:
    """Put the read digits into the text-layer word, run by run, keeping every
    non-digit character of the text layer. Returns (new_word, status) with
    status in unread / refused / same / changed."""
    if reading is None or not reading.strip() or reading.strip() == "?":
        return word, "unread"
    if not runs_compatible(word, reading):
        return word, "refused"
    it = iter(digit_runs(reading))
    new = RUN.sub(lambda m: next(it), word)
    return new, ("changed" if new != word else "same")


_GLUE = set("0123456789.")


def find_word(text: str, word: str, start: int) -> int:
    """First occurrence of `word` at or after `start` whose neighbours are
    neither digits nor '.', or -1. A bare "1" must not match inside "11", and
    "38" must not match inside the date "1.1.38". Hebrew letters may touch the
    word: `get_text("words")` splits "'אוק38" into "'אוק" and "38", while the
    page text keeps them glued."""
    i = text.find(word, start)
    while i >= 0:
        before = i == 0 or text[i - 1] not in _GLUE
        j = i + len(word)
        after = j >= len(text) or text[j] not in _GLUE
        if before and after:
            return i
        i = text.find(word, i + 1)
    return -1


def patch_page_text(page_text: str, words: list[dict], readings: dict[str, str]
                    ) -> tuple[str | None, dict]:
    """Rewrite the digit words of one page's text in place.

    `words` are the page's digit-bearing words in text order, each {id, text}.
    Every word must be found sequentially at a word boundary, or the page is
    refused (None) — a page whose words cannot be located is a page whose
    substitutions cannot be trusted."""
    stats = {"words": len(words), "changed": 0, "same": 0, "refused": 0, "unread": 0,
             "changes": []}
    out, cursor = [], 0
    for w in words:
        i = find_word(page_text, w["text"], cursor)
        if i < 0:
            stats["error"] = f"{w['id']} {w['text']!r} not found in page text"
            return None, stats
        new, status = substitute(w["text"], readings.get(w["id"]))
        stats[status] += 1
        if status == "changed":
            stats["changes"].append((w["id"], w["text"], new))
        out.append(page_text[cursor:i])
        out.append(new)
        cursor = i + len(w["text"])
    out.append(page_text[cursor:])
    return "".join(out), stats


def rebuild_raw_text(pages: list[str]) -> str:
    """Exactly ingestion/pdf_to_json.extract_text's join."""
    return "\n\n".join(p for p in pages if p.strip())


_ORDER_NUMBER = re.compile(r"\d+\.\d{4}\b")


def years_gate_text(text: str) -> str:
    """The plausible-year gate must not see order numbers: a corrupted
    "33.2023" counts as the year 2023, and restoring it to "33.0203" then looks
    like a lost year (32.0402 failed the gate that way). Order-number shapes are
    blanked before the share is measured, on both sides alike."""
    return _ORDER_NUMBER.sub(" ", text or "")


def merge_readings(words: list[dict], first: dict[str, str], second: dict[str, str]
                   ) -> tuple[dict[str, str], int]:
    """Two independent reads of the same crops -> one reading per word, kept
    only where both reads lead to the SAME substituted word. A disagreement
    becomes "?" (unread: the word is left alone). Calibration on 35.0314 showed
    the one way a single read goes wrong — a clipped glyph read as a digit —
    and two reads rarely clip the same way. Returns (merged, disagreements)."""
    merged, disagree = {}, 0
    for w in words:
        a, b = first.get(w["id"]), second.get(w["id"])
        if a is None or b is None:
            merged[w["id"]] = a if b is None else b
            if a is None and b is None:
                merged.pop(w["id"], None)
            continue
        if substitute(w["text"], a)[0] == substitute(w["text"], b)[0]:
            merged[w["id"]] = a
        else:
            merged[w["id"]] = "?"
            disagree += 1
    return merged, disagree


# --------------------------------------------------------------------------
# PDF side

def _pdf_for(doc: dict):
    import fitz
    path = C.ROOT / "pdf-ldf_law" / str(doc.get("source_file") or "")
    if not path.exists():
        raise FileNotFoundError(f"pdf missing: {path.name}")
    return fitz.open(str(path))


def digit_words(page, pno: int) -> list[dict]:
    """The page's words that carry a digit, in text order, with their boxes."""
    out = []
    for k, w in enumerate(page.get_text("words")):
        x0, y0, x1, y1, text = w[0], w[1], w[2], w[3], w[4]
        if RUN.search(text):
            out.append({"id": f"p{pno}w{k}", "text": text, "bbox": [x0, y0, x1, y1]})
    return out


def build_sheets(pdf, doc_id: str, out_dir: Path) -> dict:
    """Crop every digit word off the rendered pages and lay the crops out on
    labelled contact sheets. Returns the manifest (also written to disk)."""
    import fitz
    out_dir.mkdir(parents=True, exist_ok=True)
    pages_meta, items = [], []
    for pno in range(len(pdf)):
        page = pdf[pno]
        words = digit_words(page, pno)
        pages_meta.append({"page": pno, "words": words})
        for w in words:
            x0, y0, x1, y1 = w["bbox"]
            clip = fitz.Rect(x0 - CROP_MARGIN, y0 - CROP_MARGIN, x1 + CROP_MARGIN, y1 + CROP_MARGIN)
            pix = page.get_pixmap(dpi=CROP_DPI, clip=clip)
            items.append((w["id"], pix))

    sheets = []
    for s, start in enumerate(range(0, len(items), SHEET_ITEMS)):
        batch = items[start:start + SHEET_ITEMS]
        # layout pass: rows of (label, crop) flowing left to right
        placed, x, y, row_h = [], 12.0, 12.0, ITEM_H + 12.0
        for wid, pix in batch:
            w_pt = min(220.0, pix.width * (ITEM_H / max(1, pix.height)))
            if x + 46 + w_pt > SHEET_W - 12 and x > 12.0:
                x, y = 12.0, y + row_h
            placed.append((wid, pix, x, y, w_pt))
            x += 46 + w_pt + 18
        height = y + row_h + 12
        sheet = fitz.open()
        sp = sheet.new_page(width=SHEET_W, height=height)
        for wid, pix, x, y, w_pt in placed:
            sp.insert_text(fitz.Point(x, y + 17), wid, fontsize=8)
            sp.insert_image(fitz.Rect(x + 46, y + 2, x + 46 + w_pt, y + 2 + ITEM_H), pixmap=pix)
        path = out_dir / f"{doc_id}_sheet{s}.png"
        sp.get_pixmap(dpi=200).save(str(path))
        sheets.append({"path": str(path), "items": [wid for wid, *_ in placed]})
        sheet.close()

    manifest = {"doc_id": doc_id, "pages": pages_meta, "sheets": sheets,
                "digit_words": len(items)}
    (out_dir / f"{doc_id}_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    return manifest


def page_texts(pdf) -> list[str]:
    return [p.get_text() for p in pdf]


# --------------------------------------------------------------------------
# reading the sheets (paid)

def read_sheets(manifest: dict, doc_id: str) -> tuple[dict[str, str], float]:
    import backend
    from night.ledger import Ledger, cost_usd
    ledger = Ledger(C.LEDGER)
    readings, total = {}, 0.0
    for n, sheet in enumerate(manifest["sheets"]):
        png = Path(sheet["path"]).read_bytes()
        rid = ledger.reserve(f"digitfix:{doc_id}#s{n}", 0.02)
        try:
            r = backend.client.messages.create(
                model=MODEL, max_tokens=3000,
                messages=[{"role": "user", "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                                 "data": base64.b64encode(png).decode()}},
                    {"type": "text", "text": READ_PROMPT},
                ]}],
            )
        except Exception as e:  # noqa: BLE001 - the ledger must settle either way
            ledger.settle(rid, 0.0)
            C.log(f"[digitfix] sheet {n}: api error {type(e).__name__}: {e}")
            continue
        usd = cost_usd(MODEL, input_tokens=r.usage.input_tokens, output_tokens=r.usage.output_tokens)
        ledger.settle(rid, usd)
        total += usd
        text = "".join(b.text for b in r.content if b.type == "text").strip()
        text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            C.log(f"[digitfix] sheet {n}: unparseable reply")
            continue
        for k, v in data.items():
            if k in sheet["items"]:
                readings[k] = str(v)
        C.log(f"[digitfix] sheet {n}: {len(data)} readings, ${usd:.4f}")
    return readings, total


# --------------------------------------------------------------------------
# patching a document

def patch_document(doc: dict, manifest: dict, readings: dict[str, str]) -> dict:
    """Run every page through the gates. Returns a report with the new raw_text
    (or None) and per-page stats; writes nothing."""
    pdf = _pdf_for(doc)
    texts = page_texts(pdf)
    stored = str(doc.get("raw_text", ""))
    faithful = rebuild_raw_text(texts) == stored
    new_pages, pages, totals = [], [], {"changed": 0, "same": 0, "refused": 0, "unread": 0}
    refused_pages = 0
    for meta, text in zip(manifest["pages"], texts):
        new, stats = patch_page_text(text, meta["words"], readings)
        if new is None:
            refused_pages += 1
            new = text
        else:
            for k in totals:
                totals[k] += stats[k]
        pages.append({"page": meta["page"], **{k: v for k, v in stats.items() if k != "changes"},
                      "changes": stats.get("changes", [])})
        new_pages.append(new)
    new_raw = rebuild_raw_text(new_pages)
    before, _ = year_share(years_gate_text(stored))
    after, _ = year_share(years_gate_text(new_raw))
    ok_years = before is None or after is None or after >= before
    return {"faithful": faithful, "refused_pages": refused_pages, "totals": totals,
            "year_share": (before, after), "years_ok": ok_years, "pages": pages,
            "raw_text": new_raw if (faithful and refused_pages == 0 and ok_years) else None}


def load_doc(doc_id: str) -> dict:
    import backend
    for d in backend.load_documents():
        if d.get("document_id") == doc_id:
            return d
    raise KeyError(doc_id)


def print_report(doc_id: str, rep: dict) -> None:
    b, a = rep["year_share"]
    fmt = lambda x: "n/a" if x is None else f"{x:.2f}"
    t = rep["totals"]
    C.log(f"[digitfix] {doc_id}: faithful={rep['faithful']} refused_pages={rep['refused_pages']} "
          f"changed={t['changed']} same={t['same']} refused={t['refused']} unread={t['unread']} "
          f"years {fmt(b)} -> {fmt(a)} ({'ok' if rep['years_ok'] else 'WORSE'})")
    shown = 0
    for p in rep["pages"]:
        for wid, old, new in p["changes"]:
            if shown < 16:
                C.log(f"[digitfix]    {wid}: {old} -> {new}")
                shown += 1
    if rep["raw_text"] is None:
        C.log("[digitfix] gates not passed - nothing would be written")


def apply_document(doc_id: str, rep: dict, reader: str) -> Path:
    from night.rehearse import doc_path
    path = doc_path(doc_id)
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["raw_text"] = rep["raw_text"]
    doc["digits_fixed"] = {"when": date.today().isoformat(), "reader": reader, **rep["totals"],
                           "year_share": rep["year_share"][1]}
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("doc_id")
    ap.add_argument("--readings", help="JSON {word_id: reading} to apply through the gates")
    ap.add_argument("--read", action="store_true", help="paid: Haiku reads the sheets")
    ap.add_argument("--apply", action="store_true", help="write the patched raw_text to storage")
    args = ap.parse_args()

    doc = load_doc(args.doc_id)
    out_dir = OUT_DIR / args.doc_id
    pdf = _pdf_for(doc)
    manifest = build_sheets(pdf, args.doc_id, out_dir)
    faithful = rebuild_raw_text(page_texts(pdf)) == str(doc.get("raw_text", ""))
    C.log(f"[digitfix] {args.doc_id}: {len(pdf)} pages, {manifest['digit_words']} digit words, "
          f"{len(manifest['sheets'])} sheets in {out_dir} | regenerated text == stored: {faithful}")

    readings: dict[str, str] = {}
    reader = "none"
    if args.read:
        readings, usd = read_sheets(manifest, args.doc_id)
        reader = MODEL
        rpath = out_dir / f"{args.doc_id}_readings.json"
        rpath.write_text(json.dumps(readings, ensure_ascii=False, indent=1), encoding="utf-8")
        C.log(f"[digitfix] {len(readings)} readings, ${usd:.4f}, saved {rpath}")
    elif args.readings:
        readings = json.loads(Path(args.readings).read_text(encoding="utf-8"))
        reader = Path(args.readings).name

    if not readings:
        return
    rep = patch_document(doc, manifest, readings)
    print_report(args.doc_id, rep)
    if args.apply:
        if rep["raw_text"] is None:
            C.log("[digitfix] --apply refused by the gates")
            sys.exit(1)
        path = apply_document(args.doc_id, rep, reader)
        C.log(f"[digitfix] written {path.name} - re-index this order before measuring")


if __name__ == "__main__":
    main()
