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
import re
import statistics as st

import backend
from night import config as C
from night.curate import cited_numbers, clause_numbers_in_raw, is_numbered
from night.rehearse import doc_path

OUT = C.ROOT / "REVIEW_key_facts.md"

# Digits fused into Hebrew words — the signature of this corpus' OCR failure.
# 20.0502 reads "או0מאסר ב0 תקופת", "ז0כאי", "תוקף סעיפים6 עד7 מה- 0 נ ביו י3..0".
# An order that damaged cannot be curated reliably, and its clause citations
# cannot be verified because the numbers are not recoverable from the text.
_GLUED = re.compile(r"[א-ת]\d|\d[א-ת]")


def ocr_rates() -> tuple[dict[str, float], float]:
    rates = {}
    for d in backend.load_documents():
        raw, doc_id = str(d.get("raw_text", "")), d.get("document_id")
        w = len(raw.split())
        if doc_id and w:
            rates[doc_id] = len(_GLUED.findall(raw)) * 1000 / w
    return rates, st.median(rates.values())


def _tbl(head, rows) -> str:
    lines = ["| " + " | ".join(head) + " |",
             "|" + "|".join("---" for _ in head) + "|"]
    lines += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(lines)


def build() -> None:
    acc = C.read_jsonl(C.OUT / "curate_accepted.jsonl")
    latest = {a["doc_id"]: a for a in acc}          # last write per order wins
    if not latest:
        raise SystemExit("no curate_accepted.jsonl")

    rates, med = ocr_rates()
    damaged = {k for k, v in rates.items() if v > med * 4}
    hot = sorted(damaged & set(latest), key=lambda k: -rates[k])

    md = ["# סקירת תוכן — סעיפי key-facts שנכתבו הלילה", "",
          f"‏{len(latest)} פקודות. **זה החוסם היחיד למיזוג.**", "",
          "השערים האוטומטיים תפסו המצאה: ציטוט לסעיף שלא קיים, אוצר-מילים זר לפקודה, "
          "נושא שהפקודה שותקת בו. הם **לא** יכולים לתפוס סעיף מעוגן היטב שפשוט שגוי — "
          "סף שנקרא לא נכון, מאשר בדרגה לא נכונה, תנאי שהוצמד למקרה הלא-נכון. "
          "זה מה שצריך עין אנושית.", "",
          "לכל סעיף מופיעים מספרי הסעיפים שהוא מצטט, כדי שאפשר יהיה לקפוץ אליהם בפקודה.", "",
          "## ⛔ לסקור קודם — מקור פגום ב-OCR", "",
          "הטקסט הגולמי של חלק מהפקודות שבור: ספרות תקועות בתוך מילים, מספרי סעיפים "
          "מודבקים, תאריכים משובשים. ‏20.0502 למשל מכילה „או0מאסר ב0 תקופת\", „ז0כאי\", "
          "„תוקף סעיפים6 עד7 מה- 0 נ ביו י3..0\". **פקודה כזאת נאצרה ממקור שאי אפשר "
          "לסמוך עליו, וגם אי אפשר לאמת את הציטוטים שלה כי המספרים לא ניתנים לשחזור "
          "מהטקסט.** אלה הסעיפים שהכי סביר שיהיו שגויים.", "",
          f"נמדד לפי ספרות מודבקות למילים עבריות; החציון בקורפוס {med:.0f} ל-1,000 מילים.", ""]
    if hot:
        md += [_tbl(["פקודה", "ספרות מודבקות / 1k מילים", "מול החציון"],
                    [[k, f"{rates[k]:.0f}", f"×{rates[k]/med:.1f}"] for k in hot]), ""]
    else:
        md += ["אף אחת מהפקודות שנאצרו אינה חורגת מפי-4 מהחציון.", ""]
    md += ["---", ""]

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

        flag = "  ⛔ **מקור פגום ב-OCR**" if doc_id in damaged else ""
        md += [f"## {doc_id} — {doc.get('title','')}{flag}", "",
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
