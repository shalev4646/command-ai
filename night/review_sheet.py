"""Render the curated key-facts as a human review sheet.

The automated gates catch invention — a citation to a clause that does not
exist, vocabulary foreign to the order, a topic the order never raises. They
cannot catch a clause that is well-grounded and still WRONG: a misread
threshold, an approver at the wrong rank, a condition attached to the wrong
case. That is what a person has to check, and it is the only thing standing
between this branch and a merge.

So the sheet is built for checking, not for reading: every clause sits next to
the clause numbers it claims to come from, and the order's raw text is linked
so a reviewer can jump straight to them.
"""
from __future__ import annotations

import json

from night import config as C
from night.curate import cited_numbers, clause_numbers_in_raw, is_numbered
from night.rehearse import doc_path

OUT = C.ROOT / "REVIEW_key_facts.md"


def build() -> None:
    acc = C.read_jsonl(C.OUT / "curate_accepted.jsonl")
    latest = {a["doc_id"]: a for a in acc}          # last write per order wins
    if not latest:
        raise SystemExit("no curate_accepted.jsonl")

    md = ["# סקירת תוכן — סעיפי key-facts שנכתבו הלילה", "",
          f"‏{len(latest)} פקודות. **זה החוסם היחיד למיזוג.**", "",
          "השערים האוטומטיים תפסו המצאה: ציטוט לסעיף שלא קיים, אוצר-מילים זר לפקודה, "
          "נושא שהפקודה שותקת בו. הם **לא** יכולים לתפוס סעיף מעוגן היטב שפשוט שגוי — "
          "סף שנקרא לא נכון, מאשר בדרגה לא נכונה, תנאי שהוצמד למקרה הלא-נכון. "
          "זה מה שצריך עין אנושית.", "",
          "לכל סעיף מופיעים מספרי הסעיפים שהוא מצטט, כדי שאפשר יהיה לקפוץ אליהם בפקודה.", "",
          "---", ""]

    for doc_id in sorted(latest):
        entry = latest[doc_id]
        sec = entry["section"]
        try:
            doc = json.loads(doc_path(doc_id).read_text(encoding="utf-8"))
        except FileNotFoundError:
            continue
        raw = str(doc.get("raw_text", ""))
        real = clause_numbers_in_raw(raw)
        numbered = is_numbered(raw)

        md += [f"## {doc_id} — {doc.get('title','')}", "",
               f"*{sec.get('title','')}*", "",
               f"מקור: `{doc_path(doc_id).name}` · {len(raw.split()):,} מילים גולמיות · "
               + ("ממוספרת" if numbered else "**לא ממוספרת — אין ציטוטים לאמת מולם**")
               + (f" · {'recovered' if entry.get('recovered') else 'Opus'}"), ""]
        if entry.get("warnings"):
            md += [f"> ⚠ {len(entry['warnings'])} אזהרות: "
                   + "; ".join(w.split(":")[0] for w in entry["warnings"][:4]), ""]

        for cl in sec.get("clauses", []):
            cites = sorted(cited_numbers(cl.get("text", "")))
            tag = (f"סעיפים {', '.join(map(str, cites))}" if cites
                   else ("⚠ בלי ציטוט" if numbered else "—"))
            bad = [c for c in cites if c not in real]
            if bad:
                tag += f"  ⛔ לא קיימים: {bad}"
            md += [f"### {cl.get('number','')}", "",
                   f"`{tag}`", "", cl.get("text", ""), ""]
        md += ["---", ""]

    OUT.write_text("\n".join(md), encoding="utf-8")
    C.log(f"[review] wrote {OUT} ({len(latest)} orders)")


if __name__ == "__main__":
    build()
