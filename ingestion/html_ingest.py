# -*- coding: utf-8 -*-
"""Ingest orders from the IDF orders-site HTML (canonical, clean text) instead
of the media PDFs (stale / RTL-scrambled digits — see the 2026-07-11 batch).

Input: a staging dir of {num, path, pdf, date, len, text} JSONs fetched from
the site (one per order) + a hand-maintained catalog below that fixes each
order's document_id and title (the site page carries them inconsistently).
The matching PDF (downloaded separately into pdf-ldf_law/) is referenced as
source_file so the sources UI and clause-image deep links keep working.

Usage: python ingestion/html_ingest.py <staging_dir> [num ...]
Runs analyze_document (Sonnet, ~4c/order) only for orders not already in the
json_store; re-running is idempotent and preserves hand-maintained fields.
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from common import safe_print
from ingestion.pdf_to_json import analyze_document, _existing_doc_for

# num (site slug digits) -> (document_id, title). Titles from the site catalog,
# ids normalized to the corpus convention (XX.YYYY).
CATALOG = {
    "350307": ("35.0307", 'השתתפות בשכ"ד, בשכר חדר או בהוצאות בעבור אחזקת דירה - חיילים בשירות חובה'),
    "350807": ("35.0807", 'הקלות בתנאי שירות (הת"ש)'),
    "350805": ("35.0805", "שירות חובה - מענק נישואין ומענק עקב לידה או אימוץ"),
    "350803": ("35.0803", "קופה להלוואות ולמענקים לסיוע לחיילים בשירות חובה"),
    "350205": ("35.0205", "תשלומים - חיילים משתחררים או מפוטרים משירות חובה"),
    "560131": ("56.0131", "דמי כלכלה לחיילים בשירות חובה"),
    "330120": ("33.0120", "נסיעות חיילים בתחבורה ציבורית"),
    "210113": ("21.0113", 'הגבלת שימוש בטלפון אישי (רט"ן) בצה"ל'),
    "210210": ("21.0210", 'צילום במחנות צה"ל'),
    "310308": ("31.0308", "בקשה לשינוי שיבוץ - חוגרים בשירות חובה או בשירות מילואים"),
    "310116": ("31.0116", 'נוהל העברת חיילים ושיבוצם קרוב לבית (קל"ב) מסיבה רפואית נפשית'),
    "330351": ("33.0351", "תרגול נוסף"),
    "610113": ("61.0113", "חסיון רפואי וסודיות רפואית"),
    "380122": ("38.0122", "נוהל טיפול בחיילים המשוחררים עקב אי-כשירות רפואית ונוהל הגשת בקשה להכרה בנכות"),
    "320401": ("32.0401", "כושר בריאותי - בדיקה, קביעה וסמכות"),
    "310109": ("31.0109", "שחרור חיילות בשירות חובה עקב נישואין או עקב הריון"),
    "330146": ("33.0146", "המרשם הפלילי - כללי סודיות, דרכי קבלת מידע מהמרשם ואופן הטיפול בו"),
    "300106": ("30.0106", "תעודות צבאיות"),
    "350206": ("35.0206", "תשלומים - שירות מילואים"),
    "350209": ("35.0209", "חיילי מילואים - תגמול מיוחד והשתתפות בהוצאות אישיות"),
    "310605": ("31.0605", "ועדה לתיאום שירות מילואים אישי בעת חירום"),
    "360106": ("36.0106", "הסדרי הפנסייה למשרתי הקבע החדשים"),
    "360513": ("36.0513", "השתתפות בשכר דירה, בשכר חדר ובהוצאות העברת תכולת דירה - חיילים בשירות קבע"),
    "350227": ("35.0227", "הפחתת שכר עקב מעצר וריצוי עונש - מתן פיצוי לאחר זיכוי - שירות סדיר ומילואים"),
    "350314": ("35.0314", "תשלומים בדיעבד לחיילים ופיצוי בגין פיגור בתשלום"),
    # רענון 2026-07-21: ארבע פקודות ותיקות שה-watcher מצא שהתעדכנו בפורטל.
    # ה-ids ההיסטוריים (05.104, PM-33.0207...) נשמרים — כל הקוד/eval מפנים אליהם.
    "350408": ("05.104", "פעילויות גיבוש והפגה, סיורים, ימי כיף, טיולים ונופשים"),
    "20101": ("2.0101", 'נשיאת נשק ואבטחתו בחופשה ובמהלך שהיה מחוץ למתקני צה"ל'),
    "330207": ("PM-33.0207", "השירות המשותף"),
    "580202": ("58.0202", "רישיון נהיגה"),
}


def _published(date_str: str) -> str | None:
    """Site header date (DD.MM.YY[YY]) -> ISO. Two-digit years: the orders
    corpus spans 1948-today, so YY > 26 reads as 19YY."""
    m = re.fullmatch(r"(\d{2})\.(\d{2})\.(\d{2,4})", date_str or "")
    if not m:
        return None
    d, mo, y = m.groups()
    if len(y) == 2:
        y = ("20" if int(y) <= 26 else "19") + y
    return f"{y}-{mo}-{d}"


def ingest_staged(staging_dir: Path, only: set[str] | None = None) -> list[str]:
    out_dir = Path(__file__).parent.parent / "storage" / "json_store"
    out_dir.mkdir(parents=True, exist_ok=True)
    from storage.vector_store import index_document

    done = []
    for f in sorted(staging_dir.glob("*.json")):
        num = f.stem
        if num not in CATALOG:
            safe_print(f"[skip] {num}: לא בקטלוג")
            continue
        if only and num not in only:
            continue
        doc_id, title = CATALOG[num]
        staged = json.loads(f.read_text(encoding="utf-8"))
        text = (staged.get("text") or "").strip()
        if len(text) < 600:
            safe_print(f"[skip] {num}: טקסט קצר מדי ({len(text)})")
            continue

        source_file = f"{num}.pdf"
        meta = {
            "document_id": doc_id,
            "title": title,
            "published": _published(staged.get("date", "")),
            "raw_text": text,
            "source_file": source_file,
            "source_url": "https://www.idf.il" + staged.get("path", ""),
        }

        prev = _existing_doc_for(out_dir, source_file)
        for field in ("sections", "annex_exceptions", "anchor_questions"):
            if prev and prev.get(field):
                meta[field] = prev[field]
        if prev and prev.get("questions_curated"):
            meta["suggested_questions"] = prev.get("suggested_questions") or {}
            meta["questions_curated"] = True
            meta["roles"] = prev.get("roles") or []
        else:
            analysis = analyze_document(text)
            meta["suggested_questions"] = analysis["questions"]
            meta["roles"] = analysis["roles"]

        slug = re.sub(r"[^\w֐-׿-]", "-", title)[:60]
        (out_dir / f"{slug}.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        index_document(meta)
        safe_print(f"[ok] {doc_id} — {title[:40]} ({len(text)} תווים, roles={','.join(meta['roles'])})")
        done.append(doc_id)
    return done


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python ingestion/html_ingest.py <staging_dir> [num ...]")
        sys.exit(1)
    staged = Path(sys.argv[1])
    only = set(sys.argv[2:]) or None
    ingested = ingest_staged(staged, only)
    print(f"הסתיים: {len(ingested)} פקודות נקלטו")
