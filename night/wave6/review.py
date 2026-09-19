# -*- coding: utf-8 -*-
"""wave-6 review sheet: every hand-written clause beside the source text it came from.

For each clause, the stretch of raw_text that shares the most words with it is
printed under it, so a reader can check faithfulness without opening the PDF.
Pure text, no model.

    venv\\Scripts\\python.exe -m night.wave6.review   ->  night/wave6/REVIEW_wave6.md
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
STORE = ROOT / "storage" / "json_store"


def _words(s: str) -> list[str]:
    return [w for w in re.sub(r'[^\w\s"]', " ", s).split() if len(w) >= 3]


def best_stretch(raw: str, clause: str, span: int = 70) -> tuple[float, str]:
    rw = re.sub(r"\s+", " ", raw).split(" ")
    cw = set(_words(clause))
    best, at = 0.0, 0
    for i in range(0, max(1, len(rw) - span), 8):
        sw = set(_words(" ".join(rw[i:i + span])))
        score = len(cw & sw) / max(1, len(cw))
        if score > best:
            best, at = score, i
    return best, " ".join(rw[at:at + span])


def main() -> int:
    ids = {json.loads(f.read_text(encoding="utf-8"))["document_id"] for f in (HERE / "defs").glob("*.json")}
    out = ["# wave-6 — גיליון-עיון: כל סעיף שנכתב ביד מול הטקסט שממנו נגזר", "",
           "לכל סעיף: הכותרת, הנוסח שכתבתי, ומתחתיו קטע-המקור שחולק איתו הכי הרבה מילים.",
           "מה לחפש: מילה שמשנה משמעות (רשאי מול חייב), תנאי שנשמט, או קביעה שאין במקור.", ""]
    for p in sorted(STORE.glob("*.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        if d.get("document_id") not in ids:
            continue
        out += [f"## {d['title']}", "", f"- מזהה: `{d['document_id']}` · תווית-מקור: {d.get('civil_label','')}",
                f"- אורך המקור: {len(d['raw_text'].split())} מילים · תפקידים: {', '.join(d['roles'])}", ""]
        for cl in d["sections"][0]["clauses"]:
            score, src = best_stretch(d["raw_text"], cl["text"])
            out += [f"### {cl['number']}", "", f"**נכתב:** {cl['text']}", "",
                    f"**המקור ({score:.0%} מהמילים משותפות):** {src}", ""]
    (HERE / "REVIEW_wave6.md").write_text("\n".join(out), encoding="utf-8")
    print("written", HERE / "REVIEW_wave6.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
