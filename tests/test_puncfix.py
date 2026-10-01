# -*- coding: utf-8 -*-
"""night/puncfix.py — the digit repair by glyph id (night/PUNCFIX_CRITERION.md). The manager's condition: a healthy
order comes out BYTE-IDENTICAL. Offline; needs the order PDFs on disk (D:/app_soldier/pdf-*), skipped without them.

    venv\\Scripts\\python.exe tests\\test_puncfix.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from night import puncfix as P  # noqa: E402

STORE = ROOT / "storage" / "json_store"
# standard-order fonts, digits drawn and none of them read differently by id (the survey, 30.09): the largest five
HEALTHY_PDF = ["PM-33.0302", "HKA-31-08-01", "33.0304", "PM-35.0402", "35.0210"]
# raw_text from the IDF site's HTML: the tool never touches them
HEALTHY_WEB = ["30.0106", "31.0109"]


def _doc(did):
    for p in STORE.glob("*.json"):
        d = json.loads(p.read_text(encoding="utf-8"))
        if d.get("document_id") == did:
            return d
    return None


def test_standard_glyph_order():
    assert P.STD[17] == "." and [P.STD[19 + k] for k in range(10)] == [str(k) for k in range(10)]
    assert P.ANCHORS == {15: ",", 16: "-", 29: ":"}


def test_healthy_pdf_orders_are_byte_identical():
    ran = 0
    for did in HEALTHY_PDF:
        d = _doc(did)
        pdf = P.find_pdf(d) if d else None
        if pdf is None:
            continue
        r = P.repair(d, pdf)
        assert r["differ"] == 0 and r["applied"] == 0, (did, r["differ"], r["applied"])
        assert r["new_raw"] == d["raw_text"], f"{did}: output differs from raw_text"
        ran += 1
    if ran == 0:
        print("  (skipped: no PDFs on disk)")


def test_web_orders_are_never_touched():
    ids = {d["document_id"] for d in P.pdf_orders(STORE)}
    for did in HEALTHY_WEB:
        assert _doc(did) is not None, did
        assert did not in ids, f"{did} is web-sourced and must not be repaired"


def test_a_scrambled_order_is_repaired_to_its_glyphs():
    """33.0220: the page says 33.0220 — the text layer reads it through a scrambled map; the repair gives it back."""
    d = _doc("33.0220")
    pdf = P.find_pdf(d) if d else None
    if pdf is None:
        print("  (skipped: no PDF on disk)")
        return
    import fitz
    nums = [n["glyph"] for n in P.numbers(P.digit_chars(fitz.open(pdf)))]
    assert "33.0220" in nums, nums[:12]


def test_a_font_outside_the_families_read_by_eye_is_never_repaired():
    """HKA-32-03-10: an ArialMT that passes the anchor test but numbers its digits two places lower. Its raw_text
    is right ("1) 2) 3)" on page 8); the repair must leave it byte-identical."""
    d = _doc("HKA-32-03-10")
    pdf = P.find_pdf(d) if d else None
    if pdf is None:
        print("  (skipped: no PDF on disk)")
        return
    r = P.repair(d, pdf)
    assert r["applied"] == 0 and r["new_raw"] == d["raw_text"], (r["applied"], r["skipped"])


def test_the_write_changes_raw_text_and_nothing_else():
    """apply --write: the order file keeps every byte but its raw_text value (json_store has files written with
    indent 1 and 2, and without a newline at the end — re-dumping would rewrite them all)."""
    for indent, tail in ((1, ""), (2, ""), (2, "\n")):
        doc = {"document_id": "x", "title": "כותרת \"מרכאות\"", "raw_text": "בלמ\"ס\n 32..2.7 \\ סוף", "n": [1, 2]}
        text = json.dumps(doc, ensure_ascii=False, indent=indent) + tail
        new = P.with_raw(text, doc["raw_text"], "בלמ\"ס\n 32.0207 \\ סוף")
        assert json.loads(new) == {**doc, "raw_text": "בלמ\"ס\n 32.0207 \\ סוף"}
        changed = [(a, b) for a, b in zip(text.split("\n"), new.split("\n")) if a != b]
        assert len(changed) == 1 and changed[0][0].lstrip().startswith('"raw_text"'), changed
        assert new.endswith(tail or "}") and len(text.split("\n")) == len(new.split("\n"))
    try:
        P.with_raw('{"title": "x"}', "a", "b")
        raise AssertionError("a file without that raw_text must be refused")
    except ValueError:
        pass


def test_the_write_refuses_another_fingerprint():
    assert P.fingerprint([]) == P.fingerprint([])
    a = [{"doc_id": "1", "new_raw": "x"}, {"doc_id": "2", "new_raw": "y"}]
    assert P.fingerprint(a) == P.fingerprint(list(reversed(a)))
    assert P.fingerprint(a) != P.fingerprint([{"doc_id": "1", "new_raw": "x"}, {"doc_id": "2", "new_raw": "z"}])


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as e:
                fails += 1
                print(f"FAIL {name}: {e}")
    sys.exit(1 if fails else 0)
