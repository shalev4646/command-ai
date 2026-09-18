"""Boot app.py with a stubbed backend, isolated from the shared venv.

    python tools/ui_audit/run_stub.py [--root DIR] [--port 8851]

Two things are replaced before app.py runs:

* backend — wholesale. The corpus/ONNX stack is not needed to look at a
  screen (the repo's own pattern, tests/test_scope_routes.py), and booting the
  real one runs _startup_ingest, which ingests any new PDF through the PAID
  API. A name app.py asks for that is missing here fails loudly, never as a
  silent default.
* Streamlit's static directory. app.py re-patches static/index.html on every
  boot (boot_shell stamps the file with a hash of its own markup), so serving
  two code versions from ONE venv makes them overwrite each other's boot
  shell — and every other dev server on the machine then serves the wrong
  one. The stub copies static/ to a private directory and points both
  Streamlit and boot_shell at the copy.

--root serves another checkout (e.g. a worktree of an older branch) through
this same stub.
"""
import argparse
import hashlib
import os
import shutil
import sys
import tempfile
import types
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("--root", default=str(Path(__file__).resolve().parents[2]),
                help="checkout whose app.py is served (default: this repo)")
ap.add_argument("--port", type=int, default=8851)
ap.add_argument("--address", default="127.0.0.1",
                help="0.0.0.0 to reach it from a phone on the same Wi-Fi")
ap.add_argument("--static-dir", default="",
                help="private copy of streamlit/static (default: under the temp dir)")
args = ap.parse_args()

ROOT = Path(args.root).resolve()
# the checkout's .streamlit/config.toml (theme) is read from the working dir
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

b = types.ModuleType("backend")
b.__file__ = str(ROOT / "backend.py")
b.ensure_pdfs_ingested = lambda *a, **k: None
b.warm_index = lambda *a, **k: None
b.load_documents = lambda *a, **k: []
b.get_loaded_docs_info = lambda *a, **k: []
b.get_suggested_questions = lambda *a, **k: [
    "כמה שעות שינה מגיעות לחייל ביממה לפי הפקודה?",
    "האם מפקד רשאי למנוע ממני יציאה לחופשה?",
]
b.get_pdf_bytes = lambda *a, **k: None

# Canned answers, one per shape the answer screen draws, picked by a word in
# the question (sweep.py asks one of each). The real 4-tuple contract:
# (text deltas, sources, the user content sent, a usage dict filled at the end).
_SRC = {"doc_id": "35.0402", "title": "חופשות לחיילים בשירות חובה",
        "civil_source": False, "civil_label": "", "source_file": "", "clause": None,
        "highlight": "", "superseded": False, "superseded_note": ""}
_CANNED = {
    "חופשה": ("**פסיקה:** זכאי\n**מקור:** פ\"מ 35.0402 — חופשות\n\n"
              "חייל בשירות חובה זכאי לחופשה מיוחדת של עד שבעה ימים כשקרוב משפחה "
              "מדרגה ראשונה מאושפז.\n\n- הבקשה מוגשת למפקד הישיר\n"
              "- נדרש אישור רפואי על האשפוז", [_SRC]),
    "מלגה": ("**פסיקה:** לא נמצא\n\nבפקודות שסופקו אין מענה לשאלה הזו.\n\n"
             "**טרם במאגר:** זכאות חייל למלגת לימודים במהלך השירות.\n\n"
             "מה שנובע עבורך: לפנות למשקית ת\"ש ביחידה.", [dict(_SRC, doc_id="33.0352",
                                                               title="מניעת חופשה")]),
    "מטען": ("**פסיקה:** מותר בתנאים\n**מקור:** פ\"מ 58.0301 — הובלת מטען חורג\n\n"
             "מטען החורג מרוחב הרכב טעון אישור בכתב.",
             [dict(_SRC, doc_id="58.0301", title="הובלת מטען חורג", superseded=True,
                   superseded_note="הפקודה בוטלה ב-2024")]),
}


def _stream(question, *a, **k):
    text, sources = next((v for w, v in _CANNED.items() if w in (question or "")),
                         _CANNED["חופשה"])
    usage = {}

    def gen():
        for part in text.split("\n\n"):
            yield part + "\n\n"
        usage.update({"input_tokens": 10, "output_tokens": 20, "truncated": False})
    return gen(), sources, "stub", usage


b.stream_ai_answer = _stream
b._compose_user_content = lambda *a, **k: ""
b.lacked_from = lambda *a, **k: []
b.render_clause_image = lambda *a, **k: None
b.start_keep_warm = lambda *a, **k: None
# the second pass is off: the stub never answers, so there is nothing to redo
b.RETRIEVE_SECOND_PASS = 0
b.RETRIEVE_SECOND_PASS_KEEP_RULING = 0
b.second_answer_regressed = lambda *a, **k: False


def _missing(name):
    raise AttributeError(
        f"ui_audit stub backend has no {name!r} - add it to tools/ui_audit/run_stub.py")


b.__getattr__ = _missing
sys.modules["backend"] = b

import streamlit.file_util as _fu  # noqa: E402

shared = Path(_fu.get_static_dir())
static = Path(args.static_dir) if args.static_dir else (
    Path(tempfile.gettempdir()) / "cai_ui_audit_static"
    / hashlib.sha1(str(ROOT).encode()).hexdigest()[:10])
if static.exists():
    shutil.rmtree(static)
shutil.copytree(shared, static)
_fu.get_static_dir = lambda: str(static)

import boot_shell  # noqa: E402  (the checkout's own, via sys.path)

boot_shell._index_path = lambda: static / "index.html"
print(f"[run_stub] serving {ROOT / 'app.py'} on :{args.port}; static copy {static}",
      flush=True)

from streamlit import config  # noqa: E402
from streamlit.web import bootstrap  # noqa: E402

config.set_option("server.headless", True)
config.set_option("server.port", args.port)
config.set_option("server.address", args.address)
config.set_option("server.fileWatcherType", "none")
config.set_option("browser.gatherUsageStats", False)
bootstrap.run(str(ROOT / "app.py"), False, [], {})
