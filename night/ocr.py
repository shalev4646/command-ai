# -*- coding: utf-8 -*-
"""Hebrew OCR for the scanned orders the extractor rejects, into a waiting folder.

    venv\\Scripts\\python.exe -m night.ocr <pdf-or-folder> [...] --out <dir> [--dpi 300] [--psm 3] [--force]

Tesseract 5.4 (installed 23.09.2026 at C:\\Program Files\\Tesseract-OCR) with the
tessdata_best Hebrew model in %LOCALAPPDATA%\\tessdata; both paths can be overridden
with CAI_TESSERACT / CAI_TESSDATA. --psm 3 was chosen on the combat-certificate page
that carries a header table: psm 3 reads the table too, psm 6 does not.

For every PDF: pages rendered at --dpi with PyMuPDF, each sent to tesseract, and
  <out>/<stem>.ocr.txt   the text with "===== עמוד N =====" markers
  <out>/<stem>.ocr.json  per-page stats + the dry intake verdicts below
  <out>/REPORT.md        one table over every .ocr.json in <out> (regenerated)
Nothing is written under pdf-ldf_law/ or storage/: this is a waiting folder, and a
PDF must never be moved into the ingest folder by this tool (sources_pending/README.md).

The stats are the quality signal a human reads before deciding to ingest: characters
per page, digits per page, and the noise ratio — non-space characters that are neither
Hebrew letters, Latin letters, digits nor ordinary punctuation, over all non-space
characters. The dry intake verdict replays two gates without running the intake:
the extractor's sparse-text gate (ingestion.pdf_to_json.MIN_CHARS_PER_PAGE, on the OCR
text instead of the text layer) and the intake's reversed-id check (the order number
read from the first page against the number in the filename, night.intake logic).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TESSERACT = os.environ.get("CAI_TESSERACT", r"C:\Program Files\Tesseract-OCR\tesseract.exe")
TESSDATA = os.environ.get("CAI_TESSDATA", str(Path(os.environ.get("LOCALAPPDATA", "")) / "tessdata"))
PAGE_MARK = "===== עמוד {n} ====="
_HEB = re.compile(r"[\u05d0-\u05ea]")
_LAT = re.compile(r"[A-Za-z]")
_DIG = re.compile(r"\d")
_PUNCT = set(".,:;!?()[]{}<>\"'`´‘’“”„«»-–—_/\\|%₪$#*+=@&§~^…•·")
_ORDER_NO = re.compile(r"(?<!\d)(\d{1,2})\.(\d{4})(?!\d)")


def _min_chars_per_page() -> int:
    try:
        from ingestion import pdf_to_json as P  # noqa: WPS433
        return int(getattr(P, "MIN_CHARS_PER_PAGE", 900))
    except Exception:
        return 900


def _number_from_filename(name: str) -> str | None:
    try:
        from night.intake import _number_from_filename as f
        return f(name)
    except Exception:
        return None


def ocr_page(png: Path, psm: int, lang: str) -> str:
    r = subprocess.run([TESSERACT, str(png), "stdout", "--tessdata-dir", TESSDATA, "-l", lang, "--psm", str(psm)],
                       capture_output=True)
    if r.returncode != 0:
        raise RuntimeError(f"tesseract exit {r.returncode}: {r.stderr.decode('utf-8', 'replace')[:300]}")
    return r.stdout.decode("utf-8", "replace")


def page_stats(text: str) -> dict:
    nonspace = [c for c in text if not c.isspace()]
    heb = len(_HEB.findall(text))
    lat = len(_LAT.findall(text))
    dig = len(_DIG.findall(text))
    punct = sum(1 for c in nonspace if c in _PUNCT)
    noise = max(0, len(nonspace) - heb - lat - dig - punct)
    return {"chars": len(nonspace), "hebrew": heb, "latin": lat, "digits": dig,
            "noise": noise, "noise_ratio": round(noise / len(nonspace), 3) if nonspace else 0.0,
            "lines": sum(1 for l in text.splitlines() if l.strip())}


def order_number_in(text: str) -> str | None:
    m = _ORDER_NO.search(text[:1500])
    return f"{int(m.group(1))}.{m.group(2)}" if m else None


def process(pdf: Path, out: Path, dpi: int, psm: int, lang: str, force: bool) -> dict:
    import fitz  # PyMuPDF

    stem = pdf.stem
    jpath, tpath = out / f"{stem}.ocr.json", out / f"{stem}.ocr.txt"
    if jpath.exists() and tpath.exists() and not force:
        return json.loads(jpath.read_text(encoding="utf-8"))
    doc = fitz.open(str(pdf))
    pages, chunks = [], []
    t0 = time.time()
    with tempfile.TemporaryDirectory() as tmp:
        for i, page in enumerate(doc, 1):
            png = Path(tmp) / f"p{i}.png"
            page.get_pixmap(dpi=dpi).save(str(png))
            text = ocr_page(png, psm, lang)
            layer = page.get_text()
            st = page_stats(text)
            st.update({"page": i, "layer_chars": len("".join(layer.split()))})
            pages.append(st)
            chunks.append(PAGE_MARK.format(n=i) + "\n" + text.strip() + "\n")
    elapsed = round(time.time() - t0, 1)
    full = "\n".join(chunks)
    n = len(pages)
    chars = sum(p["chars"] for p in pages)
    min_cpp = _min_chars_per_page()
    avg = chars // n if n else 0
    layer_avg = sum(p["layer_chars"] for p in pages) // n if n else 0
    seen = order_number_in(full)
    want = _number_from_filename(pdf.name)
    rec = {
        "file": pdf.name, "source_dir": str(pdf.parent), "pages": n, "dpi": dpi, "psm": psm, "lang": lang,
        "elapsed_s": elapsed, "chars": chars, "chars_per_page": avg, "layer_chars_per_page": layer_avg,
        "digits_per_page": round(sum(p["digits"] for p in pages) / n, 1) if n else 0,
        "noise_ratio": round(sum(p["noise"] for p in pages) / max(1, chars), 3),
        "hebrew_ratio": round(sum(p["hebrew"] for p in pages) / max(1, chars), 3),
        "order_number_seen": seen, "order_number_filename": want,
        "gate_sparse_text": {"min_chars_per_page": min_cpp, "layer_passes": layer_avg >= min_cpp,
                             "ocr_passes": avg >= min_cpp},
        "gate_id": ("n/a" if not want else "unreadable" if not seen else
                    "match" if seen.replace(".", "").lstrip("0") == want.replace(".", "").lstrip("0") else "MISMATCH"),
        "pages_detail": pages,
    }
    tpath.write_text(full, encoding="utf-8", newline="\n")
    jpath.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    return rec


def report(out: Path) -> Path:
    recs = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(out.glob("*.ocr.json"))]
    lines = ["# OCR — דוח איכות ושער-אינטייק יבש", "",
             f"{len(recs)} קבצים · Tesseract 5.4, heb (tessdata_best), psm 3 · שער הטקסט הדליל: "
             f"{_min_chars_per_page()} תווים/עמוד בממוצע (ingestion.pdf_to_json) · הזיהוי: מספר-הפקודה בעמוד 1 מול שם-הקובץ.",
             "**שום קובץ לא הועבר ל-pdf-ldf_law; אלה טקסטים לתיקיית-המתנה.**", "",
             "| קובץ | עמודים | שכבת-טקסט תווים/עמוד | OCR תווים/עמוד | ספרות/עמוד | רעש | עברית | מס' בעמוד 1 | שם-קובץ | שער-דליל (שכבה→OCR) | זיהוי | שניות |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in recs:
        g = r["gate_sparse_text"]
        lines.append(f"| {r['file']} | {r['pages']} | {r['layer_chars_per_page']} | {r['chars_per_page']} | "
                     f"{r['digits_per_page']} | {r['noise_ratio']:.3f} | {r['hebrew_ratio']:.2f} | "
                     f"{r['order_number_seen'] or '—'} | {r['order_number_filename'] or '—'} | "
                     f"{'✓' if g['layer_passes'] else '✗'}→{'**✓**' if g['ocr_passes'] else '**✗**'} | {r['gate_id']} | {r['elapsed_s']} |")
    passing = [r["file"] for r in recs if r["gate_sparse_text"]["ocr_passes"]]
    failing = [r["file"] for r in recs if not r["gate_sparse_text"]["ocr_passes"]]
    lines += ["", f"**עוברים את שער-הטקסט עם OCR ({len(passing)}):** " + ", ".join(passing),
              f"**לא עוברים ({len(failing)}):** " + (", ".join(failing) or "—"),
              "", "רעש = תווים שאינם אותיות עבריות/לטיניות, ספרות או פיסוק, מכלל התווים; כותרות-עמוד בגופן זעיר "
                  "הן המקור העיקרי. „זיהוי“ = MISMATCH פירושו שמספר-הפקודה שנקרא בעמוד 1 אינו זה שבשם-הקובץ — לבדוק "
                  "לפני קליטה (night.intake.check_ids תופס היפוך-ספרות בכותרת)."]
    p = out / "REPORT.md"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return p


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("inputs", nargs="+")
    ap.add_argument("--out", required=True)
    ap.add_argument("--dpi", type=int, default=300)
    ap.add_argument("--psm", type=int, default=3)
    ap.add_argument("--lang", default="heb")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args(argv)
    out = Path(a.out)
    for guard in ("pdf-ldf_law", "storage"):
        if guard in out.resolve().parts:
            print(f"[ocr] refusing to write under {guard}/ — this is a waiting-folder tool"); return 2
    out.mkdir(parents=True, exist_ok=True)
    if not Path(TESSERACT).exists():
        print(f"[ocr] tesseract not found at {TESSERACT} (set CAI_TESSERACT)"); return 2
    pdfs: list[Path] = []
    for inp in a.inputs:
        p = Path(inp)
        pdfs += sorted(p.glob("*.pdf")) if p.is_dir() else [p]
    print(f"[ocr] {len(pdfs)} files -> {out}  (dpi {a.dpi}, psm {a.psm}, {a.lang})", flush=True)
    for pdf in pdfs:
        try:
            r = process(pdf, out, a.dpi, a.psm, a.lang, a.force)
            g = r["gate_sparse_text"]
            print(f"[ocr] {pdf.name:55} {r['pages']:3}p  {r['chars_per_page']:5} ch/p  noise {r['noise_ratio']:.3f}  "
                  f"gate {'PASS' if g['ocr_passes'] else 'fail'}  id {r['gate_id']}  {r['elapsed_s']}s", flush=True)
        except Exception as e:  # keep going; the report shows what is missing
            print(f"[ocr] {pdf.name}: ERROR {e}", flush=True)
    print(f"[ocr] report -> {report(out)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
