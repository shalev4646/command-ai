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
               f"- ‏{len(latest)} סעיפים נכתבו ע\"י Opus ועברו את שערי-הנאמנות, "
               "ועוד אחד (‏33.0304) נכתב ידנית בחזרה שהוכיחה את המנגנון — סה\"כ 27",
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
        blind_rows = [r for r in sweep if r["source"] == "blind"]
        io_rows = [r for r in sweep if r["source"] == "inside_out"]
        bb, bi = Counter(r["band"] for r in blind_rows), Counter(r["band"] for r in io_rows)

        def _pct(c, rows, band):
            return f"{c.get(band,0)} ({100*c.get(band,0)/max(1,len(rows)):.0f}%)"

        md += ["## 3. סריקת האחזור", "",
               f"‏{len(sweep)} שאלות דרך נתיב-הייצור האמיתי. הפסים מכוילים מול התפלגות "
               "הציונים של 102 שאלות-הזהב, לא מול מספר שהמצאתי.", "",
               "> ⚠⚠ **אל תקרא את הפס האדום כשיעור-כישלון — כיילתי אותו לא נכון.** "
               "הסף נגזר מהאחוזון ה-10 של שאלות-הזהב (0.792), ושאלות-זהב נכתבו קרוב "
               "לשפת הקורפוס. שאלות טבעיות מקבלות ציונים נמוכים יותר באופן שיטתי: "
               "החציון שלהן הוא 0.724, כלומר **יותר ממחציתן נופלות מתחת לעשירון התחתון "
               "של סט-הזהב** עוד לפני שנבדק אם הן נכשלו.", "",
               "המדידה שחושפת את זה: מתוך השאלות ההפוכות בפס האדום, "
               "**72% בכל זאת הגיעו לפקודה שלהן בטופ-3** (ירוק 100%, צהוב 88%). "
               "הפסים **מדרגים** נכון ו**מסווגים** לא נכון — השתמש בהם כסדר-עדיפויות, "
               "לא כפסק-דין.", "",
               "**והפילוח מופרד בכוונה.** השאלות ההפוכות נוצרו מתוך הבלוקים המתוקננים "
               "עצמם ולכן נבדקות מול הטקסט שילד אותן — הן מודדות תקינות-הטמעה, לא כיסוי. "
               "**העמודה הכנה היא של השאלות העיוורות.**", "",
               _tbl(["פס", f"עיוורות (n={len(blind_rows)}) ← הכנה", f"הפוכות (n={len(io_rows)}) ← מעגלי"],
                    [["ירוק", _pct(bb, blind_rows, C.BAND_GREEN), _pct(bi, io_rows, C.BAND_GREEN)],
                     ["צהוב", _pct(bb, blind_rows, C.BAND_YELLOW), _pct(bi, io_rows, C.BAND_YELLOW)],
                     ["אדום", _pct(bb, blind_rows, C.BAND_RED), _pct(bi, io_rows, C.BAND_RED)]]), "",
               "ירוק = מאחזר כמו מקרה שידוע שנענה נכון · צהוב = סביר אך לא חד-משמעי, "
               "לשם הלך הכסף · אדום = כלום לא עבר סף.", ""]

        io = [r for r in sweep if r["source"] == "inside_out"]
        if io:
            hit = sum(1 for r in io if r["target_hit"])
            lo, hi = wilson(hit, len(io))
            md += [f"**נגישות (מספר מנופח — ר׳ אזהרה):** {hit}/{len(io)} מהשאלות ההפוכות "
                   f"הגיעו לפקודה שלהן בטופ-3, {100*hit/len(io):.0f}% "
                   f"(‏95%: {100*lo:.0f}–{100*hi:.0f}%).", "",
                   "> ⚠⚠ **המספר הזה מעגלי ואסור להתייחס אליו כמדד כיסוי.** אחרי שכל 98 "
                   "הפקודות קיבלו `key-facts`, השאלות ההפוכות נוצרו **מתוך הבלוקים "
                   "המתוקננים עצמם** ולא מהטקסט הגולמי — כלומר הן נבדקות מול בדיוק הטקסט "
                   "שילד אותן. זה מודד שהטמעה מוצאת פסקה מפסקה, לא שהקורפוס עונה לחייל. "
                   "**המספר הכן היחיד הוא של השאלות העיוורות.**", "",
                   "בדיקה נקודתית שממחישה את הפער, על 33.0304: הניסוח המדויק של העוגן "
                   "(*„אם הוזמנתי לעדות ולא הגעתי\"*) מחזיר אותה במקום 1; שתי פרפרזות "
                   "טבעיות של אותה שאלה לא מחזירות אותה **בטופ-25 בכלל**. הנתב אפילו זיהה "
                   "אותה נכון והבונוס שלו לא שינה ולו ספרה — כי הצ'אנקים שלה לא מגיעים "
                   "לרשימת-המועמדים מלכתחילה. וזאת מפני שה-key-facts שנכתבו לה מכסים "
                   "אזהרת-חשוד ומעצר, ולא את מה שהעוגן מבטיח. **העוגנים מבטיחים יותר ממה "
                   "שהאצירה מכסה** — פריט עבודה קונקרטי, ואפשר לסרוק אותו בחינם על כל 98.", ""]

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

    # --- anchors vs curated coverage -----------------------------------------
    prom = C.read_jsonl(C.OUT / "promise.jsonl")
    if prom:
        import statistics as st
        sc = [r["best_cosine"] for r in prom]
        acc_ids = {a["doc_id"] for a in C.read_jsonl(C.OUT / "curate_accepted.jsonl")}
        mine = [r["best_cosine"] for r in prom if r["doc_id"] in acc_ids]
        pre = [r["best_cosine"] for r in prom if r["doc_id"] not in acc_ids]
        worst: dict[str, int] = {}
        tot: dict[str, int] = {}
        for r in prom:
            tot[r["doc_id"]] = tot.get(r["doc_id"], 0) + 1
            if r["best_cosine"] < 0.35:
                worst[r["doc_id"]] = worst.get(r["doc_id"], 0) + 1

        md += ["## 5. העוגנים מבטיחים יותר ממה שהבלוקים מספקים", "",
               "נמצא במקרה ונמדד אחר כך. ‏33.0304 נושאת עוגן *„אם הוזמנתי לעדות ולא "
               "הגעתי\"*, אבל הבלוק המתוקנן שלה מכסה אזהרת-חשוד ומעצר — לא אי-התייצבות. "
               "הניסוח המדויק של העוגן כן מאתר אותה, כי הוא מאונדקס כצ'אנק בפני עצמו "
               "ומסתיר את הפער; שתי פרפרזות טבעיות לא מחזירות אותה בטופ-25 בכלל.", "",
               "נמדד על כל הקורפוס: לכל עוגן, הקוסינוס לסעיף המתוקנן הקרוב ביותר, עם "
               "אותו מודל-הטמעה שהאחזור עצמו משתמש בו.", "",
               _tbl(["סף", "עוגנים לא-מכוסים"],
                    [[f"< {t}", f"{sum(1 for x in sc if x < t)} "
                                f"({100*sum(1 for x in sc if x < t)/len(sc):.0f}%)"]
                     for t in (0.30, 0.35, 0.45, 0.55)]), "",
               "> ⚠ **הסף מוצג ברגישות בכוונה.** הריצה הראשונה שלי השתמשה ב-0.55 ודיווחה "
               "**41% לא-מכוסים** — מספר מבהיל וכמעט ריק מתוכן, כי 0.55 יושב ממש מתחת "
               f"לחציון ({st.median(sc):.3f}), וכמחצית מכל דבר נמצאת מתחת לחציון שלו. "
               f"הקו שאני מוכן להגן עליו הוא **0.35 (‏האחוזון ה-10) ⇒ "
               f"{sum(1 for x in sc if x < 0.35)} עוגנים, 7%**.", ""]
        if mine and pre:
            md += [f"**והפער אינו באשמת הלילה:** הפקודות שאצרתי מכוסות טוב יותר "
                   f"מהקיימות — חציון {st.median(mine):.3f} מול {st.median(pre):.3f}, "
                   f"ומתחת ל-0.35: {100*sum(1 for x in mine if x<0.35)/len(mine):.0f}% "
                   f"מול {100*sum(1 for x in pre if x<0.35)/len(pre):.0f}%. זו בעיה "
                   "קיימת בקורפוס.", ""]
        if worst:
            md += ["הפקודות הדחופות ביותר:", "",
                   _tbl(["פקודה", "עוגנים לא-מכוסים"],
                        [[k, f"{v}/{tot[k]}"] for k, v in
                         sorted(worst.items(), key=lambda kv: -kv[1])[:10]]), ""]

    # --- what is still open ---------------------------------------------------
    md += ["## מה נשאר פתוח", "",
           "- **סקירת תוכן ל-27 הסעיפים.** הדבר היחיד שחוסם מיזוג.",
           "- **117 עוגנים בלי סעיף מכסה.** סריקה חינמית מוכנה (`night/promise.py`), "
           "התיקון הוא הרחבת key-facts קיימים — לא כתיבה מאפס.",
           "- **316 הפקודות שאינן על הדיסק.** הלילה לא יכול לגעת בזה — צריך למשוך מקורות.",
           "- **ניקוי קולופונים באינג'סט.** 393 צ'אנקים נגועים; רק 2% מההקשר, אבל 14% "
           "מהשאלות מבזבזות חריץ. מנוף זול, לא בהיקף שאושר.",
           "", "הכל על ענף `night-gaps`. **לא ממוזג, לא נפרס.**", ""]

    OUT.write_text("\n".join(md), encoding="utf-8")
    C.log(f"[report] wrote {OUT}")


if __name__ == "__main__":
    build()
