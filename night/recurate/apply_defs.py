# -*- coding: utf-8 -*-
"""Apply a curated-block definition to an existing order — through the gates, $0.

Two def shapes, one path:
  night/recurate/defs/*.json   mode "replace-sections": the listed sections are
                               dropped and one `key-facts` block takes their place
  night/wave7/defs/*.json      mode "block-for-existing-order": the order has no
                               block; `section_id` says key-facts / key-facts-nodigits

Gates, all free: `night.curate.check` (citations against the order's clause
markers, grounded vocabulary, no risk topic the order never mentions, no debris;
digit-free mode for a nodigits block) and `night.numbers` (every number in a
digit block must appear in the order's raw_text). A def with a problem is not
written. `--write` writes storage/json_store/<order>.json and re-indexes it
(local ONNX embeddings, no API); without it nothing changes on disk.

    venv\\Scripts\\python.exe -m night.recurate.apply_defs night/recurate/defs            # dry
    venv\\Scripts\\python.exe -m night.recurate.apply_defs night/wave7/defs --only 36.0313 --write
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from common import safe_print  # noqa: E402
from night import numbers as N  # noqa: E402
from night.curate import DIGIT_FREE_NOTE, check, coverage, support  # noqa: E402
from night.rehearse import doc_path  # noqa: E402
from night.support_audit import numbers_of  # noqa: E402

# Orders the automated curator refuses (night.curate.NEVER). A manual block for
# one of them needs the user's explicit yes — the flag says so, it does not decide.
NEVER = {"20.0502", "3.0502", "33.1010"}


def section_from(defn: dict) -> tuple[dict, list[str], bool]:
    """(section, ids to drop, digit_free)."""
    if defn.get("mode") == "replace-sections":
        # The section title is embedded in every clause chunk ("{doc} — {section}\nסעיף …"), so a
        # new title moves the ranking of clauses whose text did not change: HKA-31-08-01 with the
        # prefixed title lost an order from the ruler window (q00109, 29.09). A def that corrects an
        # existing block in place carries the old title in `section_title`.
        title = defn.get("section_title") or f"עיקרי הפקודה — {defn['title']}"
        return ({"id": "key-facts", "title": title,
                 "clauses": [{"number": c["number"], "text": c["text"]} for c in defn["clauses"]]},
                list(defn.get("replaces_sections", [])), False)
    sid = defn.get("section_id", "key-facts")
    digit_free = sid == "key-facts-nodigits"
    title = defn.get("section_title") or "עיקרי הפקודה"
    if digit_free:
        title = f"{title} [{defn.get('digit_free_note') or DIGIT_FREE_NOTE}]"
    section = {"id": sid, "title": title,
               "clauses": [{"number": c["number"], "text": c["text"]} for c in defn["clauses"]]}
    if digit_free:
        section["digit_free"] = True
    return section, [], digit_free


def seen_numbers(defn: dict) -> dict[str, list[str]]:
    """Per clause: the numbers of its text that the def records in numbers_seen (read on the page).
    Only the intersection — numbers_seen also carries notes („372 in raw_text")."""
    out = {}
    for c in defn.get("clauses", []):
        seen: set[str] = set()
        for note in c.get("numbers_seen", []):
            seen |= set(N.numbers_in(str(note))) | set(re.findall(r"\d+(?:[./:]\d+)*", str(note)))
        # both extractors: night.numbers skips order references („פ"מ 21.0101"), the support gate counts them
        in_text = set(N.numbers_in(c["text"])) | set(numbers_of(c["text"]))
        hit = sorted(x for x in in_text if x in seen)
        if hit:
            out[c["number"]] = hit
    return out


def gate(section: dict, raw: str, digit_free: bool, seen: dict[str, list[str]] | None = None
         ) -> tuple[list[str], list[str], list[tuple]]:
    problems, warnings = check(section, raw, digit_free=digit_free)
    seen = seen or {}
    misses = []
    if not digit_free:
        for c in section["clauses"]:
            # a number read on the page image passes although the scrambled raw_text lacks it
            m = [x for x in N.numbers_in(c["text"]) if not N.present(x, raw) and x not in seen.get(c["number"], [])]
            if m:
                misses.append((c["number"][:40], m))
    return problems, warnings, misses


def recurated_record(doc: dict, defn: dict, def_name: str, drop: list, seen: dict[str, list[str]]) -> dict:
    """Merge, never overwrite: another wave's notes (v161's page_verified_numbers) live here too.
    The def's page-read numbers are recorded so the audits do not count them (coverage_audit.page_verified)."""
    rec = dict(doc["recurated"]) if isinstance(doc.get("recurated"), dict) else {}
    rec.update({"when": defn.get("when") or "2026-09-22", "def": def_name, "replaced": drop})
    if seen:
        rec["page_verified_numbers"] = list(rec.get("page_verified_numbers", [])) + [
            {"clause": k, "numbers": v, "def": def_name} for k, v in seen.items()]
    return rec


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("defs_dir")
    ap.add_argument("--only", action="append", default=[], help="document_id(s) to apply")
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()
    files = sorted(Path(args.defs_dir).glob("*.json"))
    rc = 0
    written = []
    for f in files:
        defn = json.loads(f.read_text(encoding="utf-8"))
        did = defn.get("document_id")
        if not did or (args.only and did not in args.only):
            continue
        try:
            path = doc_path(did)
        except Exception as e:  # noqa: BLE001
            safe_print(f"[apply] {did}: not in json_store ({type(e).__name__})"); rc = 1; continue
        doc = json.loads(path.read_text(encoding="utf-8"))
        section, drop, digit_free = section_from(defn)
        seen = seen_numbers(defn)
        problems, warnings, misses = gate(section, doc["raw_text"], digit_free, seen)
        # the coverage gate judges the block as it will be served: the kept sections plus this one
        kept = [s for s in doc.get("sections", []) if s["id"] not in drop]
        have = {c["number"] for s in kept if s["id"] == section["id"] for c in s["clauses"]}
        served = {"clauses": [c for s in kept for c in s["clauses"]]
                  + [c for c in section["clauses"] if c["number"] not in have]}
        cp, cw = coverage(served, doc["raw_text"], doc.get("title") or "", defn.get("omitted_on_purpose"))
        problems += cp
        warnings += cw
        # the support gate judges the def's OWN clauses (the existing ones are held by the ratchet)
        sp, sw = support(section, doc["raw_text"], did, seen)
        problems += sp
        warnings += sw
        words = [len(c["text"].split()) for c in section["clauses"]]
        flag = " ⚠ NEVER — needs the user's yes" if did in NEVER else ""
        safe_print(f"[apply] {did:<11} {section['id']:<19} {len(section['clauses'])} clauses, words max {max(words)}, "
                   f"problems {len(problems)}, warnings {len(warnings)}, number misses {len(misses)}{flag}")
        for x in problems:
            safe_print(f"          PROBLEM {x[:150]}")
        for x in cw:
            safe_print(f"          COVER?  {x[:150]}")
        for x in sw:
            safe_print(f"          SUPPORT? {x[:150]}")
        for x in misses:
            safe_print(f"          NUMBER  {x}")
        if problems or misses:
            rc = 1
            continue
        if not args.write:
            continue
        if did in NEVER and not defn.get("user_approved_never"):
            safe_print(f"          not written: {did} is in NEVER and the def carries no user_approved_never")
            rc = 1
            continue
        # a kept section with the same id (an existing key-facts) merges: its clauses first, the new ones after
        same = [s for s in doc.get("sections", []) if s["id"] == section["id"] and s["id"] not in drop]
        if same:
            have = {c["number"] for s in same for c in s["clauses"]}
            section["clauses"] = [c for s in same for c in s["clauses"]] + [c for c in section["clauses"] if c["number"] not in have]
        doc["sections"] = [s for s in doc.get("sections", []) if s["id"] not in drop and s["id"] != section["id"]] + [section]
        doc["recurated"] = recurated_record(doc, defn, f.name, drop, seen)
        path.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
        from storage import vector_store as vs
        n = vs.index_document(doc, save_cache=True)
        safe_print(f"          WRITTEN {path.name} — re-indexed {n} chunks")
        written.append(did)
    if args.write:
        safe_print(f"[apply] written: {written or 'nothing'} — now the corpus gates (README step 4)")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
