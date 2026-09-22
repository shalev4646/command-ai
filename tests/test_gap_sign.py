# -*- coding: utf-8 -*-
"""out_of_scope.declares_gap — the one predicate behind the chip, the strip and
the door (22.09, rs041).

rs041 ("כמה ימים חופש חולים מגיע לי על שפעת?") on the grade-blocks6 arm: the
question matches `medical_scope`, the kept (second-pass) answer opens
"**תשובה:** אין בפקודות מספר ימים קבוע…" and never writes the rule-2א marker
line — so report_goal.door() returned None before asking the family, the app
rendered no chip and no strip, and the question counted as "no door" in the
served tally. The 18.09 chip bug had the same root: three surfaces reading the
model's sign each for themselves.

Measured free before the predicate was written (every graded arm and probe
file on disk, arbitration wins; 269 fully answered + 29 partial marker-less
answers): the refusal sentence at the top and the explicit negative opening
fire on ZERO answered answers; "הפקודות אינן קובעות X" and the bold label
"מה הפקודות לא קובעות" fire on answered ones and are NOT signs. Pinned here.

    venv\\Scripts\\python.exe tests\\test_gap_sign.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import out_of_scope as OS
import scope_routes
from night import doorgate as DG
from night import report_goal as G

RS041_Q = "כמה ימים חופש חולים מגיע לי על שפעת?"
RS041_KEPT = ("**תשובה:** אין בפקודות מספר ימים קבוע ל\"שפעת\" או לכל מחלה מסוימת — ימי המחלה "
              "(ימי ג) ניתנים לפי קביעת הרופא לגופו של מקרה.\n\n"
              "**מקור:**\n- \"יום ב ויום ג ניתנים על ידי רופא\" (טיפול רפואי בחייל).\n\n"
              "**מה הפקודות לא קובעות:** אין בקטעים ערך מספרי של ימי מחלה המשויך למחלה מסוימת.")
RS041_FIRST = ("**פסיקה:** המידע לא קיים בפקודות שסופקו.\n\nהקטעים עוסקים בהקשר אחר.\n\n"
               + scope_routes.MARK_MISSING + " הכללים הקובעים את מספר ימי חופשת המחלה.")
FULL = ("**פסיקה:** זכאי בתנאים\n**מקור:** פ\"מ 35.0402\n\nחייל זכאי לחופשה מיוחדת של שבעה ימים.\n\n"
        "**מה הפקודות לא קובעות:** את מועד תחילת החופשה — זה בסמכות המפקד.")
# rs040 on clean3: a FULL answer that opens with a negative about the framing
RS040_FULL = ("**תשובה:** הפקודות אינן קובעות מכסת חופשה חודשית, אלא **מכסה שנתית**: "
              "אתה זכאי ל-18 ימי חופשה בשנה.")
LATE_REFUSAL = ("**פסיקה:** פטור בתנאים — מהמסדר.\n**מקור:** פ\"מ 33.0202.\n\n"
                "לגבי החלק השני של השאלה — המידע לא קיים בפקודות שסופקו.")


def test_the_two_markers_are_a_gap():
    assert OS.declares_gap(RS041_FIRST) == "marker"
    assert OS.declares_gap("שורה\n" + scope_routes.MARK_OUT_OF_SCOPE + " חוק אזרחי") == "marker"


def test_the_refusal_sentence_at_the_top_is_a_gap_and_late_is_not():
    """The chip's own rule, now shared: the mandated sentence within the first
    80 characters. Later in the text it is a scope caveat on a real answer."""
    assert OS.declares_gap("המידע לא קיים בפקודות שסופקו. השאלה אינה ברורה.") == "refusal"
    assert OS.declares_gap("**פסיקה:** המידע לא קיים בפקודות שסופקו — לגבי תפיסת טלפון.") == "refusal"
    assert OS.declares_gap(LATE_REFUSAL) is None


def test_an_explicit_negative_opening_is_a_gap():
    assert OS.declares_gap(RS041_KEPT) == "negative"
    assert OS.declares_gap("אין בקטעים כלל שעניינו הוצאת ילד מבית ספר.") == "negative"
    assert OS.declares_gap("**פסיקה:** לא נמצא בפקודות כלל על כך.") == "negative"


def test_the_measured_non_signs_stay_silent():
    """'הפקודות אינן קובעות X' opens full answers (rs040, q00273) and the bold
    label rides on 96 answered answers — neither may open a door."""
    assert OS.declares_gap(RS040_FULL) is None
    assert OS.declares_gap(FULL) is None
    assert OS.declares_gap("") is None and OS.declares_gap(None) is None


def test_the_opening_claim_looks_past_markup_and_labels():
    assert OS.opening_claim("**תשובה:** אין בפקודות מספר") == "אין בפקודות מספר"
    assert OS.opening_claim("  \n**פסיקה:** **אסור**") == "אסור"


def test_report_goal_door_reaches_the_family_for_rs041():
    assert G.door(RS041_KEPT, RS041_Q) == "medical_scope"
    assert G.door(RS041_FIRST, RS041_Q) == "medical_scope", "the marker path is unchanged"
    assert G.door(FULL, RS041_Q) is None, "a full answer never earns a door"


def test_rs041_is_served_not_stranded():
    row = {"id": "rs041", "answer": RS041_KEPT, "clean_q": RS041_Q, "q": RS041_Q,
           "grade": {"level": "led_known", "parts": ["מספר ימי חופש חולים"], "answered_parts": 0}}
    d = G.tally([row], {"rs041": "NOT_IN_CORPUS"})
    assert d["credited"] == ["rs041"] and d["stranded"] == [], d
    assert d["served"] == 1 and d["goal"] == 1, d


def test_the_doorgate_sign_gate_counts_full_answers_only():
    """A fully answered, marker-less answer the predicate fires on is a breach;
    a partial answer that says what the orders do not set is reported, not
    counted (rule 2 asks for exactly that sentence there)."""
    rows = [
        {"id": "a", "_arm": "t", "clean_q": "שאלה מלאה?", "answer": RS040_FULL,
         "grade": {"parts": ["x"], "answered_parts": 1}},
        {"id": "b", "_arm": "t", "clean_q": "שאלה חלקית?",
         "answer": "**פסיקה:** לא נמצא בפקודות מי מאשר; החלק השני נענה.",
         "grade": {"parts": ["x", "y"], "answered_parts": 1}},
        {"id": "c", "_arm": "t", "clean_q": "שאלה מלאה שנפתחת בשלילה?",
         "answer": "אין בפקודות כלל כזה.", "grade": {"parts": ["x"], "answered_parts": 1}},
        {"id": "d", "_arm": "t", "clean_q": RS041_Q, "answer": RS041_KEPT,
         "grade": {"parts": ["x"], "answered_parts": 1}},
    ]
    breaches, partial, n_full = DG.gap_sign_breaches(rows, no_rule={RS041_Q})
    assert n_full == 2, n_full                       # a and c; d is adjudicated no-rule
    assert [b[1] for b in breaches] == ["t:c"], breaches
    assert [p[1] for p in partial] == ["t:b"], partial


if __name__ == "__main__":
    import inspect
    fns = [f for n, f in sorted(globals().items()) if n.startswith("test_") and inspect.isfunction(f)]
    for f in fns:
        f()
        print("PASS", f.__name__)
    print(f"{len(fns)} passed")
