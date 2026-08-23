"""A withdrawn order must never be curated.

58.0301 (הובלת מטען חורג) was curated on 2026-08-17 and served to commanders
as current until 2026-08-24. Nothing inside the document was wrong: the orders
site marks a withdrawal only in the NAME of the PDF it serves, so every gate
that reads raw_text confirmed it as live. Labelling it afterwards took three
sessions and three fixes across two lanes; refusing to curate it is one check.

    venv\Scripts\python.exe tests\test_withdrawn_orders.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from night.curate import withdrawn

failed = []


def check(name, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"   {detail}" if detail else ""))
    if not cond:
        failed.append(name)


def main() -> int:
    # the marker lives in the file name, which is the whole point
    check("a withdrawal marked only in source_file is caught",
          withdrawn({"source_file": "פמ-580301-הובלת-מטען-חורג-נוסח-עדכני-פקודה-מבוטלת.pdf",
                     "title": "הובלת מטען חורג", "raw_text": "כללים תקינים לגמרי"}))
    check("a withdrawal marked in the title is caught",
          withdrawn({"title": 'פ"מ 52.0301 (פקודה מבוטלת)', "source_file": "x.pdf"}))
    check("a withdrawal marked in civil_label is caught",
          withdrawn({"civil_label": 'פ"מ 58.0301 — ⚠ פקודה מבוטלת', "source_file": "x.pdf"}))
    check("הוראה מבוטלת is caught too",
          withdrawn({"source_file": "הקא-1234-הוראה-מבוטלת.pdf"}))
    check("an order in force is not caught",
          not withdrawn({"source_file": "פמ-330209-נסיעה-מחוץ-למסגרת-התפקיד-פרסום.pdf",
                         "title": "נסיעה מחוץ למסגרת התפקיד"}))
    check("a document with no fields at all does not crash",
          not withdrawn({}))
    # a live order whose TEXT merely discusses cancellation must stay curatable
    check("the word inside raw_text alone does not disqualify",
          not withdrawn({"source_file": "פמ-330304.pdf", "title": "ביטול עונש",
                         "raw_text": "פקודה מבוטלת אינה מקנה זכות"}))

    # and the corpus itself: every withdrawn order must already be flagged
    store = ROOT / "storage" / "json_store"
    unflagged = []
    for p in store.glob("*.json"):
        d = json.loads(p.read_text(encoding="utf-8"))
        if withdrawn(d) and d.get("superseded") is not True:
            unflagged.append(d.get("document_id"))
    check("every withdrawn order in the corpus carries superseded=True",
          not unflagged, f"unflagged: {unflagged}")

    print(f"\n{'FAILED: ' + ', '.join(failed) if failed else 'all withdrawn-order tests passed'}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
