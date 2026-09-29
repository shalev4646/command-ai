# -*- coding: utf-8 -*-
"""Do the test targets' verified quotes survive a corpus write? (free, no API)

Every free instrument drops a target whose verified quote no indexed chunk contains
(night/sectprobe.py `targets()`: counting it "would charge retrieval with chunking's sins"). So a
write that deletes the only chunk carrying a quote never shows as a loss: the denominator shrinks
and "zero lost" still passes. On 29.09 two replace-sections definitions would have removed 20
such quotes, held ones among them; this counts them before the write, or after it.

    python -m night.quote_survival defs night/recurate/defs_w11/HKA-31-08-01.json ...
    python -m night.quote_survival store --against main

`defs` simulates each definition the way `night.recurate.apply_defs --write` would apply it, against
this tree's json_store. `store` compares this tree's json_store with the one at a git ref, doc by
doc. A quote survives when some chunk still contains it after the same normalization the
instruments use; chunks are built like `vector_store.index_document` builds them (raw windows and
curated clauses; the question anchors carry no order text and are left out). Held targets —
fresh_v3, split "held", or a file named *held* — are counted, never listed. Exit 1 if any quote
is lost.
"""
from __future__ import annotations

import argparse
import glob
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def norm(s: str) -> str:
    """night.sectprobe._norm, copied so this tool never imports backend."""
    s = re.sub(r'["״׳\'“”‘’]', "", s or "")
    return re.sub(r"\s+", " ", s).strip()


def doc_chunks(doc: dict) -> list[str]:
    """The normalized texts `vector_store.index_document` indexes for a document."""
    from storage.vector_store import _split_raw_text
    did, title = doc.get("document_id", "unknown"), doc.get("title", "")
    out = [norm(c["text"]) for c in _split_raw_text(doc["raw_text"], did, title)] if doc.get("raw_text") else []
    for s in doc.get("sections", []):
        st = s.get("title", s.get("id", ""))
        for c in s.get("clauses", []):
            text = (c.get("text") or "").strip()
            if text:
                out.append(norm(f"{title} — {st}\nסעיף {c.get('number', '')}: {text}"))
    return out


def after_write(doc: dict, defn: dict) -> dict:
    """The document as `apply_defs --write` leaves it (the section merge of its write path)."""
    from night.recurate.apply_defs import section_from
    section, drop, _ = section_from(defn)
    section = {**section, "clauses": list(section["clauses"])}
    same = [s for s in doc.get("sections", []) if s["id"] == section["id"] and s["id"] not in drop]
    if same:
        have = {c["number"] for s in same for c in s["clauses"]}
        section["clauses"] = [c for s in same for c in s["clauses"]] + \
                             [c for c in section["clauses"] if c["number"] not in have]
    new = dict(doc)
    new["sections"] = [s for s in doc.get("sections", []) if s["id"] not in drop and s["id"] != section["id"]] + [section]
    return new


def load_targets(root: Path = ROOT) -> list[tuple[str, dict, bool]]:
    """(file, row, held) for every row in the tree that carries verified quotes."""
    out = []
    for f in sorted(glob.glob(str(root / "night" / "**" / "*.json"), recursive=True)):
        rel = Path(f).relative_to(root).as_posix()
        if "/out/" in rel and not Path(f).name.startswith("adjudication"):
            continue
        try:
            rows = json.loads(Path(f).read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            continue
        if not (isinstance(rows, list) and rows and isinstance(rows[0], dict) and "verified_quotes" in rows[0]):
            continue
        held_file = "fresh_v3" in rel or "held" in Path(f).name
        for r in rows:
            if isinstance(r, dict) and r.get("doc_id") and r.get("verified_quotes"):
                out.append((rel, r, held_file or r.get("split") == "held"))
    return out


def lost(before: list[str], after: list[str], targets: list[tuple[str, dict, bool]]) -> tuple[list, list]:
    """(dropped, damaged): targets that had a quote in `before` and now have none / have fewer."""
    dropped, damaged = [], []
    for f, r, held in targets:
        qs = [norm(q) for q in r["verified_quotes"] if norm(q)]
        was = [q for q in qs if any(q in c for c in before)]
        now = [q for q in was if any(q in c for c in after)]
        if was and not now:
            dropped.append((f, r.get("id"), held))
        elif len(now) < len(was):
            damaged.append((f, r.get("id"), held))
    return dropped, damaged


def _report(doc_id: str, dropped: list, damaged: list) -> int:
    n = 0
    for kind, rows in (("dropped from the instruments", dropped), ("lost a quote", damaged)):
        dev = sorted(f"{Path(f).name}:{i}" for f, i, h in rows if not h)
        held = sum(1 for *_, h in rows if h)
        if dev or held:
            print(f"[survival] {doc_id}: {kind} — {', '.join(dev) or '-'}" + (f" + held {held}" if held else ""))
        n += len(rows)
    return n


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="mode", required=True)
    d = sub.add_parser("defs")
    d.add_argument("files", nargs="+")
    s = sub.add_parser("store")
    s.add_argument("--against", required=True)
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    from night.rehearse import doc_path

    targets = load_targets()
    by_doc: dict[str, list] = {}
    for t in targets:
        by_doc.setdefault(t[1]["doc_id"], []).append(t)
    total = checked = 0
    if args.mode == "defs":
        for f in args.files:
            defn = json.loads(Path(f).read_text(encoding="utf-8"))
            did = defn["document_id"]
            doc = json.loads(Path(doc_path(did)).read_text(encoding="utf-8"))
            dr, dm = lost(doc_chunks(doc), doc_chunks(after_write(doc, defn)), by_doc.get(did, []))
            total += _report(did, dr, dm)
            checked += 1
    else:
        store = ROOT / "storage" / "json_store"
        names = subprocess.run(["git", "-c", "core.quotepath=off", "diff", "--name-only", args.against, "--", "storage/json_store"],
                               cwd=ROOT, capture_output=True, text=True, encoding="utf-8").stdout.split()
        for rel in names:
            p = ROOT / rel
            old = subprocess.run(["git", "show", f"{args.against}:{rel}"], cwd=ROOT, capture_output=True)
            if old.returncode != 0 or not p.exists():
                continue           # an added or deleted order: nothing that existed was rewritten
            before = json.loads(old.stdout.decode("utf-8"))
            now = json.loads(p.read_text(encoding="utf-8"))
            did = now.get("document_id")
            dr, dm = lost(doc_chunks(before), doc_chunks(now), by_doc.get(did, []))
            total += _report(did, dr, dm)
            checked += 1
    print(f"[survival] {checked} order(s), {len(targets)} targets with quotes: {total} target(s) lose a quote")
    return 1 if total else 0


if __name__ == "__main__":
    raise SystemExit(main())
