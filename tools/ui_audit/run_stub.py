"""Boot app.py with a stubbed backend — the corpus/ONNX stack is not needed to
look at the entry gate, and stubbing it is the repo's own test pattern."""
import sys, types
from pathlib import Path

ROOT = Path("/home/user/command-ai")
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
b.stream_ai_answer = lambda *a, **k: iter(())
b._compose_user_content = lambda *a, **k: ""
b.lacked_from = lambda *a, **k: []
b.render_clause_image = lambda *a, **k: None
b.start_keep_warm = lambda *a, **k: None
sys.modules["backend"] = b

from streamlit.web import bootstrap
from streamlit import config
config.set_option("server.headless", True)
config.set_option("server.port", 8501)
config.set_option("server.fileWatcherType", "none")
config.set_option("browser.gatherUsageStats", False)
bootstrap.run(str(ROOT / "app.py"), False, [], {})
