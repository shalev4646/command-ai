# -*- coding: utf-8 -*-
"""אילו מסמכים איבדו את הספרות שלהם, ואיך משחזרים אותן מהדף.

למה הקובץ קיים
--------------
`night/digits.py` עונה על „האם לאצור את המסמך הזה" ומחזיר בוליאני לכל מסמך.
מה שחסר היה **תמונת המצב**: כמה מהקורפוס במצב הזה, אילו סעיפים מאוצרים כבר
מוגשים לחיילים עם „יש לבדוק בנוסח המקורי" במקום מספר, ומאיפה מתחילים לתקן.

נמדד ב-12.09.2026: **180 מ-294 המסמכים (61%) נכשלים בגלאי, ו-173 מהם נאצרו
בכל זאת.** ‏87 סעיפים מאוצרים ב-40 פקודות נושאים מציין-מקום במקום ערך.

⚡ **והממצא שמצדיק את הקובץ:** השיבוש הוא בשכבת-הטקסט של ה-PDF בלבד. הדף
המרונדר תקין לחלוטין. ‏`35.0306` הוא המקרה שאומת: שכבת-הטקסט אומרת „11 ק״מ"
והדף אומר **„50 ק״מ"**, והמיפוי הוא `0→1 · 2→9 · 4→1 · 5→1 · 9→1`.

⛔ **ארבע ספרות קורסות ל-„1", ולכן ההמרה חד-כיוונית ואי אפשר לשחזר מהטקסט.**
זו הסיבה שחילוץ מחדש (נוסה עם pymupdf — טקסט זהה) אינו עוזר, ושכל תיקון
אוטומטי מהטקסט יזריק מספרים שגויים לתוכן שחיילים פועלים לפיו.

הנתיב היחיד שעובד הוא **רינדור העמוד וקריאתו בראייה**. ‏`--render` מכין את
העמודים; הקריאה עצמה נעשית על-ידי המודל בסשן ולכן **אינה נוגעת בליג'ר**.

עצמאי בכוונה
------------
לא מייבא את `backend` (ולכן לא chromadb, לא anthropic, לא מודל ההטמעה) —
קורא את `storage/json_store` ישירות. זה מה שמאפשר להריץ אותו בכל סביבה,
כולל כזו בלי מפתח API, ובלי לשלם דבר.

    python -m night.digit_audit                    # דוח מלא
    python -m night.digit_audit --placeholders     # רק סעיפי מציין-המקום
    python -m night.digit_audit --render 35.0306   # רינדור עמודים ל-PNG
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

STORE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "storage", "json_store")
PDF_DIRS = ("pdf-ldf_law", "pdf-law", "pdf-hka")

# --- שכפול מדויק של המבחנים ב-night/digits.py ו-night/curate.py -------------
# משוכפל ולא מיובא, בכוונה: הייבוא גורר את backend ואיתו את כל תלויות
# ההרצה. המבחנים עצמם קצרים ויציבים; אם הם ישתנו שם, לעדכן גם כאן.
_FOUR = re.compile(r"(?<!\d)(\d{4})(?!\d)")
_YEAR_LO, _YEAR_HI = 1948, 2026
_MIN_PLAUSIBLE = 0.34
_MIN_SAMPLE = 3
_CLAUSE = re.compile(r"(?<!\d)(\d{1,3})\s*\.\s*(?=[\sא-ת])")

# התבניות שהאצירה כותבת כשאסור לה לנקוב במספר.
_PLACEHOLDER = re.compile(
    r"יש לבדוק בנוסח המקורי|הפקודה קובעת|שנקבע בפקודה|שהפקודה קובעת")


def year_share(raw: str) -> float | None:
    four = [int(y) for y in _FOUR.findall(raw)]
    if len(four) < _MIN_SAMPLE:
        return None
    return sum(1 for y in four if _YEAR_LO <= y <= _YEAR_HI) / len(four)


def is_numbered(raw: str) -> bool:
    nums = {int(m.group(1)) for m in _CLAUSE.finditer(raw)}
    nums = {n for n in nums if n > 0}
    if not nums:
        return False
    run = 0
    while run + 1 in nums:
        run += 1
    return run >= 5 and len(nums) / max(nums) >= 0.6


def trustworthy(raw: str) -> bool:
    share = year_share(raw)
    return (share is not None and share >= _MIN_PLAUSIBLE) or is_numbered(raw)


def load_docs() -> list[dict]:
    out = []
    for path in sorted(glob.glob(os.path.join(STORE, "*.json"))):
        with open(path, encoding="utf-8") as fh:
            out.append(json.load(fh))
    return out


def clauses_of(doc: dict) -> list[dict]:
    return [c for s in doc.get("sections", []) if isinstance(s, dict)
            for c in s.get("clauses", [])]


def audit(docs: list[dict]) -> list[dict]:
    rows = []
    for d in docs:
        raw = d.get("raw_text", "") or ""
        cls = clauses_of(d)
        rows.append({
            "id": d.get("document_id", ""),
            "title": (d.get("title") or "")[:44],
            "trust": trustworthy(raw),
            "share": year_share(raw),
            "clauses": len(cls),
            "placeholders": sum(1 for c in cls
                                if _PLACEHOLDER.search(c.get("text", ""))),
            "source_file": d.get("source_file", "") or "",
        })
    return rows


def find_pdf(source_file: str) -> str | None:
    if not source_file:
        return None
    for d in PDF_DIRS:
        p = os.path.join(d, source_file)
        if os.path.exists(p):
            return p
    return None


def render(doc_id: str, out_dir: str, dpi: int = 200) -> None:
    """מרנדר את עמודי המסמך ל-PNG, לקריאה בראייה ושחזור הספרות."""
    try:
        import fitz
    except ImportError:
        sys.exit("צריך pymupdf: pip install pymupdf")
    doc = next((d for d in load_docs() if d.get("document_id") == doc_id), None)
    if doc is None:
        sys.exit(f"{doc_id} אינו בקורפוס")
    pdf = find_pdf(doc.get("source_file", ""))
    if pdf is None:
        sys.exit(f"ה-PDF של {doc_id} אינו על הדיסק ({doc.get('source_file')})")
    os.makedirs(out_dir, exist_ok=True)
    with fitz.open(pdf) as f:
        for i, page in enumerate(f, 1):
            path = os.path.join(out_dir, f"{doc_id}_p{i:02d}.png")
            page.get_pixmap(dpi=dpi).save(path)
            print(f"  {path}")
    print(f"\n{doc_id}: {len(doc.get('raw_text','').split())} מילים בטקסט, "
          f"year_share={year_share(doc.get('raw_text','') or '')}")
    print("קרא את העמודים והשווה מול raw_text — כל הפרש הוא ספרה שאבדה בחילוץ.")


def main() -> None:
    ap = argparse.ArgumentParser(description="ביקורת ספרות על הקורפוס")
    ap.add_argument("--placeholders", action="store_true",
                    help="רק מסמכים שיש בהם סעיף עם מציין-מקום במקום ערך")
    ap.add_argument("--render", metavar="DOC_ID",
                    help="רנדר את עמודי המסמך ל-PNG לשחזור בראייה")
    ap.add_argument("--out", default="night/out/pages",
                    help="תיקיית היעד לרינדור")
    args = ap.parse_args()

    if args.render:
        render(args.render, args.out)
        return

    rows = audit(load_docs())
    broken = [r for r in rows if not r["trust"]]
    curated_broken = [r for r in broken if r["clauses"]]
    ph = [r for r in rows if r["placeholders"]]

    print(f"מסמכים בקורפוס:                 {len(rows)}")
    print(f"ספרות אמינות:                   {len(rows) - len(broken)}")
    print(f"⛔ ספרות שבורות:                 {len(broken)}"
          f"  ({len(broken) / max(1, len(rows)) * 100:.0f}%)")
    print(f"   מתוכם נאצרו ומוגשים לחיילים: {len(curated_broken)}")
    print(f"\nסעיפי מציין-מקום:               "
          f"{sum(r['placeholders'] for r in ph)} ב-{len(ph)} מסמכים")
    safe = sum(r["placeholders"] for r in ph if r["trust"])
    print(f"   מהם במסמכים שהגלאי מאשר:     {safe}  ← בטוח לתקן מהטקסט")
    print(f"   מהם במסמכים שהגלאי פוסל:     "
          f"{sum(r['placeholders'] for r in ph if not r['trust'])}"
          f"  ← דורש קריאה מהדף")

    rows_to_show = ph if args.placeholders else curated_broken
    print(f"\n{'מציין':>6} {'סעיפים':>7} {'אמין':>5} {'שנים%':>6}  מסמך")
    for r in sorted(rows_to_show,
                    key=lambda r: (-r["placeholders"], -r["clauses"]))[:40]:
        share = f"{r['share']:.2f}" if r["share"] is not None else "  - "
        print(f"{r['placeholders']:6d} {r['clauses']:7d} "
              f"{'V' if r['trust'] else 'X':>5} "
              f"{share:>6}  {r['id']:16s} {r['title']}")


if __name__ == "__main__":
    main()
