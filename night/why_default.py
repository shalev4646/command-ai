# -*- coding: utf-8 -*-
"""למה נדלקה דלת ברירת-המחדל — כשל-אחזור מוסווה, או באמת אין כלל.

למה הקובץ קיים
--------------
`out_of_scope.py` כתב את האזהרה על עצמו ב-26.08:

    ⚠ המחיר, לרישום: כשליש מהאפסים שבהם הרצועה נדלקת הם שאלות שהקורפוס כן
    יכול לענות עליהן והאחזור החמיץ. דלת גנרית משתיקה גם אותן, ולכן הרצועה
    מפסיקה להיות סימן שמשהו שבור. **המדד להחזיק בו: כמה שאלות מגיעות עד
    לכאן.**

ב-28.08 נמדד: **40 מ-42 שאלות טריות מגיעות לדלת ברירת-המחדל.** כלומר הרצועה
כיסתה על הכול ואיש לא ידע על מה. המודול הזה מפריד את הרעש מהאות.

איך
---
התשובה עצמה נוקבת במה שחסר לה — `**טרם במאגר:** מכסת ימי החופשה השנתית`.
זו שאילתה טובה בהרבה מהשאלה המדוברת: היא כבר מנוסחת בשפת הפקודות. המודול
מנקד אותה מול **כל 2,389 הסעיפים המאוצרים** ומחזיר את ההתאמה הטובה ביותר.

**ארבע-גרמים של תווים, לא מילים.** העברית מדביקה תחיליות וסופיות לגזע, וגישה
ברמת-מילה נוסתה באותו יום וטבעה ברעש המורפולוגי: „קעקועים" מול „קעקוע" מול
„הקעקוע" הן שלוש מילים שונות ואותו ארבע-גרם.

הכיול — ומה שהוא לא נותן
------------------------
כויל מול **107 האפסים שהבוררות של פיילוט-150 תייגה אחד-אחד עם אימות-ציטוט**.
שיעור-הבסיס שם: 69.2% מהם שאלות שלקורפוס יש עליהן חומר.

    AUC = 0.758   (יש-חומר מול NO_SUCH_RULE, ארבע-גרמים; שלוש=0.717, חמש=0.700)

    סף      מסומנות   דיוק     כיסוי
    0.150      21     100.0%   28.4%
    **0.130**  38     **94.7%**  48.6%    <- נקודת-העבודה
    0.120      55      85.5%   63.5%
    0.100      75      78.7%   79.7%

⚠ **ומה ש-`COVERED` אינו אומר.** הכיול היה מול „לא NO_SUCH_RULE", וצד זה כולל
גם 12 שאלות שהבוררות סימנה `NOT_IN_CORPUS` — הכלל קיים במסמך שאינו שלנו,
והקורפוס רק מזכיר את הנושא. הראשונה בתור של פיילוט-150 היא בדיוק כזו (ביטוח
לאומי, 0.279). ⇒ **`COVERED` = „הפקודות אינן פשוט שותקות כאן, שווה להסתכל",
ולא „התשובה נמצאת ב-36.0401".**

⛔ **והאות חד-כיווני — זו המגבלה החשובה ביותר כאן.** ציון גבוה אומר בדיוק
95% שהפקודות אינן שותקות. ציון נמוך **אינו** אומר „אין כלל":

    ציון < 0.10:  32 שאלות, 53% באמת NO_SUCH_RULE  (שיעור-בסיס 31%)

53% מול 31% הוא הטיה, לא הכרעה. ⇒ **המודול לעולם אינו מחזיר „אין כלל".**
הוא מחזיר `COVERED` או `UNCLEAR`, בדיוק כמו ש-`out_of_scope` מחזיר `None`
במקום לנחש דלת. הכרעת „אין כלל" נעשית בבוררות, בתשלום, ולא כאן.

⚠ **וכיול על סט אחד מאשר את עצמו.** הספים נגזרו מפיילוט-150 בלבד. הם ראויים
לאמון כתור-עבודה מדורג, לא כפסק על שאלה בודדת. ⏰ סבב-בוררות על סט אחר —
לאמת מולו לפני שנשענים על המספרים האלה.

⚡ **ועוד ממצא מאותו כיול, שווה לזכור:** `top_score` — הביטחון של האחזור
בעצמו — נתן **AUC 0.500 בדיוק**. אפס מידע על אם הוא מצא את הדבר הנכון.
גם `context_words` (0.473) ומספר-המסמכים (0.465). הביטחון של האחזור אינו
עדות לכלום, ולכן שום שער אינו יכול להישען עליו.

טהור: json ו-stdlib בלבד, בלי Streamlit, בלי רשת, בלי קריאת API.
"""
from __future__ import annotations

import json
import math
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import scope_routes
from common import safe_print
from night import config as C

# The two routing markers the prompt dictates. The text after one of them is
# the app's own statement of what it lacked.
MARKERS = tuple(m for m in (getattr(scope_routes, "MARK_MISSING", ""),
                            getattr(scope_routes, "MARK_OUT_OF_SCOPE", "")) if m)

NGRAM = 4
# 0.130 buys 94.7% precision on the arbitrated 107 against a 69.2% base rate,
# and still reaches half of them. Raising it to 0.150 buys the last 5 points of
# precision for 20 points of coverage -- a worse trade for a work queue.
COVERED_AT = 0.130

# The declaration is one sentence. More than this is the answer's next section
# bleeding in, which drags in that section's vocabulary and inflates the match.
DECL_CHARS = 400

