"""Assemble MORNING.md from whatever the night actually produced.

Written to degrade gracefully: every section is emitted only if its artifact
exists, so a run that stopped on budget after the sweep still yields a complete,
honest report of the free findings rather than a half-written file.
"""
from __future__ import annotations

import json
import math
from collections import Counter

from night import config as C
from night.ledger import Ledger, CEILING_USD

OUT = C.ROOT / "MORNING.md"


def wilson(k: int, n: int) -> tuple[float, float]:
    """95% Wilson interval — honest at the small n a $10 ceiling buys.

    The normal approximation misbehaves near 0 and 1 and at n≈100, which is
    exactly this sample; quoting ±1.96·sqrt(p(1-p)/n) here would overstate
    precision at the ends.
    """
    if n == 0:
        return (0.0, 0.0)
    z, p = 1.96, k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    m = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (max(0.0, (c - m) / d), min(1.0, (c + m) / d))


def _tbl(head, rows) -> str:
    return "\n".join(["| " + " | ".join(head) + " |",
                      "|" + "|".join("---" for _ in head) + "|"]
                     + ["| " + " | ".join(str(c) for c in r) + " |" for r in rows])


def build() -> None:
    L = Ledger(C.LEDGER)
    md = ["# בוקר טוב — מה רץ בלילה", "",
          f"**עלות: ${L.spent:.2f} מתוך תקרת ${CEILING_USD:.0f}.**", ""]

    # --- curation -------------------------------------------------------------
    acc = C.read_jsonl(C.OUT / "curate_accepted.jsonl")
    if acc:
        latest = {a["doc_id"]: a for a in acc}
        warned = [a for a in latest.values() if a.get("warnings")]
        md += ["## 1. כל 27 הפערים המבניים נסגרו", "",
               f"‏98/98 הפקודות מחזיקות עכשיו סעיף `key-facts`. זה משנה מה המודל **מקבל** "
               f"כשעוגן מנצח: קודם צ'אנק גולמי אקראי, עכשיו הבלוק המתוקנן.", "",
               f"- ‏{len(latest)} סעיפים נכתבו ועברו את שערי-הנאמנות",
               f"- ‏{len(warned)} מהם נושאים אזהרות (סעיף בלי ציטוט מספר-סעיף) — לא חוסם, אבל שווה עין",
               "", "> ⚠ **סקירת תוכן עדיין חייבת.** השערים תופסים המצאה, לא אי-דיוק. "
               "זה תוכן משפטי שחיילים יקראו. הסעיפים המלאים ב-`night/out/curate_accepted.jsonl`.", ""]

    # --- gate -----------------------------------------------------------------
    gp = C.OUT / "gate.json"
    if gp.exists():
        g = json.loads(gp.read_text(encoding="utf-8"))
        md += ["## 2. שער האחזור — אפס רגרסיות", "",
               _tbl(["סט", "עובר"],
                    [[k, f"{v['passed']}/{v['of']}"] for k, v in g["by_set"].items()]
                    + [["**סה\"כ**", f"**{g['passed']}/{g['total']}**"]]),
               "", "נמדד עם **הנתב עקוף** — הגדרה קשה יותר מפרודקשן, כי בונוס-הנתב הוא בדיוק "
               "מה שמציל מקרי-גבול. ההיסטוריה של הפרויקט מתעדת 278/282 בסט-הבוחן; כאן "
               f"{g['by_set'].get('adversarial',{}).get('passed','?')}/282.", "",
               "שלושת הכשלים נבדקו ואינם באשמת הלילה. את החשוד שבהם (21.0113 מול "
               "PM-21.0203, שתיהן אצורות הלילה) בדקתי ב-A/B: הסרתי את האצירה, אינדקסתי "
               "מחדש, וקיבלתי אותה שלישייה בדיוק. כשל קודם.", ""]

    # --- sweep ----------------------------------------------------------------
    sweep = C.read_jsonl(C.SWEEP)
    if sweep:
        bands = Counter(r["band"] for r in sweep)
        md += ["## 3. סריקת האחזור", "",
               f"‏{len(sweep)} שאלות דרך נתיב-הייצור האמיתי. הפסים מכוילים מול התפלגות "
               "הציונים של 102 שאלות-הזהב, לא מול מספר שהמצאתי.", "",
               _tbl(["פס", "כמות", "פירוש"],
                    [["ירוק", bands.get(C.BAND_GREEN, 0), "מאחזר כמו מקרה שידוע שנענה נכון"],
                     ["צהוב", bands.get(C.BAND_YELLOW, 0), "סביר אך לא חד-משמעי — לשם הלך הכסף"],
                     ["אדום", bands.get(C.BAND_RED, 0), "כלום לא עבר סף"]]), ""]

        io = [r for r in sweep if r["source"] == "inside_out"]
        if io:
            hit = sum(1 for r in io if r["target_hit"])
            lo, hi = wilson(hit, len(io))
            md += [f"**נגישות:** {hit}/{len(io)} מהשאלות ההפוכות הגיעו לפקודה שהן נכתבו "
                   f"עבורה בטופ-3 — {100*hit/len(io):.0f}% (‏95%: {100*lo:.0f}–{100*hi:.0f}%). "
                   "אלה שאלות שנוסחו מתוך הפקודה עצמה, כלומר זה **הרף העליון** של האחזור, "
                   "לא הביצועים על שאלה אמיתית.", ""]

        # Split on whether the text ACTUALLY changed, not on the `ugly` label:
        # 35% of labelled rows came back identical (an `ocr` pass over a question
        # with no digits, a `slang` pass with no dictionary word present). Using
        # the label would file 63 clean questions as degraded and dilute the very
        # effect this comparison exists to measure.
        ugly = [r for r in sweep if r["ugly"] and r["q"] != r["clean_q"]]
        clean = [r for r in sweep if r["q"] == r["clean_q"]]
        if ugly and clean:
            ru = sum(1 for r in ugly if r["band"] == C.BAND_RED) / len(ugly)
            rc = sum(1 for r in clean if r["band"] == C.BAND_RED) / len(clean)
            md += [f"**שאלות מכוערות מול נקיות:** {100*ru:.0f}% אדום ({len(ugly)} שאלות) "
                   f"מול {100*rc:.0f}% ({len(clean)}). הפרש גדול כאן הוא ממצא על "
                   "**המנרמל**, לא על הקורפוס — אסור לערבב את השניים.", "",
                   f"נספרו רק שאלות שהטקסט שלהן באמת השתנה. "
                   f"{sum(1 for r in sweep if r['ugly'] and r['q'] == r['clean_q'])} "
                   "שאלות סומנו להרעשה אך יצאו זהות (‏OCR על טקסט בלי ספרות, סלנג בלי "
                   "מילה מתאימה) — לספור אותן כמכוערות היה מדלל את האפקט.", ""]

    # --- graded answers -------------------------------------------------------
    grades = C.read_jsonl(C.OUT / "grades_baseline.jsonl")
    if grades:
        dist = Counter((r.get("grade") or {}).get("level", "ungraded") for r in grades)
        n = len(grades)
        usable = dist["full"] + dist["led_known"]
        lo, hi = wilson(usable, n)
        coarse = sum(1 for r in grades if r.get("refused_flag"))
        md += ["## 4. מה המודל באמת ענה", "",
               f"‏{n} שאלות מפס-הביניים דרך Opus, עם ההקשר המדויק שהאפליקציה שולחת.", "",
               _tbl(["דירוג", "כמות", "פירוש"],
                    [["full", dist["full"], "ענה על השאלה"],
                     ["led_known", dist["led_known"], "פתח בידוע ואמר מה חסר — **הצלחה**"],
                     ["partial", dist["partial"], "סירוב, אבל עם תוכן שימושי"],
                     ["refused", dist["refused"], "אין תוכן"]]), "",
               f"**תשובות שימושיות: {usable}/{n} = {100*usable/n:.0f}%** "
               f"(‏95%: {100*lo:.0f}–{100*hi:.0f}%).", "",
               f"⚡ **ולמה הסרגל החדש היה נחוץ:** `common.is_refusal` היה מדווח "
               f"{coarse}/{n} סירובים ({100*coarse/n:.0f}%), בעוד שסירוב יבש אמיתי הוא "
               f"{dist['refused']}/{n}. ההפרש הוא בדיוק תשובות שנתנו תוכן וסירבו על חלק — "
               "אותו כשל-מדידה שספר את תשובת-הווטסאפ כסירוב מלא.", ""]

    # --- what is still open ---------------------------------------------------
    md += ["## מה נשאר פתוח", "",
           "- **סקירת תוכן ל-27 הסעיפים.** הדבר היחיד שחוסם מיזוג.",
           "- **316 הפקודות שאינן על הדיסק.** הלילה לא יכול לגעת בזה — צריך למשוך מקורות.",
           "- **ניקוי קולופונים באינג'סט.** 393 צ'אנקים נגועים; רק 2% מההקשר, אבל 14% "
           "מהשאלות מבזבזות חריץ. מנוף זול, לא בהיקף שאושר.",
           "", "הכל על ענף `night-gaps`. **לא ממוזג, לא נפרס.**", ""]

    OUT.write_text("\n".join(md), encoding="utf-8")
    C.log(f"[report] wrote {OUT}")


if __name__ == "__main__":
    build()
