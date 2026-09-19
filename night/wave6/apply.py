# -*- coding: utf-8 -*-
"""wave-6: hand-curated sources, applied through the project's own gates. $0.

Each file in defs/ is a hand-written definition: where the PDF is, where the
instruction starts and ends inside the FOI reply (the cover letter is cut), and
the curated clauses, written close to the source wording. Nothing here calls a
model. What decides whether a document enters is mechanical and the same as for
every curated order:

  night.curate.check     citations exist in raw_text, vocabulary is grounded,
                         no risk topic the source never mentions, no debris
  night.digits           a source whose digits nothing vouches for gets a
                         digit-free block (section id key-facts-nodigits)

A document with a problem is NOT written. PDFs go to pdf-hka/, never to
pdf-ldf_law/ (the boot-time ingest scans that folder through the paid API).

    venv\\Scripts\\python.exe -m night.wave6.apply            # dry: gates only
    venv\\Scripts\\python.exe -m night.wave6.apply --write    # write + index
"""
from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from common import safe_print  # noqa: E402
from ingestion import pdf_to_json as P  # noqa: E402
from night import digits  # noqa: E402
from night.curate import DIGIT_FREE_NOTE, check  # noqa: E402

HERE = Path(__file__).resolve().parent
PENDING = Path(r"D:\app_soldier\sources_pending\foi-released")
STORE = ROOT / "storage" / "json_store"
PDF_DIR = ROOT / "pdf-hka"


def _slug(title: str) -> str:
    s = re.sub(r'["״׳\'()—–,.:/\\]', "", title)
    return re.sub(r"\s+", "-", s).strip("-")[:60]


def build(defn: dict) -> tuple[dict, list[str], list[str]]:
    raw_all = P.extract_text(PENDING / defn["source_pdf"])
    a = raw_all.find(defn["cut_start"])
    if a < 0:
        return {}, [f"cut_start not found: {defn['cut_start']!r}"], []
    b = raw_all.find(defn["cut_end"], a) if defn.get("cut_end") else -1
    raw = raw_all[a:b] if b > a else raw_all[a:]
    # `drop_ranges`: chapters of the source that no asker needs and that only add
    # ranking competition (karpar-300.001's internal committee chapters pulled a
    # profile-committee question off its order — gate 390 -> 389, 2026-09-19).
    for start, end in defn.get("drop_ranges") or []:
        i, j = raw.find(start), raw.find(end)
        if i < 0 or j <= i:
            return {}, [f"drop_range markers not found in order: {start!r} .. {end!r}"], []
        raw = raw[:i] + raw[j:]

    doc = {
        "document_id": defn["document_id"], "title": defn["title"],
        "published": defn.get("published"), "raw_text": raw,
        "source_file": defn["pdf_name"], "source_url": defn.get("source_url", ""),
        "suggested_questions": defn.get("suggested_questions") or {},
        "roles": defn["roles"], "anchor_questions": defn.get("anchor_questions") or [],
        "questions_curated": True, "civil_label": defn["civil_label"],
    }
    # `force_digit_free`: the source's digits may be sound and its NUMBERS still
    # unfit to state — hka-32-03-10 is the May-2009 text and the army said in 2021
    # it was under revision. The block then carries structure only, and the note
    # says why (the default note would wrongly blame the extraction).
    trusted = digits.trustworthy(doc) and not defn.get("force_digit_free")
    section = {"id": "key-facts", "title": f"עיקרי המסמך — {defn['title']}",
               "clauses": defn["clauses"]}
    problems, warnings = check(section, raw, digit_free=not trusted)
    if not trusted:
        section["id"] = "key-facts-nodigits"
        section["digit_free"] = True
        section["title"] = f"{section['title']} [{defn.get('digit_free_note') or DIGIT_FREE_NOTE}]"
    doc["sections"] = [section]
    doc["_wave"] = "wave6-2026-09-19 hand-curated"
    safe_print(f"[wave6] {defn['document_id']}: raw {len(raw.split())} words, digits "
               f"{'trusted' if trusted else 'NOT vouched -> digit-free block'}, "
               f"{len(defn['clauses'])} clauses, {len(problems)} problems, {len(warnings)} warnings")
    return doc, problems, warnings


def main(write: bool) -> int:
    bad = 0
    built = []
    for f in sorted((HERE / "defs").glob("*.json")):
        defn = json.loads(f.read_text(encoding="utf-8"))
        doc, problems, warnings = build(defn)
        for p in problems:
            safe_print(f"   PROBLEM  {p}")
        for w in warnings:
            safe_print(f"   warning  {w}")
        bad += bool(problems)
        built.append((defn, doc))
    if bad:
        safe_print(f"[wave6] {bad} document(s) failed the gates — nothing written.")
        return 1
    if not write:
        safe_print("[wave6] dry run — gates passed, nothing written. Use --write.")
        return 0
    import storage.vector_store as vs
    PDF_DIR.mkdir(exist_ok=True)
    for defn, doc in built:
        shutil.copyfile(PENDING / defn["source_pdf"], PDF_DIR / defn["pdf_name"])
        out = STORE / f"{_slug(defn['title'])}.json"
        out.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
        n = vs.index_document(doc)
        safe_print(f"[wave6] wrote {out.name} and indexed {n} chunks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main("--write" in sys.argv))
