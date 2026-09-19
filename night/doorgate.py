# -*- coding: utf-8 -*-
r"""שער-הדלתות — „דלת שגויה גרועה מאין-דלת", נמדד ולא מוצהר.

חינם: בלי API, בלי רשת, בלי מודל. קורא רק תוצרי-מדידה שכבר על הדיסק.

שני דברים, ושניהם נדרשים ב-`night/DOORS_CRITERION.md` שנכתב לפני הקוד:

1. **שער קשיח — אפס תפיסות.** משפחה חדשה שנדלקת על שאלה שהבוררות סימנה
   `ANSWERED*`, או ששורת-מדידה מראה שהאפליקציה ענתה עליה בפועל
   (`refused_flag` כבוי), היא דלת שגויה. תפיסה אחת ⇒ יציאה 1.
   הסט נבנה בקוד ולא נבחר ביד, כי סט שנבחר ביד בוחר את מה שהוא רוצה למצוא.

2. **הסט המוחזק.** כל שאלה מבוררת `NO_SUCH_RULE` / `NOT_IN_CORPUS` שאינה
   מהסרגל — כלומר לא השתתפה בכתיבת המשפחות החדשות. התפיסות שלה **מודפסות
   לקריאה ידנית** ואינן מכשילות אוטומטית: כאן השאלה אינה „האם נדלקה" אלא
   „האם הכתובת נכונה", ועל זה עונה אדם. זה כלל 5 ב-`CLAUDE.md`, ובלעדיו
   הבדיקה היא מראה.

שימוש:
    python -m night.doorgate            # שער + דוח
    python -m night.doorgate --families civil_school,fault_report
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import out_of_scope as OS  # noqa: E402

OUT = ROOT / "night" / "out"

# המשפחות שנכתבו 19.09 ולכן הן היחידות שהשער הזה אחראי עליהן. המשפחות
# הישנות נבדקו בשעתן מול הסטים שלהן; הרצה עליהן כאן תדווח על תפיסות
# שאושרו מזמן ותטביע את האות.
NEW_FAMILIES = ("civil_school", "fault_report", "pay_entitlement")


def _load(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _rows_of(blob) -> list[dict]:
    """קובצי-הבוררות אינם בצורה אחת: `adjudication.json` הוא מילון של שלוש
    רשימות, והשאר רשימות. מי שיניח צורה אחת יאבד 31 שאלות בשקט."""
    if isinstance(blob, list):
        return [r for r in blob if isinstance(r, dict)]
    if isinstance(blob, dict):
        out: list[dict] = []
        for v in blob.values():
            if isinstance(v, list):
                out += [r for r in v if isinstance(r, dict)]
        return out
    return []


def _text(r: dict) -> str:
    return (r.get("clean_q") or r.get("question") or r.get("q") or "").strip()


def answered_questions() -> list[tuple[str, str]]:
    """(מקור, שאלה) לכל שאלה שהפקודות כן ענו עליה — משני סוגי ראיה.

    ⚡ 19.09: ההרצה הראשונה „נכשלה" על rs053 עצמה, שהיא יעד מבורר
    `NO_SUCH_RULE`. הסיבה: `refused_flag` כבוי פירושו „הזרוע הזו לא סירבה",
    לא „הפקודות עונות" — בזרוע sonnet6 המודל ענה על שאלה שאין עליה כלל.
    ⇒ **הבוררות גוברת על המדידה.** שאלה שבוררה כ„אין כלל" יוצאת מסט
    אפס-התפיסות גם אם זרוע כלשהי ענתה עליה; אחרת השער מעניש דלת על כך
    שהיא עושה בדיוק את עבודתה.
    """
    no_rule: set[str] = set()
    for path in sorted(OUT.glob("adjudication*.json")):
        for r in _rows_of(_load(path)):
            if str(r.get("verdict", "")) in ("NO_SUCH_RULE", "NOT_IN_CORPUS"):
                q = _text(r)
                if q:
                    no_rule.add(q)

    seen: dict[str, str] = {}
    for path in sorted(OUT.glob("adjudication*.json")):
        for r in _rows_of(_load(path)):
            if str(r.get("verdict", "")).startswith("ANSWERED"):
                q = _text(r)
                if q and q not in no_rule:
                    seen.setdefault(q, f"{path.name}:{r.get('id', '?')}")
    for path in sorted(OUT.glob("probe_*.jsonl")):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(r, dict) and not r.get("refused_flag"):
                q = _text(r)
                if q and q not in no_rule:
                    seen.setdefault(q, f"{path.name}:{r.get('id', '?')}")
    return sorted(seen.items(), key=lambda kv: kv[1])


def held_out_no_rule() -> list[tuple[str, str]]:
    """שאלות מבוררות „אין כלל" שאינן מהסרגל — הסט שלא השתתף בכתיבה."""
    out: dict[str, str] = {}
    for path in sorted(OUT.glob("adjudication*.json")):
        if "realstyle" in path.name:      # הסרגל — ממנו נכתבו המשפחות
            continue
        for r in _rows_of(_load(path)):
            if str(r.get("verdict", "")) in ("NO_SUCH_RULE", "NOT_IN_CORPUS"):
                q = _text(r)
                if q:
                    out.setdefault(q, f"{path.name}:{r.get('id', '?')}")
    return sorted(out.items(), key=lambda kv: kv[1])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--families", default=",".join(NEW_FAMILIES))
    args = ap.parse_args(argv)
    watched = {f.strip() for f in args.families.split(",") if f.strip()}

    answered = answered_questions()
    held = held_out_no_rule()
    print(f"[doorgate] watching {sorted(watched)}")
    print(f"[doorgate] zero-capture set: {len(answered)} answered questions")
    print(f"[doorgate] held-out set: {len(held)} adjudicated no-rule questions "
          f"(the ruler is excluded -- it wrote the families)")

    breaches = []
    for q, src in answered:
        fam = OS.family_of(q)
        if fam in watched:
            breaches.append((fam, src, q))

    hits = []
    for q, src in held:
        fam = OS.family_of(q)
        if fam in watched:
            hits.append((fam, src, q))

    print()
    if breaches:
        print(f"[doorgate] FAIL -- {len(breaches)} wrong doors on answered questions:")
        for fam, src, q in breaches:
            print(f"    {fam:16} {src:34} {q[:70]}")
    else:
        print("[doorgate] PASS -- zero captures on every answered question")

    print()
    print(f"[doorgate] held-out captures: {len(hits)} -- READ EACH ONE. "
          f"the question is not 'did it fire' but 'is the address right'.")
    for fam, src, q in hits:
        print(f"    {fam:16} {src:34} {q[:70]}")

    return 1 if breaches else 0


if __name__ == "__main__":
    raise SystemExit(main())
