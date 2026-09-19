# -*- coding: utf-8 -*-
"""wave-6 reach check (free): do fresh soldier phrasings reach the NEW documents?

These questions are deliberately not the anchors written for the documents —
an anchor that finds itself proves nothing. Production flags, route=∅, no HyDE.

    venv\\Scripts\\python.exe -m night.wave6.reach
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import backend  # noqa: E402
from common import safe_print  # noqa: E402

CASES = [
    ("FOI-FOOD-2025", "soldier", "אני טבעונית ובחדר אוכל כמעט אין לי מה לאכול. מה הצבא אמור לספק לי?"),
    ("FOI-FOOD-2025", "soldier", "אובחנתי עם צליאק באמצע השירות, איך מסדרים אוכל בלי גלוטן בבסיס?"),
    ("FOI-FOOD-2025", "commander", "חייל אצלי דורש אוכל מהדרין, מה המענה שצה\"ל נותן?"),
    ("HKA-33-05-01-S5", "soldier", "בשער לא נתנו לי לצאת הביתה בגלל שהמדים לא היו מסודרים, זה מותר?"),
    ("HKA-33-05-01-S5", "soldier", "שוטר צבאי רשם אותי על הופעה בתחנה המרכזית. יש לו בכלל סמכות?"),
    ("HKA-33-05-01-S5", "commander", "מי ביחידה שלי אחראי לבדוק הופעה של חיילים שיוצאים מהבסיס?"),
    ("HKA-32-03-10", "soldier", "כבר שנה וחצי אני בצבא ועדיין רבט, מה התנאים לקבל סמל?"),
    ("HKA-32-03-10", "soldier", "המפ אמר שאצלו בפלוגה מקבלים דרגה מאוחר יותר מכולם, מותר לו לקבוע דבר כזה?"),
    ("HKA-32-03-10", "commander", "חייל שלי חזר מהכלא, מתי מותר לי להמליץ עליו לדרגה?"),
]


def main() -> int:
    hyde, backend.RETRIEVE_HYDE = backend.RETRIEVE_HYDE, False
    hits = 0
    try:
        for want, role, q in CASES:
            win = backend.retrieve_for_role(q, role, route=set(), widen=False)
            win = backend.widen_context(win, q, role, route=set())
            docs = []
            for c in win:
                if c["doc_id"] not in docs:
                    docs.append(c["doc_id"])
            ok = want in docs
            hits += ok
            safe_print(f"{'HIT ' if ok else 'MISS'} {want:<17} pos={docs.index(want)+1 if ok else '-'}  window={docs[:4]}  | {q[:60]}")
    finally:
        backend.RETRIEVE_HYDE = hyde
    safe_print(f"[wave6] new documents reached: {hits}/{len(CASES)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
