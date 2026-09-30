# -*- coding: utf-8 -*-
"""Punctuation coded as a digit — a free, deterministic repair from the PDF's own glyphs (night/PUNCFIX_CRITERION.md).
DRY RUN ONLY: writes under night/out/puncfix/, never the corpus; the corpus write is session A's, as its own wave.

    venv\\Scripts\\python.exe -m night.puncfix classes            # the glyph classes + crop sheets for the visual check
    venv\\Scripts\\python.exe -m night.puncfix dry                # the substitutions on raw_text, with the verified map
    venv\\Scripts\\python.exe -m night.puncfix sample             # the pre-registered verification sample, as crop sheets

The defect: some PDFs' character maps code a punctuation glyph as a digit — 33.0220's period reads "2", 33.0145's "1"
(33.0145: "03.06.1979" -> "0310611979"). The text cannot tell such a "2" from a real one; the glyph box can: a real
digit is as wide as its font's other digits, a period, comma or colon drawn from the same font about half that
(night/cleantext/pagegate.py; the corpus survey 30.09: a clean gap at 0.36-0.40 em). A CLASS is one (order, font,
glyph id) — ONE glyph of one embedded font, drawn the same every time (the glyph id comes from the PDF's text
trace, page.get_texttrace(); the width alone could not tell a period from a colon of the same advance). Each class
is identified once, by eye, from its crops (the map, night/puncfix_map.json); a class whose shape is unclear is not
repaired. The ink test (ink_shape) is a reading aid only: on 30.0117 it calls 39 of one glyph's 242 occurrences a
"colon" — a neighbour's ink inside the clip — so it never decides anything.

The stored raw_text is the PDF's text layer (8 of the 59 identical, 50 above 0.98), so every glyph is placed in it by
alignment: a substitution is made only where the raw_text around it is the PDF's own text, unchanged — never inside
a stretch that digit readings (digits_fixed) or anything else rewrote.
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

OUT = ROOT / "night" / "out" / "puncfix"
MAP = ROOT / "night" / "puncfix_map.json"
PDF_DIRS = ("pdf-ldf_law", "pdf-hka", "pdf-law")
RATIO = 0.75
WORD_END_DIGIT = re.compile(r"[א-ת]{2,}\d(?=[\s,;:)]|$)", re.M)
SAMPLE_SALT = "puncfix-20260930"
SAMPLE_N, SAMPLE_ORDERS = 30, 15


def find_pdf(doc: dict) -> Path | None:
    src = doc.get("source_file") or ""
    for base in (Path("D:/app_soldier"), ROOT):
        for d in PDF_DIRS:
            p = base / d / src
            if src and p.exists():
                return p
    return None


def reference_width(ws: list[float]) -> float:
    """A font's real digit width in this PDF — the most common width among its digits of at least 0.40 em, or of all
    of them for a condensed face (night/cleantext/pagegate.py)."""
    wide = [round(w, 2) for w in ws if w >= 0.40]
    return Counter(wide or [round(w, 2) for w in ws]).most_common(1)[0][0]


def pdf_orders(store: Path) -> list[dict]:
    """Every order whose served text is its PDF's text layer: not web-sourced, not OCR, with the PDF on disk. The
    repair is per glyph class wherever it occurs; the survey's 59 are where nearly all of them are."""
    docs = []
    web = set()
    wf = ROOT / "night" / "recurate" / "web_text_orders.json"
    if wf.exists():
        web = {r["document_id"] for r in json.loads(wf.read_text(encoding="utf-8"))}
    for p in sorted(store.glob("*.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        if d["document_id"] in web or d.get("ingested_from_text") or find_pdf(d) is None:
            continue
        docs.append(d)
    return docs


# ── glyphs ───────────────────────────────────────────────────────────────────────────────────────────────────────
def glyphs(pdf: Path) -> tuple[list[dict], dict[str, float]]:
    """Every character mapped to an ASCII digit, with its page, box, baseline origin, font and width (em); and each
    font's reference digit width in this PDF."""
    import fitz
    doc = fitz.open(pdf)
    out, widths = [], defaultdict(list)
    for pno, pg in enumerate(doc):
        for b in pg.get_text("rawdict")["blocks"]:
            for l in b.get("lines", []):
                for s in l["spans"]:
                    if s["size"] <= 0:
                        continue
                    for i, c in enumerate(s["chars"]):
                        if c["c"].isascii() and c["c"].isdigit():
                            w = (c["bbox"][2] - c["bbox"][0]) / s["size"]
                            widths[s["font"]].append(w)
                            near = "".join(x["c"] for x in s["chars"][max(0, i - 1):i + 2])
                            out.append({"page": pno, "font": s["font"], "code": c["c"], "w": w, "bbox": c["bbox"],
                                        "origin": c["origin"], "size": s["size"], "near": near, "line": l["bbox"]})
    ref = {f: reference_width(ws) for f, ws in widths.items()}
    gid_at = {}
    for pno, pg in enumerate(doc):
        for sp in pg.get_texttrace():
            for ucs, gid, origin, _bbox in sp["chars"]:
                if 48 <= ucs <= 57:
                    gid_at[(pno, round(origin[0], 1), round(origin[1], 1))] = gid
    for g in out:
        g["narrow"] = g["w"] < RATIO * ref[g["font"]]
        g["gid"] = gid_at.get((g["page"], round(g["origin"][0], 1), round(g["origin"][1], 1)))
        g["cls"] = f"{g['font']}|gid{g['gid']}|{g['code']}"
    return out, ref


def ink_shape(doc, g: dict) -> str:
    """The glyph's ink, read off the page at 600 dpi, relative to the baseline and the font size: 'period' (one dot
    at the baseline), 'comma' (one mark reaching below it), 'colon' (two dots stacked), 'semicolon', 'dash' (a flat
    bar above the baseline), 'high' (marks at quote height), or 'other'."""
    import fitz
    pg = doc[g["page"]]
    x0, y0, x1, y1 = g["bbox"]
    base, size = g["origin"][1], g["size"]
    clip = fitz.Rect(x0, base - size, x1, base + 0.45 * size)
    pix = pg.get_pixmap(dpi=600, clip=clip, colorspace=fitz.csGRAY)
    w, h, buf = pix.width, pix.height, pix.samples
    ink = [[buf[yy * w + xx] < 128 for xx in range(w)] for yy in range(h)]
    seen, comps = set(), []
    for yy in range(h):
        for xx in range(w):
            if ink[yy][xx] and (yy, xx) not in seen:
                stack, pts = [(yy, xx)], []
                seen.add((yy, xx))
                while stack:
                    a, b_ = stack.pop()
                    pts.append((a, b_))
                    for da, db in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        na, nb = a + da, b_ + db
                        if 0 <= na < h and 0 <= nb < w and ink[na][nb] and (na, nb) not in seen:
                            seen.add((na, nb))
                            stack.append((na, nb))
                if len(pts) >= 6:
                    comps.append(pts)
    if not comps:
        return "blank"
    px_per_pt = h / (1.45 * size)
    base_px = size * px_per_pt                       # the baseline's row inside the clip

    def box(pts):
        ys, xs = [p[0] for p in pts], [p[1] for p in pts]
        return min(ys), max(ys), min(xs), max(xs)
    boxes = sorted((box(p) for p in comps), key=lambda b_: b_[0])
    tol = 0.08 * size * px_per_pt
    if len(boxes) == 1:
        top, bot, left, right = boxes[0]
        hgt, wid = bot - top, right - left
        if bot > base_px + tol:
            return "comma"
        if top < base_px - 0.55 * size * px_per_pt:
            return "high"
        if wid > 1.6 * max(hgt, 1) and bot < base_px - tol:
            return "dash"
        return "period"
    if len(boxes) == 2:
        (t1, b1, _, _), (t2, b2, _, _) = boxes
        if b1 < t2:                                   # stacked
            return "semicolon" if b2 > base_px + tol else "colon"
        return "high" if b2 < base_px - 0.55 * size * px_per_pt else "other"
    return "other"


def classes_of(did: str, pdf: Path, n_crops: int = 3) -> dict[str, dict]:
    import fitz
    gl, ref = glyphs(pdf)
    doc = fitz.open(pdf)
    by = defaultdict(list)
    for g in gl:
        if g["narrow"]:
            by[g["cls"]].append(g)
    out = {}
    for cls, gs in by.items():
        shapes = Counter(ink_shape(doc, g) for g in gs)
        step = max(1, len(gs) // n_crops)
        out[cls] = {"doc_id": did, "n": len(gs), "in_number": sum(1 for g in gs if re.search(r"\d", g["near"].replace(g["code"], "", 1))),
                    "shapes": dict(shapes), "ref": ref[gs[0]["font"]], "examples": gs[::step][:n_crops]}
    return out


def render_crop(doc, g: dict, dpi: int = 300):
    """The glyph's line around it, the glyph boxed in red — a Pixmap."""
    import fitz
    pg = doc[g["page"]]
    r = fitz.Rect(g["bbox"])
    line = fitz.Rect(g["line"])
    shape = pg.new_shape()
    shape.draw_rect(r + (-0.6, -0.6, 0.6, 0.6))
    shape.finish(color=(1, 0, 0), width=0.7)
    shape.commit(overlay=True)
    clip = fitz.Rect(max(line.x0, r.x0 - 110), min(line.y0, r.y0) - 3, min(line.x1, r.x1 + 110), max(line.y1, r.y1) + 3)
    return pg.get_pixmap(dpi=dpi, clip=clip)


def sheet(crops: list[tuple[str, object]], path: Path) -> None:
    """Stack the crops into one PNG, each under a short ASCII label (the index .txt beside it gives the full one)."""
    from PIL import Image, ImageDraw
    ims = [(lab, Image.frombytes("RGB", (p.width, p.height), (p if p.n == 3 else __import__("fitz").Pixmap(
        __import__("fitz").csRGB, p)).samples)) for lab, p in crops]
    lab_h, pad = 16, 4
    width = max(max(im.width for _, im in ims), 420) + 2 * pad
    height = sum(im.height + lab_h + pad for _, im in ims) + pad
    out = Image.new("RGB", (width, height), "white")
    d = ImageDraw.Draw(out)
    y = pad
    for lab, im in ims:
        d.text((pad, y), lab, fill=(0, 0, 160))
        y += lab_h
        out.paste(im, (pad, y))
        y += im.height + pad
        d.line([(0, y - pad // 2), (width, y - pad // 2)], fill=(200, 200, 200))
    out.save(str(path))


def cmd_classes(store: Path) -> int:
    import fitz
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "sheets").mkdir(exist_ok=True)
    docs = pdf_orders(store)
    allc, rows = {}, []
    for d in docs:
        pdf = find_pdf(d)
        try:
            cl = classes_of(d["document_id"], pdf)
        except Exception as e:  # noqa: BLE001
            print(f"[puncfix] {d['document_id']}: ERROR {e!r}"[:200])
            continue
        # the 59: the survey's order-level signal on the PDF text layer
        text = "".join(pg.get_text() for pg in fitz.open(pdf))
        rate = len(WORD_END_DIGIT.findall(text)) * 1000 / max(len(text.split()), 1)
        for cls, c in cl.items():
            allc[f"{d['document_id']}|{cls}"] = {**c, "order_rate": round(rate, 1), "pdf": str(pdf)}
    (OUT / "classes.json").write_text(json.dumps({k: {kk: vv for kk, vv in v.items() if kk != "examples"}
                                                 for k, v in allc.items()}, ensure_ascii=False, indent=1),
                                      encoding="utf-8")
    # crop sheets, 6 classes a sheet, 3 crops a class
    keys = sorted(allc)
    index = []
    n = 0
    for i in range(0, len(keys), 6):
        crops = []
        n += 1
        for ci, k in enumerate(keys[i:i + 6]):
            c = allc[k]
            doc = fitz.open(c["pdf"])
            tag = f"S{n:02d}-C{ci + 1}"
            index.append({"tag": tag, "class": k, "n": c["n"], "in_number": c["in_number"], "shapes": c["shapes"]})
            for j, g in enumerate(c["examples"]):
                crops.append((f"{tag} [{j + 1}/{len(c['examples'])}] code '{g['code']}' w={g['w']:.2f}/{c['ref']:.2f}em "
                              f"p{g['page'] + 1}", render_crop(doc, g)))
        sheet(crops, OUT / "sheets" / f"classes_{n:02d}.png")
    (OUT / "sheets" / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[puncfix] {len(allc)} narrow-glyph classes in {len({v['doc_id'] for v in allc.values()})} orders "
          f"({sum(v['n'] for v in allc.values()):,} glyphs, {sum(v['in_number'] for v in allc.values()):,} inside a "
          f"number) -> {OUT / 'classes.json'}; crops in {OUT / 'sheets'}")
    mixed = {k: v["shapes"] for k, v in allc.items() if len(v["shapes"]) > 1}
    print(f"[puncfix] classes whose occurrences do not share one ink shape: {len(mixed)}")
    for k, v in sorted(mixed.items())[:20]:
        print(f"   {k}: {v}")
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["classes", "dry", "sample"])
    ap.add_argument("--store", default=str(ROOT / "storage" / "json_store"))
    a = ap.parse_args(argv)
    if a.cmd == "classes":
        return cmd_classes(Path(a.store))
    raise SystemExit(f"{a.cmd}: not yet — after the class map is verified (night/PUNCFIX_CRITERION.md)")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
