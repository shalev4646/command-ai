# -*- coding: utf-8 -*-
"""Gates and target selection for night.anchors — no API calls anywhere here."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from night.anchors import (MIN_KEEP, NEVER, _flat_questions, check,
                           estimate_usd, targets)

KF = [{"id": "key-facts", "title": "עיקרי", "clauses": [
    {"number": "כמה חופשה מגיע", "text": "חייל זכאי לימי חופשה שנתית."}]}]

RAW = ("חייל בשירות סדיר זכאי לימי חופשה שנתית. את היציאה לחופשה מאשר מפקד "
       "היחידה. חופשה שאושרה ובוטלה תוחזר למכסה. אפשר לצבור ימי חופשה "
       "משנה לשנה באישור. בזמן תרגיל חופשות מוקפאות. בקשה לחופשה מיוחדת "
       "מוגשת למפקד. יציאה לחוץ לארץ בחופשה טעונה אישור. חופשת שחרור "
       "ניתנת לקראת תום השירות.")

GOOD = [
    "כמה ימי חופשה מגיעים לי בשנה",
    "מי צריך לאשר לי יציאה לחופשה",
    "מה עושים כשמבטלים לי חופשה מאושרת",
    "האם אפשר לצבור ימי חופשה משנה לשנה",
    "מה קורה לחופשה שלי בזמן תרגיל",
    "איך מגישים בקשה לחופשה מיוחדת",
    "האם מותר לי לנסוע לחוץ לארץ בחופשה",
    "מתי מקבלים חופשת שחרור בסוף השירות",
]


def doc(**over):
    d = {"document_id": "99.0001", "raw_text": RAW, "sections": KF,
         "suggested_questions": ["שאלה קיימת על חופשה שנתית"],
         "title": "חופשות"}
    d.update(over)
    return d


def test_accepts_a_clean_set():
    kept, problems, warnings = check(GOOD, doc(), eval_qs=[])
    assert kept == GOOD
    assert problems == [] and warnings == []


def test_drops_short_and_exact_duplicate():
    qs = ["מה זה חופשה", "שאלה קיימת על חופשה שנתית"] + GOOD
    kept, problems, warnings = check(qs, doc(), eval_qs=[])
    assert kept == GOOD
    assert problems == []
    assert any("short" in w for w in warnings)
    assert any("duplicate" in w for w in warnings)


def test_drops_benchmark_mirror():
    evals = ["כמה ימי חופשה מגיעים לחייל בשנה"]
    kept, problems, warnings = check(GOOD, doc(), eval_qs=evals)
    assert GOOD[0] not in kept
    assert any("mirrors" in w for w in warnings)
    assert problems == []          # 7 survivors, still above MIN_KEEP


def test_drops_risk_topic_the_order_is_silent_about():
    qs = GOOD + ["האם מגיע לי פיצוי כספי על ביטול"]
    kept, _, warnings = check(qs, doc(), eval_qs=[])
    assert all("פיצוי" not in q for q in kept)
    assert any("silent" in w for w in warnings)


def test_risk_topic_present_in_raw_is_allowed():
    d = doc(raw_text=RAW + " במקרה ביטול ישולם פיצוי כספי לחייל.")
    qs = GOOD[:7] + ["האם מגיע לי פיצוי כספי על ביטול"]
    kept, problems, _ = check(qs, d, eval_qs=[])
    assert qs[-1] in kept and problems == []


def test_too_few_survivors_is_a_problem():
    kept, problems, _ = check(GOOD[:3], doc(), eval_qs=[])
    assert len(kept) == 3 < MIN_KEEP
    assert problems and "3" in problems[0]


def test_targets_selection_and_order():
    docs = [
        doc(document_id="35.0002"),
        doc(document_id="31.0001"),
        doc(document_id="33.0003", anchor_questions=["כבר יש עוגן אחד כאן"]),
        doc(document_id="34.0004", sections=[]),
        doc(document_id="20.0502"),                     # standing exclusion
        {"raw_text": RAW, "sections": KF},              # no id at all
    ]
    assert [d["document_id"] for d in targets(docs)] == ["31.0001", "35.0002"]
    assert "20.0502" in NEVER


def test_flat_questions_handles_both_formats():
    flat = doc(suggested_questions=["א שאלה", "ב שאלה"])
    role = doc(suggested_questions={"soldier": ["א שאלה"], "commander": ["ג שאלה"]},
               anchor_questions=["עוגן ישן"])
    assert _flat_questions(flat) == ["א שאלה", "ב שאלה"]
    assert _flat_questions(role) == ["א שאלה", "ג שאלה", "עוגן ישן"]


def test_estimate_batch_is_half_of_sync():
    d = doc()
    sync, batch = estimate_usd(d), estimate_usd(d, batch=True)
    assert sync > 0
    assert abs(batch - sync / 2) < 1e-12


if __name__ == "__main__":
    # Plain-assert runner, matching every other suite in tests/ (see
    # test_scope_routes.py: without it the file imports, runs nothing, exits 0).
    failures = 0
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            try:
                _fn()
                print("PASS", _name)
            except AssertionError as _exc:
                failures += 1
                print("FAIL", _name, "-",
                      str(_exc).encode("ascii", "replace").decode())
    sys.exit(1 if failures else 0)
