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


# ══ The digit repair, by glyph id (night/PUNCFIX_CRITERION.md, "ההרחבה לספרות") ════════════════════════════════
STD = {17: ".", **{19 + k: str(k) for k in range(10)}}
ANCHORS = {15: ",", 16: "-", 29: ":"}
DIGIT_SALT = "digits-20261001"
FAMILIES_IN_SCOPE = ("Miriam", "David", "FrankRuehl")
TRUST_FOOLING = ["38.0102", "33.0220", "31.0252", "PM-33.0342", "3.0501", "33.0336", "32.0220", "PM-33.0352",
                 "PM-33.0307", "33.0115", "33.1010", "31.0507", "36.0207", "35.0314", "36.0304", "35.0233", "38.0117",
                 "32.0502", "35.0107", "32.0223", "36.0205", "PM-33.0202", "31.0503", "35.0108", "33.0147", "35.0818",
                 "HKA-32-03-10"]


def family(font: str) -> str:
    return re.sub(r"^[A-Z]{6}\+", "", font or "")


def standard_fonts(doc) -> set[str]:
    """Fonts whose glyph order is confirmed standard: >= 2 of the anchors (15 ',', 16 '-', 29 ':') present, all
    mapped as standard."""
    seen: dict[str, Counter] = {}
    for pg in doc:
        for sp in pg.get_texttrace():
            for ucs, gid, _o, _b in sp["chars"]:
                if gid in ANCHORS and ucs > 0:
                    seen.setdefault(sp["font"], Counter())[(gid, chr(ucs))] += 1
    return {f for f, c in seen.items() if len({g for g, _ in c}) >= 2 and all(ch == ANCHORS[g] for g, ch in c)}


def digit_chars(doc) -> list[dict]:
    """Every character of a confirmed standard-order font drawn with glyph 17 or 19..28 that the text layer maps
    to a digit or '.': page, font, gid, the text layer's char, the glyph's own char, its box and baseline origin,
    and its run (a span's consecutive such characters make one number)."""
    std = standard_fonts(doc)
    out = []
    for pno, pg in enumerate(doc):
        for si, sp in enumerate(pg.get_texttrace()):
            if sp["font"] not in std:
                continue
            run = None
            for ci, (ucs, gid, origin, bbox) in enumerate(sp["chars"]):
                ch = chr(ucs) if ucs > 0 else ""
                if gid in STD and (ch.isdigit() or ch == "."):
                    run = run if run is not None else (pno, si, ci)
                    out.append({"page": pno, "font": sp["font"], "gid": gid, "text": ch, "glyph": STD[gid],
                                "origin": origin, "bbox": bbox, "run": run})
                else:
                    run = None
    return out


def numbers(chars: list[dict]) -> list[dict]:
    """The runs as numbers: the text layer's reading and the glyph ids' reading, with the run's box."""
    by: dict[tuple, list[dict]] = {}
    for c in chars:
        by.setdefault(c["run"], []).append(c)
    out = []
    for run, cs in by.items():
        x0 = min(c["bbox"][0] for c in cs); y0 = min(c["bbox"][1] for c in cs)
        x1 = max(c["bbox"][2] for c in cs); y1 = max(c["bbox"][3] for c in cs)
        out.append({"page": run[0], "run": list(run), "text": "".join(c["text"] for c in cs),
                    "glyph": "".join(c["glyph"] for c in cs), "bbox": [x0, y0, x1, y1], "font": cs[0]["font"]})
    return out


def _pdf_orders(store: Path) -> list[dict]:
    return pdf_orders(store)


