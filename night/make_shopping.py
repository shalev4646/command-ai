"""Write SHOPPING_LIST.md from the site index cross-reference.

Priority is not "how many are missing". Three families sit at 0-4% coverage and
are the largest gaps by count — civilian-employee terms, construction, and the
General Staff's own organisational orders — and none of them touched a single
gap measured in the night's probe, because they are not about a soldier asking
what they are owed. Count is not priority; count × relevance is.
"""
from __future__ import annotations

import collections
import json

import backend
from night import config as C

NAMES = {
    "31": "כוח אדם ותנועה", "36": "תנאי שירות קבע", "33": "משטר ומשמעת",
    "35": "תנאי שירות חובה", "32": "סיווג וקידום כ\"א", "38": "נפגעים ושבויים",
    "61": "רפואה", "58": "תנועות", "21": "ביטחון מידע", "30": "רשומות כ\"א",
}
# family -> (gap share measured in the night's probe) x (1 - current coverage)
SCORE = [("33", 14.6), ("31", 13.9), ("35", 12.6), ("58", 8.2), ("61", 6.1),
         ("32", 5.6), ("38", 2.4), ("36", 2.3), ("21", 1.8), ("30", 1.3)]


def build() -> None:
    by = json.loads((C.OUT / "missing_by_family.json").read_text(encoding="utf-8"))
    have = collections.Counter(
        str(d["document_id"]).replace("PM-", "").split(".")[0].lstrip("0")
        for d in backend.load_documents() if d.get("document_id"))

    md = ["# רשימת קניות — 370 הפקודות החסרות", "",
          "**נמדד מול אינדקס אתר-הפקודות: 447 באתר, 98 אצלנו, 77 מזוהות ⇒ 370 חסרות.**", "",
          "זה מחליף את ההערכה „98/414\" שעבדנו לפיה. הכיסוי האמיתי הוא **17%**, לא 24%.", "",
          "## סדר המשיכה", "",
          "הציון = חלקה של המשפחה בפערים שנמדדו אמש **×** (‏1 − הכיסוי הנוכחי). "
          "כלומר כמה כאב היא מייצרת, משוקלל בכמה ממנה חסר.", "",
          "| ציון | משפחה | חסרות | יש | כיסוי |", "|---|---|---|---|---|"]
    for f, s in SCORE:
        n, g = len(by.get(f, [])), have.get(f, 0)
        md.append(f"| {s:.1f} | **{NAMES[f]}** ({f}) | {n} | {g} | {100*g/max(1,g+n):.0f}% |")

    md += ["",
           "> ⚠ **מה שהשארתי בכוונה בחוץ, למרות שהוא הגדול ביותר בספירה:** ‏41 (תעסוקת "
           "אזרחים, 27 חסרות, כיסוי 0%), ‏59 (בינוי, 13, ‏0%) ו-2/3 (הפ\"ע ארגוני, 52, ‏3-4%). "
           "אף אחת מהן לא נגעה בפער שנמדד אמש — הן עוסקות בעובדי צה\"ל ובמבנה ארגוני, "
           "לא בחייל ששואל מה מגיע לו. **ספירה גדולה איננה עדיפות.**", "",
           "---", "", "## המספרים המדויקים למשיכה", ""]
    listed = 0
    for f, _ in SCORE:
        ids = sorted(by.get(f, []))
        if not ids:
            continue
        listed += len(ids)
        md += [f"### {NAMES[f]} ({f}) — {len(ids)} חסרות", "",
               "`" + "`, `".join(ids) + "`", ""]

    md += ["---", "",
           f"‏{listed} מתוך 370 מפורטות כאן; היתר במשפחות שסוננו למעלה. "
           "הרשימה המלאה: `night/out/missing_by_family.json`.", "",
           "## מה קורה אחרי שתוריד", "",
           "‏`ingestion/html_ingest.py` מטמיע, ואז האצירה והשערים רצים אוטומטית. "
           "אימתתי ש-`_existing_doc_for` **משמר** `sections` ו-`anchor_questions` — "
           "‏27 הסעיפים שנכתבו בלילה ישרדו את ההטמעה מחדש.", ""]

    (C.ROOT / "SHOPPING_LIST.md").write_text("\n".join(md), encoding="utf-8")
    C.log(f"[shopping] wrote SHOPPING_LIST.md — {listed} orders across {len(SCORE)} families")


if __name__ == "__main__":
    build()
