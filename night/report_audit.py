"""Turn the free structural audit into the corpus half of the morning report.

Deliberately standalone from the paid stages: if the night never gets an API
key, this still produces a ranked work list backed by measurements rather than
by intuition. The paid probe adds the refusal number on top; it does not
replace anything here.
"""
from __future__ import annotations

from night import config as C
from night.audit import SELF_RETRIEVAL_TOP_N

OUT = C.ROOT / "REPORT_corpus.md"


def _tbl(rows: list[list[str]], head: list[str]) -> str:
    out = ["| " + " | ".join(head) + " |",
           "|" + "|".join("---" for _ in head) + "|"]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def build() -> str:
    rows = C.read_jsonl(C.OUT / "audit.jsonl")
    if not rows:
        raise SystemExit("no audit.jsonl — run `python -m night.audit` first")

    n = len(rows)
    empty = [r for r in rows if r["liftable_but_empty"]]
    unreach = [r for r in rows if not r["reachable"]]
    # the worst combination: cannot be found from its own anchors AND has no
    # curated block to hand over if it ever were found
    both = [r for r in rows if not r["reachable"] and r["liftable_but_empty"]]

    md = [
        "# דוח קורפוס — סריקה מבנית חינמית",
        "",
        f"נמדד על {n} פקודות שעל הדיסק. אפס עלות: אחזור מקומי בלבד (ONNX + chroma), בלי אף קריאת API.",
        "",
        "## המנגנון שהסריקה בודקת",
        "",
        "כשעוגן מנצח באחזור, `vector_store` לא מגיש את הצ'אנק הכי טוב של המסמך אלא "
        "**את סעיף ה-key-facts שלו כבלוק ממוזג** ([vector_store.py:569](storage/vector_store.py:569)). "
        "לכן פקודה עם עוגנים אבל בלי key-facts היא *ניתנת-להרמה אך ריקה*: ההרמה מגיעה, "
        "ומאחוריה אין בלוק מתוקנן להגיש. זה לא ליקוי סגנוני — זה בדיוק המנגנון שהפיל "
        "את שתי פספוסי-הפיילוט מ-05.08.",
        "",
        "## שלושת המספרים",
        "",
        _tbl([
            ["פקודות ללא `sections` בכלל", len([r for r in rows if r["anchor_only"]]),
             "עוגן מרים אותן ומגיש רעש"],
            ["פקודות שלא מאתרות את עצמן מהעוגנים שלהן", len(unreach),
             f"בדיקה מאשרת בלבד — ר׳ הסתייגות למטה (טופ-{SELF_RETRIEVAL_TOP_N})"],
            ["**שתי הבעיות יחד**", f"**{len(both)}**", "עדיפות עליונה"],
        ], ["ממצא", "כמות", "משמעות"]),
        "",
    ]

    if both:
        md += ["## עדיפות 1 — לא נמצאות, וגם אין מה להגיש כשכן",
               "",
               "לכל אחת מאלה צריך **שני** התיקונים יחד. הזיכרון מ-08.08 מדד שעוגן לבדו "
               "מרים למקום 2 ומגיש את הסעיף הלא-נכון, ו-key-facts לבדו לא נכנס לחמישייה — "
               "עכשיו ידוע גם *למה*: ההרמה מוסרת את בלוק ה-key-facts, ואם אין כזה היא מוסרת רעש.",
               "",
               _tbl([[r["doc_id"], r["title"][:52], r["n_anchors"],
                      r["self_rank"] or "לא נמצאה", r["raw_words"]] for r in
                     sorted(both, key=lambda r: (r["self_rank"] or 999), reverse=True)],
                    ["פקודה", "כותרת", "עוגנים", "דירוג עצמי", "מילים ב-raw"]),
               ""]

    only_unreach = [r for r in unreach if r not in both]
    if only_unreach:
        md += ["## עדיפות 2 — יש key-facts, אבל האחזור לא מגיע אליהן",
               "",
               "כאן הבלוק קיים ותקין; מה שחסר הוא גשר-ניסוח. תיקון של עוגן בלבד.",
               "",
               _tbl([[r["doc_id"], r["title"][:52], r["n_anchors"], r["self_rank"] or "לא נמצאה"]
                     for r in only_unreach],
                    ["פקודה", "כותרת", "עוגנים", "דירוג עצמי"]),
               ""]

    only_empty = [r for r in empty if r not in both]
    if only_empty:
        md += ["## עדיפות 3 — נמצאות, אבל בלי בלוק מתוקנן מאחוריהן",
               "",
               "האחזור מגיע אליהן היום דרך הצ'אנקים הגולמיים. הן יישברו ברגע שעוגן ינצח — "
               "כלומר בדיוק על השאלות הארוכות והאנקדוטליות, שהן השאלות של משתמש אמיתי.",
               "",
               _tbl([[r["doc_id"], r["title"][:52], r["raw_words"]] for r in
                     sorted(only_empty, key=lambda r: -r["raw_words"])[:40]],
                    ["פקודה", "כותרת", "מילים ב-raw"]),
               ""]

    md += ["## הסתייגות על מבחן-הנגישות",
           "",
           f"‏{len(unreach)}/{n} פקודות נכשלו במבחן \"האם הפקודה מאתרת את עצמה מהעוגנים שלה\" — "
           "וזו בדיקה **מאשרת ולא מאבחנת**. מנגנון הרמת-העוגן מבטיח כמעט מראש שעוגן יאתר "
           "את הפקודה שנכתב עבורה, ולכן תוצאה נקייה מוכיחה שהמנגנון תקין, לא שהקורפוס מכוסה. "
           "השאלה האמיתית — מה קורה עם ניסוח ש**אינו** עוגן — נמדדת רק בסריקת השאלות.",
           "",
           "## מה הדוח הזה *לא* אומר",
           "",
           "- הוא לא מודד סירובים. הוא מודד האם התוכן **מגיע להקשר**, לא האם המודל **ענה**. "
           "שני אלה נפרדו במדידה מ-08.08 ואסור לערבב אותם.",
           "- הוא לא רואה את 316 הפקודות שאינן על הדיסק. פער-התוכן דורש משיכת מקורות, לא תיקון קוד.",
           "- דירוג-אחזור הוא פרוקסי: ‏35.0206 נשארה במקום 5 והמודל ענה נכון. עלייה בדירוג היא ראיה, לא ערובה.",
           ""]
    return "\n".join(md)


if __name__ == "__main__":
    OUT.write_text(build(), encoding="utf-8")
    C.log(f"[report] wrote {OUT}")