def cmd_families(store: Path) -> int:
    """Condition 1: for every family in scope, glyphs 17 and 19..28 — 3 crops each, from different orders — as sheets
    for the visual check. The label says which character the glyph id means in the standard order."""
    import fitz
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "families").mkdir(exist_ok=True)
    found: dict[tuple, list] = {}
    for d in _pdf_orders(store):
        pdf = find_pdf(d)
        try:
            doc = fitz.open(pdf)
        except Exception:
            continue
        for c in digit_chars(doc):
            fam = family(c["font"])
            if not fam.startswith(FAMILIES_IN_SCOPE):
                continue
            k = (fam, c["gid"])
            lst = found.setdefault(k, [])
            if len(lst) < 3 and all(x[0] != d["document_id"] for x in lst):
                lst.append((d["document_id"], str(pdf), c))
    fams = sorted({k[0] for k in found})
    for fam in fams:
        crops = []
        for gid in [17] + list(range(19, 29)):
            for did, pdf, c in found.get((fam, gid), []):
                doc = fitz.open(pdf)
                g = {"page": c["page"], "bbox": c["bbox"], "line": [c["bbox"][0] - 60, c["bbox"][1] - 2,
                                                                   c["bbox"][2] + 60, c["bbox"][3] + 2]}
                crops.append((f"{fam} gid {gid} = '{STD[gid]}' (text layer: '{c['text']}') p{c['page'] + 1}",
                              render_crop(doc, g, dpi=400)))
        safe = re.sub(r"[^A-Za-z0-9]+", "_", fam)
        sheet(crops, OUT / "families" / f"{safe}.png")
        print(f"[puncfix] {fam}: {len(crops)} crops -> families/{safe}.png")
    return 0


def cmd_sample_digits(store: Path) -> int:
    """Condition 4 — drawn from the PDFs BEFORE the dry run: numbers whose glyph reading differs from the text layer,
    in the order of sha1(salt|order|page|run); first the first number of each of the first 5 trust-fooling orders
    (by the same hash of the order id), then by order, at most 2 an order, until >= 30 numbers and >= 15 orders."""
    import fitz
    pop = []
    for d in _pdf_orders(store):
        did = d["document_id"]
        try:
            doc = fitz.open(find_pdf(d))
        except Exception:
            continue
        for n in numbers(digit_chars(doc)):
            if n["glyph"] != n["text"] and len(n["glyph"].strip(".")) >= 2:
                h = hashlib.sha1(f"{DIGIT_SALT}|{did}|{n['page']}|{n['run'][1]}|{n['run'][2]}".encode("utf-8")).hexdigest()
                pop.append((h, did, n))
    pop.sort(key=lambda x: x[0])
    fool_first5 = sorted(TRUST_FOOLING, key=lambda o: hashlib.sha1(f"{DIGIT_SALT}|{o}".encode("utf-8")).hexdigest())
    pick, per = [], Counter()
    for o in fool_first5:
        first = next((x for x in pop if x[1] == o), None)
        if first:
            pick.append(first)
            per[o] += 1
        if len(pick) >= 5:
            break
    for x in pop:
        if len(pick) >= 30 and len(per) >= 15:
            break
        if x in pick or per[x[1]] >= 2:
            continue
        pick.append(x)
        per[x[1]] += 1
    rows = [{"n": i + 1, "hash": h[:12], "doc_id": did, "page": n["page"] + 1, "text_layer": n["text"],
             "by_glyph": n["glyph"], "bbox": n["bbox"], "font": family(n["font"]),
             "trust_fooling": did in TRUST_FOOLING} for i, (h, did, n) in enumerate(pick)]
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "digit_sample.json").write_text(json.dumps({"salt": DIGIT_SALT, "population": len(pop), "rows": rows},
                                                      ensure_ascii=False, indent=1), encoding="utf-8")
    docs = {x["document_id"]: x for x in _pdf_orders(store)}
    crops = []
    for r in rows:
        doc = fitz.open(find_pdf(docs[r["doc_id"]]))
        b = r["bbox"]
        g = {"page": r["page"] - 1, "bbox": b, "line": [b[0] - 90, b[1] - 3, b[2] + 90, b[3] + 3]}
        crops.append((f"#{r['n']} p{r['page']} text layer '{r['text_layer']}' -> by glyph '{r['by_glyph']}'",
                      render_crop(doc, g, dpi=400)))
    for i in range(0, len(crops), 10):
        sheet(crops[i:i + 10], OUT / f"digit_sample_{i // 10 + 1}.png")
    print(f"[puncfix] digit sample: {len(rows)} numbers from {len(per)} orders "
          f"({sum(1 for r in rows if r['trust_fooling'])} from the trust-fooling orders); population {len(pop):,}")
    return 0


# the dry run ───────────────────────────────────────────────────────────────────────────────────────────────────
MIN_EQUAL = 20


