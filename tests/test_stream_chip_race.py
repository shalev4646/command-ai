# -*- coding: utf-8 -*-
"""The streamed chip must be cut from a COMPLETE ruling line.

_stream_answer holds the stream until the first line is decided, then draws
the verdict chip off the buffer and streams the body under it. It used to
take the first newline as that signal. When a preface line precedes the
ruling line ("לגבי שאלתך:\\n**פסיקה:** מותר בתנאים — …"), the first newline
lands while the ruling line is half-streamed, and the chip is cut from
"**פסיקה:** מותר" — a green ✓ where the full line is an amber ⚠ — until the
rerun redraws it (independent review of d27ef44, 30.09.2026). The buffer is
settled only when the ruling line, if one has started, has ended too.

None of the 258 unique recorded answers in night/out (final161v2, opus5v2)
carries a preface before its ruling line, so on every one of them the moment
the chip is drawn — and the chip — is exactly what it was.

    venv\\Scripts\\python.exe tests\\test_stream_chip_race.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import backend  # noqa: E402

backend.ensure_pdfs_ingested = lambda *a, **k: []   # app.py ingests on import otherwise (test_scope_routes.py)

import app  # noqa: E402

APP_SRC = (ROOT / "app.py").read_text(encoding="utf-8")

PREFACE = "לגבי שאלתך על יציאה מהבסיס:\n"
RULING = "**פסיקה:** מותר בתנאים — באישור המפקד הישיר.\n"
BODY = "\n**מקור:** פ\"מ 33.0213 סעיף 4\n"


def _first_chip(chunks, settled):
    """What _stream_answer draws: buffer chunks until `settled` says the first
    line is decided (or the 400-char guard hits), then cut the chip."""
    buf = ""
    for c in chunks:
        buf += c
        if settled(buf) or len(buf) > 400:
            break
    return app._verdict_chip(buf, "")[0]


def _old(buf):
    return "\n" in buf


def test_the_predicate_waits_for_the_ruling_line_to_end():
    s = app._first_line_settled
    assert s("**פסיקה:** מותר\n") is True
    assert s("**פסיקה:** מותר") is False
    assert s(PREFACE + "**פסיקה:** מותר") is False
    assert s(PREFACE + RULING) is True
    assert s("שלום") is False and s("שלום\n") is True
    assert s("המידע לא קיים בפקודות שסופקו.\n") is True


def test_a_preface_no_longer_yields_a_chip_cut_mid_line():
    """The race, reproduced: the same chunks give ✓ under the old signal and
    the full line's ⚠ under the new one."""
    chunks = [PREFACE + "**פסיקה:** מותר", " בתנאים — באישור המפקד הישיר.\n", BODY]
    full = app._verdict_chip("".join(chunks), "")[0]
    assert full and "verdict-cond" in full
    old = _first_chip(chunks, _old)
    assert old and "verdict-yes" in old, "the race: a green chip off a half line"
    assert _first_chip(chunks, app._first_line_settled) == full


def test_without_a_preface_the_moment_and_the_chip_are_unchanged():
    """Ruling line first, or no ruling line at all: the new signal fires on
    exactly the same buffers the old one did — except while a second line
    still reads as a possible ruling marker ("*", "**", "**פ"…), where it
    waits those few characters. The chip cut from either buffer is the same."""
    for text in (RULING + BODY,
                 "המידע לא קיים בפקודות שסופקו.\n\nהקטעים עוסקים בנושא אחר.",
                 "**תשובה:** פונים למת\"ש.\n\n**מקור:** x"):
        delayed = 0
        for i in range(1, len(text) + 1):
            buf = text[:i]
            new, old = app._first_line_settled(buf), _old(buf)
            if new != old:
                tail = buf.rsplit("\n", 1)[1]
                assert old and not new and tail and "**פסיקה:**".startswith(tail), buf
                assert app._verdict_chip(buf, "")[0] == app._verdict_chip(text, "")[0]
                delayed += 1
        assert delayed <= len("**פסיקה:**"), text
    assert app._first_line_settled(PREFACE + "**פסי") is False           # marker mid-stream
    assert app._first_line_settled(PREFACE + "**פסיקה:**") is False      # marker, no clause yet
    assert app._first_line_settled(PREFACE + "**מקור:** x") is True      # another label


def test_a_ruling_line_that_never_ends_still_gets_its_chip():
    """No hang and no lost chip: a ruling line with no newline is cut at the
    400-char guard as before, and a stream that simply ends is parsed whole."""
    endless = PREFACE + "**פסיקה:** אסור — " + "סייג ארוך מאוד, " * 40
    assert "\n" not in endless[len(PREFACE):] and len(endless) > 400
    assert app._first_line_settled(endless) is False
    chunks = [endless[i:i + 37] for i in range(0, len(endless), 37)]
    chip = _first_chip(chunks, app._first_line_settled)
    assert chip and "verdict-no" in chip                      # cut by the 400 guard
    short = [PREFACE + "**פסיקה:** אסור", " — הסיבה."]         # stream ends mid-line
    buf = ""
    for c in short:
        buf += c
        assert not (app._first_line_settled(buf) or len(buf) > 400)
    assert app._verdict_chip(buf, "")[0] and "verdict-no" in app._verdict_chip(buf, "")[0]


def test_stream_answer_uses_the_predicate_at_both_gates():
    src = APP_SRC[APP_SRC.index("def _stream_answer("):APP_SRC.index("def _paint(")]
    assert src.count("_first_line_settled(buf) or len(buf) > 400") == 2, "the buffering loop and the parse gate"
    assert '"\\n" in buf or len(buf) > 400' not in src


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
    print("all stream-chip-race tests passed")
