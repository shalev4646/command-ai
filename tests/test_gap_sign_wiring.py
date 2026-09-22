# -*- coding: utf-8 -*-
"""rs041 end to end: an answer that declares its gap in words, without the
rule-2א marker line, must still get the neutral chip and its verified door.

Renders the real app.py through AppTest with a fake stream_ai_answer (the
technique of tests/test_second_pass_wiring.py) and reads what was drawn:

  - the rs041-shaped kept answer ("**תשובה:** אין בפקודות מספר ימים קבוע…")
    on a medical question renders the "לאן כן פונים" strip with the medical
    door and the neutral ⓘ chip;
  - a full ruling on the same question renders neither.

    venv\\Scripts\\python.exe tests\\test_gap_sign_wiring.py
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
_TOS_VERSION = int(re.search(r"^TOS_VERSION = (\d+)",
                             (ROOT / "app.py").read_text(encoding="utf-8"), re.M).group(1))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# app.py's _startup_ingest ingests every un-ingested PDF THROUGH THE PAID API;
# app.py binds the names at import, so patching the module attributes first is
# what keeps this test free. Same for persisting the embedding cache.
import backend
backend.ensure_pdfs_ingested = lambda *a, **k: None
backend.warm_index = lambda *a, **k: 0
import storage.vector_store as vs
vs._save_emb_cache = lambda: None

import metrics
metrics.reserve = lambda *a, **k: "ok"
metrics.refund = lambda *a, **k: None
metrics.log_question = lambda **kw: None      # never touch Sheets from a test

import out_of_scope as OS
from streamlit.testing.v1 import AppTest

RS041_Q = "כמה ימים חופש חולים מגיע לי על שפעת?"
RS041_KEPT = ("**תשובה:** אין בפקודות מספר ימים קבוע ל\"שפעת\" או לכל מחלה מסוימת — ימי המחלה "
              "(ימי ג) ניתנים לפי קביעת הרופא לגופו של מקרה.\n\n"
              "**מקור:**\n- \"יום ב ויום ג ניתנים על ידי רופא\" (טיפול רפואי בחייל).\n\n"
              "**מה הפקודות לא קובעות:** אין בקטעים ערך מספרי של ימי מחלה המשויך למחלה מסוימת.")
FULL = ("**פסיקה:** זכאי בתנאים\n**מקור:** פ\"מ 61.0104 — טיפול רפואי בחייל\n\n"
        "ימי ג ניתנים על ידי רופא; מספרם לפי קביעתו.")
SRC = [{"doc_id": "61.0104", "title": "טיפול רפואי בחייל", "civil_source": False,
        "civil_label": "", "source_file": "", "clause": None, "highlight": "",
        "superseded": False, "superseded_note": ""}]


def _fake(text):
    def fake(question, history=None, role="soldier", profile=None, first_answer=None):
        usage = {}

        def gen():
            yield text
            usage.update({"input_tokens": 10, "output_tokens": 20, "truncated": False})
        return gen(), SRC, "api-content", usage
    return fake


def _run(text, question=RS041_Q):
    backend.stream_ai_answer = _fake(text)
    backend.RETRIEVE_SECOND_PASS = 0
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=120)
    at.session_state["role"] = "soldier"
    at.session_state["name_asked"] = True
    at.session_state["tos_ok"] = _TOS_VERSION
    at.session_state["conversation_history"] = []
    at.session_state["messages"] = []
    at.session_state["pending_question"] = question
    at.run()
    return at


def _drawn(at) -> str:
    return "\n".join(str(getattr(m, "value", "")) for m in at.markdown)


def test_the_question_has_a_medical_door_and_the_kept_answer_declares_its_gap():
    assert OS.family_of(RS041_Q) == "medical_scope"
    assert OS.declares_gap(RS041_KEPT) == "negative"


# the rendered markup, not the CSS: app.py's stylesheet (also an st.markdown)
# names every chip class, so only the chip's own element proves it was drawn
STRIP = "לאן כן פונים</span>"
NEUTRAL_CHIP = 'class="verdict-chip verdict-none"'


def test_rs041_renders_the_medical_door_and_the_neutral_chip():
    at = _run(RS041_KEPT)
    drawn = _drawn(at)
    dest = OS.destination_for(RS041_Q)
    assert STRIP in drawn, "no referral strip was drawn"
    assert dest["label"] in drawn, f"the strip is not the medical door: {dest['label']}"
    assert NEUTRAL_CHIP in drawn and ">ⓘ לא נמצא בפקודות<" in drawn, "no neutral chip"
    msg = [m for m in at.session_state["messages"] if m["role"] == "assistant"][-1]
    assert msg["content"] == RS041_KEPT, "the answer text itself must not be touched"


def test_a_full_ruling_gets_neither_strip_nor_neutral_chip():
    at = _run(FULL)
    drawn = _drawn(at)
    assert STRIP not in drawn, "a full ruling must not earn a door"
    assert NEUTRAL_CHIP not in drawn
    assert 'class="verdict-chip verdict-cond' in drawn or 'class="verdict-chip verdict-yes' in drawn, \
        "the ruling chip should render"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
    print("all gap-sign wiring tests passed")
