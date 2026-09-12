# -*- coding: utf-8 -*-
"""שחזור-שיחה מהמכשיר (2026-09-12).

נמדד באותו יום על האפליקציה האמיתית: הקפאת ה-web view ל-10, 60 ו-150 שניות
השאירה את השיחה חיה, בעוד טעינה-מחדש אחת הורידה אותה משתי בועות לאפס —
כי `messages`/`conversation_history` חיים רק ב-session_state. הבדיקות כאן
נועלות את שלושת החלקים של התיקון: מי קורא, מי כותב, ומי מחכה למי.
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
APP = (ROOT / "app.py").read_text(encoding="utf-8")


def test_chat_probe_component_exists_and_only_reads():
    """הרכיב קורא את המכשיר ומחזיר מחרוזת — ולא כותב, כדי שלא יתחרה במראה."""
    html = (ROOT / "components" / "chat_probe" / "index.html").read_text(encoding="utf-8")
    assert 'window.top.localStorage.getItem("cai_chat")' in html
    assert "setItem" not in html, "הצד הקורא לא כותב"
    assert "streamlit:setComponentValue" in html and "streamlit:componentReady" in html
    assert '_chat_probe = components.declare_component(' in APP
    assert '"cai_chat_probe"' in APP and '"components" / "chat_probe"' in APP


def test_restore_runs_once_per_session_and_never_overwrites_live_state():
    """הדגל הוא פר-סשן: אחרי שהמשתמש מוחק שיחה אסור לשחזר לו אותה בחזרה."""
    i = APP.index("if not st.session_state.get(\"cai_chat_restored\"):")
    blk = APP[i:i + 1800]
    assert "_chat_probe(default=None)" in blk
    assert "st.session_state.cai_chat_restored = True" in blk
    assert "if isinstance(_msgs, list) and not st.session_state.messages:" in blk
    assert "if isinstance(_hist, list) and not st.session_state.conversation_history:" in blk
    assert 'm.get("role") in ("user", "assistant")' in blk, "מסננים תוכן זר מהמכשיר"


def test_the_probe_is_skipped_when_the_device_has_no_chat():
    """המחיר האמיתי של הסיבוב הוא ריצה נוספת של הסקריפט. דגל בעוגייה (שמגיעה
    עם לחיצת-היד, בלי סיבוב) אומר אם יש בכלל מה לשחזר — ומי שאין לו נכנס
    מהר בדיוק כמו לפני התכונה."""
    assert '_chat_maybe_saved = bool(_ck.get("ch")) or not _ck' in APP
    i = APP.index("_chat_maybe_saved = ")
    blk = APP[i:i + 400]
    assert 'if not st.session_state.get("cai_chat_restored") and not _chat_maybe_saved:' in blk
    assert "st.session_state.cai_chat_restored = True" in blk
    assert blk.index("_chat_maybe_saved:") < blk.index("_chat_probe(default=None)"), \
        "השער קודם לרכיב"
    # the flag itself is written only when there is a chat, so every other
    # user's cookie payload is untouched
    j = APP.index('_ck_dict["ch"] = 1')
    assert "if st.session_state.messages or st.session_state.conversation_history:" in APP[j - 120:j]


def test_curtain_waits_for_the_restore():
    """הווילון לא עולה לפני שהשיחה חזרה — אחרת המסך נבנה מחדש בגלוי."""
    m = re.search(r"^_boot_settled = .*?\n(?=\n|def )", APP, re.M | re.S)
    assert m, "לא נמצא _boot_settled"
    assert 'st.session_state.get("cai_chat_restored")' in m.group(0)


def test_mirror_writes_at_the_exit_points_only_after_the_restore():
    i = APP.index("def _mirror_chat_to_device()")
    body = APP[i:APP.index("def _emit_boot_settled()", i)]
    assert 'if not st.session_state.get("cai_chat_restored"):\n        return' in body, \
        "בלי השער הזה מראה ריקה דורסת את הזיכרון בזמן שהבדיקה עוד בדרך"
    assert "window.top.localStorage.setItem('cai_chat'," in body
    assert 'st.session_state.get("cai_chat_mirrored") == payload' in body, "כתיבה רק כשמשהו השתנה"
    assert "except (TypeError, ValueError):" in body, "הודעה לא-סריאליזבילית לא מפילה את האפליקציה"
    # called exactly where the script ends, so the answer of this run is included
    emit = APP[APP.index("def _emit_boot_settled()"):]
    emit = emit[:emit.index("\n\n")]
    assert "_mirror_chat_to_device()" in emit
    assert APP.count("_emit_boot_settled()") >= 3, "שתי נקודות-יציאה + ההגדרה"


def test_payload_is_trimmed_archive_first():
    i = APP.index("def _mirror_chat_to_device()")
    body = APP[i:APP.index("def _emit_boot_settled()", i)]
    assert body.index("while len(payload.encode(\"utf-8\")) > _CHAT_MAX_BYTES and hist:") < \
           body.index("while len(payload.encode(\"utf-8\")) > _CHAT_MAX_BYTES and len(msgs) > 2:"), \
        "הארכיון נחתך לפני השיחה שעל המסך"
    assert "_CHAT_MAX_BYTES = 200_000" in APP and "_CHAT_MAX_ARCHIVE = 5" in APP


def test_the_trim_actually_fits_a_huge_chat():
    """לוגיקת-החיתוך עצמה, מחוץ ל-Streamlit: שיחה ענקית חייבת להיכנס לתקרה."""
    MAX = 200_000
    msgs = [{"role": "user", "content": "ש" * 900} for _ in range(200)]
    hist = [{"title": "t", "messages": [{"role": "user", "content": "ש" * 2000}]} for _ in range(5)]
    payload = json.dumps({"v": 1, "m": msgs, "h": hist}, ensure_ascii=False)
    assert len(payload.encode("utf-8")) > MAX, "הקלט לבדיקה חייב לחרוג מהתקרה"
    while len(payload.encode("utf-8")) > MAX and hist:
        hist = hist[:-1]
        payload = json.dumps({"v": 1, "m": msgs, "h": hist}, ensure_ascii=False)
    while len(payload.encode("utf-8")) > MAX and len(msgs) > 2:
        msgs = msgs[2:]
        payload = json.dumps({"v": 1, "m": msgs, "h": hist}, ensure_ascii=False)
    assert len(payload.encode("utf-8")) <= MAX
    assert msgs, "תמיד נשאר משהו על המסך"


def test_clearing_history_reaches_the_device():
    i = APP.index("def _clear_history()")
    body = APP[i:i + 700]
    assert "st.session_state.conversation_history = []" in body
    assert "st.session_state.messages = []" in body
    # the mirror runs at the end of the same run and writes the empty chat;
    # the gate in the mirror is what makes an empty write safe
    assert "cai_chat_restored" in APP[APP.index("def _mirror_chat_to_device()"):
                                      APP.index("def _emit_boot_settled()")]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("PASS", name)