# A clause too short to carry 20 distinct 4-grams cannot be matched reliably;
# it would win on a single shared word.
MIN_GRAMS = 20

# log(n/(1+df)) goes to zero and then negative once a gram sits in more than
# half the units, and a negative weight PENALISES a match -- backwards. On the
# 2,389-clause corpus the floor never binds: the commonest gram (" או ") weighs
# 0.48. It binds only on an index too small to weight at all, where it degrades
# to plain overlap instead of silently scoring everything zero.
MIN_IDF = 0.01

_HEB = re.compile(r"[^֐-׿ ]+")
_WS = re.compile(r"\s+")

_INDEX: tuple[list[tuple[str, frozenset]], dict[str, float], float] | None = None


def grams(text: str) -> frozenset:
    """Character 4-grams over Hebrew letters and spaces only."""
    t = _WS.sub(" ", _HEB.sub(" ", text or "")).strip()
    return frozenset(t[i:i + NGRAM] for i in range(len(t) - NGRAM + 1))


def declaration_of(answer: str) -> str:
    """What the answer said it lacked. Empty when it claimed no lack."""
    out = []
    for m in MARKERS:
        if m in (answer or ""):
            out.append(answer.split(m, 1)[1][:DECL_CHARS])
    return " ".join(out).strip()


def build_index(docs: list[dict]) -> tuple[list[tuple[str, frozenset]], dict[str, float], float]:
    """Curated clauses only -- raw_text scored worse (AUC 0.659 vs 0.718) because
    a 1,500-character window shares vocabulary with everything."""
    units: list[tuple[str, frozenset]] = []
    for d in docs:
        did = d.get("document_id") or ""
        for sec in d.get("sections") or []:
            for c in sec.get("clauses") or []:
                g = grams(f"{c.get('number') or ''} {c.get('text') or ''}")
                if len(g) >= MIN_GRAMS:
                    units.append((did, g))
    df: Counter = Counter()
    for _, g in units:
        df.update(g)
    n = float(len(units)) or 1.0
    return units, {k: max(MIN_IDF, math.log(n / (1 + v))) for k, v in df.items()}, n


def score(declaration: str, index) -> tuple[float, str]:
    """Best-matching curated clause: (score, its document id)."""
    units, idf, n = index
    q = grams(declaration)
    if not q:
        return 0.0, ""
    default = math.log(n)
    qw = {g: idf.get(g, default) for g in q}
    nq = math.sqrt(sum(v * v for v in qw.values())) or 1.0
    best, best_doc = 0.0, ""
    for did, g in units:
        inter = q & g
        if not inter:
            continue
        s = sum(qw[x] for x in inter) / (nq * math.sqrt(len(g)))
        if s > best:
            best, best_doc = s, did
    return best, best_doc


def verdict(s: float) -> str:
    """COVERED or UNCLEAR. Never "no such rule" -- see the module docstring:
    a low score is a lean (53% against a 31% base), not a finding.

    ⚠ The score carries the index's IDF scale, so `COVERED_AT` means what it
    was calibrated to mean only against a production-scale index. On a handful
    of clauses every weight collapses to `MIN_IDF` and the same declaration
    scores two orders of magnitude lower -- the ranking still holds, the
    absolute number does not.
    """
    return "COVERED" if s >= COVERED_AT else "UNCLEAR"


def _load_corpus() -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8"))
            for p in sorted(Path("storage/json_store").glob("*.json"))]


def triage(rows: list[dict], index=None) -> list[dict]:
    """Every zero-scoring answer that declared a lack, ranked by how strongly
    the corpus already carries what it said it was missing."""
    global _INDEX
    if index is None:
        if _INDEX is None:
            _INDEX = build_index(_load_corpus())
        index = _INDEX
    out = []
    for r in rows:
        g = r.get("grade") or {}
        if int(g.get("answered_parts") or 0) or not (g.get("parts") or []):
            continue
        decl = declaration_of(r.get("answer") or "")
        if not decl:
            continue
        s, doc = score(decl, index)
        out.append({"id": r.get("id"),
                    "q": (r.get("clean_q") or r.get("q") or "")[:90],
                    "declaration": decl.strip()[:120],
                    "score": round(s, 4), "best_doc": doc,
                    "in_window": doc in set(r.get("sources") or []),
                    "verdict": verdict(s)})
    out.sort(key=lambda d: -d["score"])
    return out


def main(tags: list[str]) -> int:
    for tag in tags:
        rows = C.read_jsonl(C.OUT / f"grades_{tag}.jsonl")
        if not rows:
            safe_print(f"[why] no grades for {tag}")
            continue
        t = triage(rows)
        covered = [d for d in t if d["verdict"] == "COVERED"]
        shown = [d for d in covered if d["in_window"]]
        safe_print(f"\n[why] {tag}: {len(t)} answers declared a lack")
        safe_print(f"       COVERED — the orders are not simply silent here, ~95% "
                   f"precise: {len(covered)}   UNCLEAR: {len(t) - len(covered)}")
        safe_print(f"       of the COVERED, {len(shown)} had that very document in the "
                   f"window already — block depth, not retrieval\n")
        for d in covered[:15]:
            flag = "in-window " if d["in_window"] else "NOT-fetched"
            safe_print(f"  {d['score']:.3f} [{flag}] {d['best_doc']:<12} {d['q'][:58]}")
            safe_print(f"          lacked: {d['declaration'][:78]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:] or ["pilot150"]))