def page_text_offsets(pg) -> tuple[str, dict[tuple, int]]:
    T = pg.get_text()
    pos, p = {}, 0
    for b in pg.get_text("rawdict")["blocks"]:
        for l in b.get("lines", []):
            for s in l["spans"]:
                for c in s["chars"]:
                    ch, q = c["c"], p
                    while q < len(T) and T[q] != ch and T[q].isspace():
                        q += 1
                    if q < len(T) and T[q] == ch:
                        pos[(round(c["origin"][0], 1), round(c["origin"][1], 1), ch)] = q
                        p = q + 1
    return T, pos


def place(pages: list[str], raw: str) -> list[dict[int, int]]:
    out, at = [], 0
    for T in pages:
        lo = max(0, at - 300)
        seg = raw[lo:at + len(T) + 600]
        sm = difflib.SequenceMatcher(None, T, seg, autojunk=False)
        m, last = {}, None
        for a, b_, size in sm.get_matching_blocks():
            if size >= MIN_EQUAL:
                for k in range(size):
                    m[a + k] = lo + b_ + k
                last = lo + b_ + size
        out.append(m)
        if last is not None:
            at = last
    return out


def repair(d: dict, pdf: Path) -> dict:
    """The dry repair of one order's raw_text: every glyph whose own character differs from the text layer's,
    placed by alignment, replaced only where raw_text holds the text layer's character."""
    import fitz
    raw = d.get("raw_text") or ""
    doc = fitz.open(pdf)
    chars = digit_chars(doc)
    todo = [c for c in chars if c["glyph"] != c["text"]]
    pages, offs = [], []
    for pg in doc:
        T, pos = page_text_offsets(pg)
        pages.append(T)
        offs.append(pos)
    placed = place(pages, raw) if todo else []
    new = list(raw)
    skipped = Counter()
    subs = []
    for c in todo:
        t = offs[c["page"]].get((round(c["origin"][0], 1), round(c["origin"][1], 1), c["text"]))
        if t is None:
            skipped["not in the text layer"] += 1
            continue
        r = placed[c["page"]].get(t)
        if r is None:
            skipped["raw_text differs here"] += 1
            continue
        if raw[r] != c["text"]:
            skipped["raw_text has another char here"] += 1
            continue
        new[r] = c["glyph"]
        subs.append({"page": c["page"] + 1, "r_off": r, "from": c["text"], "to": c["glyph"], "font": family(c["font"])})
    return {"doc_id": d["document_id"], "chars": len(chars), "differ": len(todo), "applied": len(subs),
            "skipped": dict(skipped), "subs": subs, "new_raw": "".join(new)}


def cmd_dry_digits(store: Path) -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "raw").mkdir(exist_ok=True)
    tot, skipped, changed_orders = Counter(), Counter(), []
    for d in _pdf_orders(store):
        pdf = find_pdf(d)
        try:
            r = repair(d, pdf)
        except Exception as e:  # noqa: BLE001
            print(f"[puncfix] {d['document_id']}: ERROR {e!r}"[:200])
            continue
        tot.update({"orders": 1, "chars": r["chars"], "differ": r["differ"], "applied": r["applied"]})
        skipped.update(r["skipped"])
        if r["applied"]:
            changed_orders.append(r["doc_id"])
            (OUT / "raw" / f"{r['doc_id']}.json").write_text(json.dumps(
                {"doc_id": r["doc_id"], "raw_text": r["new_raw"], "subs": r["subs"]}, ensure_ascii=False), encoding="utf-8")
    (OUT / "dry_digits.json").write_text(json.dumps({"total": tot, "skipped": skipped, "changed_orders": changed_orders},
                                                    ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[puncfix] dry (digits): {tot['orders']} PDF orders; {tot['differ']:,} glyphs read differently by id, "
          f"{tot['applied']:,} replaced in raw_text in {len(changed_orders)} orders; skipped {dict(skipped)}")
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["classes", "families", "sample", "dry"])
    ap.add_argument("--store", default=str(ROOT / "storage" / "json_store"))
    a = ap.parse_args(argv)
    if a.cmd == "classes":
        return cmd_classes(Path(a.store))
    if a.cmd == "families":
        return cmd_families(Path(a.store))
    if a.cmd == "sample":
        return cmd_sample_digits(Path(a.store))
    return cmd_dry_digits(Path(a.store))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
