# -*- coding: utf-8 -*-
"""order-watch covers the whole corpus, and extending it never moves the
monitor's verdicts for the orders it already watched (_watch_enroll.py).

The baseline was built on 22.07 for 77 orders and silently stayed there while
the corpus grew to 297: an order added later was watched by nothing. The first
test is the lock -- every corpus document is in orders, aliases, manual or
pending. The rest pin the plan/enroll logic on synthetic data. No network.

    venv\\Scripts\\python.exe tests\\test_order_watch_coverage.py
"""
import copy
import hashlib
import sys
import tempfile
import types
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import _check_updates as W
import _watch_enroll as E

P = "/אתרי-יחידות/אתר-הפקודות/פקודות-מטכ-ל/"


def test_every_corpus_doc_is_covered():
    problems = E.coverage_problems(W.load_state(), E.load_corpus())
    assert not problems, "order-watch misses corpus docs:\n  " + "\n  ".join(problems[:20])


def test_plan_is_idempotent_on_the_committed_state():
    state = W.load_state()
    before = copy.deepcopy(state)
    log = E.plan(state, E.load_corpus(), {}, "2099-01-01")
    assert not [line for line in log if not line.startswith("דילוג")], log[:5]
    assert state == before


def test_the_state_file_round_trips_byte_for_byte():
    # --plan/--enroll rewrite the whole file; a rewrite must not reformat the
    # entries it did not touch, or the diff hides what really changed.
    raw = W.STATE_PATH.read_bytes()
    with tempfile.TemporaryDirectory() as tmp:
        old = E.STATE_PATH
        E.STATE_PATH = Path(tmp) / "order_watch.json"
        try:
            E.save_state(W.load_state())
            assert E.STATE_PATH.read_bytes() == raw
        finally:
            E.STATE_PATH = old


def test_ids_and_digits():
    assert E.id_digits("PM-33.0302") == "330302"
    assert E.id_digits("3.0110") == "30110"
    assert E.id_digits("2.0101") == "20101"
    assert E.id_digits("HKA-31-08-01") is None
    assert E.id_digits("חוק-קליטת-חיילים") is None
    # a year in the slug before the number (the real PM-33.0307 page)
    page = P + "משמעת-33/דרגות-לצורכי-חוק-השיפוט-הצבאי-תשט-ו-1955-330307/"
    assert W.order_digits(page) == "1955", "the monitor's helper reads the year"
    assert E.slug_has_digits(page, "330307") and not E.slug_has_digits(page, "330308")
    assert E.slug_has_digits(P + "x-010126/", "10126"), "leading zeros do not matter"


def test_plan_does_not_alias_a_number_collision_with_another_title():
    state = {"orders": {"013.3": {"title": "תגמול נוסף לחיילי מילואים", "page": P + "x-30110/", "pdfs": {}}}}
    docs = {"3.0110": {"title": "נוהל אחר לגמרי בנושא שונה", "source_file": "", "source_url": ""}}
    E.plan(state, docs, {}, "2026-09-26")
    assert "3.0110" in state["pending"] and state["aliases"] == {}


def _synthetic():
    state = {
        "search_path": "/search/",
        "manual": {"HKA-1": {"title": "הוראה פנימית", "reason": "לא בפורטל"}},
        "orders": {
            # a key an old parser wrote backwards (50.0405)
            "5040.05": {"title": "הריתוק המשקי בצה\"ל", "page": P + "אפסניה-50/הריתוק-המשקי-בצה-ל-500405/",
                        "pdfs": {"/media/a/500405.pdf": {"sha256": "aa", "bytes": 1}}},
        },
    }
    docs = {
        "50.0405": {"title": "הריתוק המשקי בצה\"ל", "source_file": "500405x.pdf", "source_url": ""},
        "33.0209": {"title": "נסיעה מחוץ למסגרת התפקיד", "source_file": "f.pdf", "source_url": ""},
        "PM-33.0209": {"title": "פקודת מטכ\"ל - נסיעה מחוץ למסגרת התפקיד", "source_file": "f.pdf", "source_url": ""},
        "21.0113": {"title": "טלפון אישי", "source_file": "210113.pdf",
                    "source_url": "https://www.idf.il" + P + "ביטחון-מידע-21/טלפון-210113/"},
        "58.0214": {"title": "טיפול ברכב אחרי תאונה", "source_file": "580214.pdf", "source_url": ""},
        "HKA-1": {"title": "הוראה פנימית", "source_file": "h.pdf", "source_url": ""},
        "FOI-X": {"title": "מענה חופש מידע", "source_file": "x.pdf", "source_url": ""},
    }
    index = {"33.0209": {"path": P + "משמעת-33/נסיעה-330209/", "title": "נסיעה 33.0209", "from": "idx.json"}}
    return state, docs, index


def test_plan_aliases_pends_and_skips():
    state, docs, index = _synthetic()
    orders_before = copy.deepcopy(state["orders"])
    log = E.plan(state, docs, index, "2026-09-26")
    assert state["orders"] == orders_before, "plan never touches orders"
    assert state["aliases"] == {"50.0405": "5040.05", "PM-33.0209": "33.0209"}
    pend = state["pending"]
    assert set(pend) == {"33.0209", "21.0113", "58.0214"}
    assert pend["33.0209"]["page"] == P + "משמעת-33/נסיעה-330209/"
    assert pend["33.0209"]["page_from"] == "idx.json"
    assert pend["21.0113"]["page"] == P + "ביטחון-מידע-21/טלפון-210113/"
    assert pend["21.0113"]["page_from"] == "source_url"
    assert pend["58.0214"]["page"] is None, "unknown page -> found by portal search at --enroll"
    assert any(line.startswith("דילוג FOI-X") for line in log)
    problems = E.coverage_problems(state, docs)
    assert problems == ["FOI-X: לא פקודה ממוספרת -- להוסיף ל-manual עם נימוק"], problems


def test_pick_hit_takes_the_single_number_match():
    hits = ["https://www.idf.il" + P + "תנועות-58/נוהל-טיפול-ברכב-580214/",
            P + "תנועות-58/נוהל-טיפול-ברכב-580214/",
            P + "תנועות-58/רישוי-לרכב-צבאי-580204/"]
    assert E._pick_hit("58.0214", hits) == P + "תנועות-58/נוהל-טיפול-ברכב-580214/"
    assert E._pick_hit("58.0217", hits) is None
    two = [P + "a-580214/", P + "b-580214/"]
    assert E._pick_hit("58.0214", two) is None, "ambiguous -> no guess"


def test_merge_verdicts():
    with tempfile.TemporaryDirectory() as tmp:
        pdf_dir = Path(tmp)
        (pdf_dir / "ok.pdf").write_bytes(b"%PDF ours")
        (pdf_dir / "old.pdf").write_bytes(b"%PDF older copy")
        ours = hashlib.sha256(b"%PDF ours").hexdigest()
        existing = {"title": "קיים", "page": P + "x-110000/",
                    "pdfs": {"/media/x.pdf": {"sha256": "11", "bytes": 1}}}
        state = {"orders": {"11.0000": existing}, "pending": {
            k: {"title": k, "portal_title": "", "page": P + f"{k}/"} for k in ("A", "B", "C", "D", "F", "G")}}
        state["pending"]["D"]["page"] = None
        docs = {k: {"source_file": "ok.pdf"} for k in ("A", "C", "D", "F", "G")}
        docs["B"] = {"source_file": "old.pdf"}
        fetched = {
            "A": {"page_status": 200, "errors": [], "page": P + "A/", "text_sha256": "t", "text_len": 9,
                  "pdfs": {"/media/a.pdf": {"sha256": ours, "bytes": 9},
                           "/media/a-annex.pdf": {"sha256": "zz", "bytes": 3}}},
            "B": {"page_status": 200, "errors": [], "page": P + "B/",
                  "pdfs": {"/media/b.pdf": {"sha256": "new", "bytes": 5}}},
            "C": {"page_status": 404, "errors": [], "page": P + "C/", "pdfs": {}},
            "D": {"page_status": 200, "errors": [], "page": P + "D-found/", "pdfs": {}},
            "F": {"page_status": 200, "errors": [], "page": P + "F/",
                  "pdfs": {"/media/f.pdf": {"error": "TimeoutError"}}},
            "G": {"page_status": 200, "errors": [], "page": P + "G/",
                  "pdfs": {"/media/g.pdf": {"sha256": ours, "bytes": 9}, "/media/g-annex.pdf": {"error": 404}}},
        }
        frozen = copy.deepcopy(existing)
        out = E.merge(state, copy.deepcopy(fetched), docs, "2026-09-26", pdf_dir=pdf_dir)
        assert {k: v for k, (v, _) in out.items()} == {
            "A": "enrolled", "B": "drift", "C": "unreachable", "D": "no-pdf", "F": "unreachable",
            "G": "dead-link"}
        assert all(state["pending"][k]["last_try"] == "2026-09-26" for k in "BCDFG")
        assert "last_try" not in state["orders"]["A"]
        assert state["orders"]["11.0000"] == frozen, "an existing entry is never rewritten"
        a = state["orders"]["A"]
        assert set(a["pdfs"]) == {"/media/a.pdf", "/media/a-annex.pdf"}
        assert a["enrolled"] == {"date": "2026-09-26", "local_match": True}
        assert (a["text_sha256"], a["text_len"]) == ("t", 9)
        assert "A" not in state["pending"]
        assert state["pending"]["B"]["portal_now"]["pdfs"] == {"/media/b.pdf": {"sha256": "new", "bytes": 5}}
        assert state["pending"]["D"]["page"] == P + "D-found/", "a page found by search is kept"
        assert set(state["pending"]) == {"B", "C", "D", "F", "G"}

        out = E.merge(state, {"B": fetched["B"]}, docs, "2026-09-27",
                      accept_drift=True, pdf_dir=pdf_dir)
        assert out == {"B": ("enrolled-drift", "")}
        assert state["orders"]["B"]["enrolled"] == {"date": "2026-09-27", "local_match": False}


def test_accept_drift_needs_explicit_ids():
    old = sys.argv
    sys.argv = ["_watch_enroll.py", "--enroll", "--accept-drift"]
    try:
        E.main()
        raise AssertionError("--accept-drift without --only must refuse")
    except SystemExit as e:
        assert e.code == 2
    finally:
        sys.argv = old


def test_local_copy_is_found_in_a_subfolder():
    with tempfile.TemporaryDirectory() as tmp:
        sub = Path(tmp) / "_unextractable"
        sub.mkdir()
        (sub / "360314.pdf").write_bytes(b"%PDF short")
        assert E._local_sha("360314.pdf", Path(tmp)) == hashlib.sha256(b"%PDF short").hexdigest()
        assert E._local_sha("nope.pdf", Path(tmp)) is None
        assert E._local_sha("", Path(tmp)) is None


@contextmanager
def _fake_portal(site):
    """Stand-in for playwright.sync_api: SNAPSHOT_JS answers from site["pages"],
    either search JS from site["search"], NEWS_JS with nothing. Yields the list
    of evaluate() calls as (kind, arg)."""
    calls = []

    class Page:
        def goto(self, *a, **k): pass
        def wait_for_timeout(self, ms): pass
        def content(self): return "<html>אתר הפקודות</html>"

        def evaluate(self, js, arg):
            if js is W.SNAPSHOT_JS:
                calls.append(("snapshot", sorted(arg)))
                return {oid: copy.deepcopy(site["pages"].get(m["page"], {"page_status": 404, "errors": [], "pdfs": {}}))
                        for oid, m in arg.items()}
            if js in (W.SEARCH_JS, E.ENROLL_SEARCH_JS):
                calls.append(("search", arg[1]))
                return list(site["search"].get(arg[1], []))
            calls.append(("news", arg))
            return []

    class Browser:
        def new_page(self, **k): return Page()
        def close(self): pass

    class PW:
        chromium = types.SimpleNamespace(launch=lambda **k: Browser())
        def __enter__(self): return self
        def __exit__(self, *a): return False

    fake = types.ModuleType("playwright.sync_api")
    fake.sync_playwright = lambda: PW()
    saved = {k: sys.modules.get(k) for k in ("playwright", "playwright.sync_api")}
    sys.modules["playwright"] = types.ModuleType("playwright")
    sys.modules["playwright.sync_api"] = fake
    try:
        yield calls
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


def test_fetch_finds_unknown_and_moved_pages_by_search():
    """fetch() against a fake portal: the orchestration --enroll runs live."""
    ok = {"page_status": 200, "errors": [], "pdfs": {"/media/p.pdf": {"sha256": "s", "bytes": 1}}}
    site = {
        "pages": {P + "a-500204/": ok, P + "b-new-580214/": ok, P + "c-new-300401/": ok,
                  P + "תנועות-58/חוק-תשט-ו-1955-330307/": ok},
        # the page chrome's own links come first -- they must not crowd the hit out
        "search": {"580214": [P + "x-1/", P + "x-2/", P + "x-3/", P + "x-4/", P + "x-5/",
                              P + "b-new-580214/", P + "other-580217/"],
                   "300401": [P + "c-new-300401/"], "500303": [],
                   "330307": [P + "תנועות-58/חוק-תשט-ו-1955-330307/"]},
    }
    state = {"search_path": "/s/", "pending": {
        "50.0204": {"page": P + "a-500204/"},     # known and live
        "58.0214": {"page": None},                # unknown -> search
        "30.0401": {"page": P + "c-old-300401/"},  # moved -> 404 -> search -> retry
        "50.0303": {"page": None},                # unknown, search finds nothing
        "PM-33.0307": {"page": None},             # a year in the slug before the number
    }}
    with _fake_portal(site):
        out = E.fetch(state, list(state["pending"]))
    assert out["50.0204"]["page_status"] == 200 and out["50.0204"]["page"] == P + "a-500204/"
    assert out["58.0214"]["page_status"] == 200 and out["58.0214"]["page"] == P + "b-new-580214/"
    assert out["30.0401"]["page_status"] == 200 and out["30.0401"]["page"] == P + "c-new-300401/"
    assert out["PM-33.0307"]["page"] == P + "תנועות-58/חוק-תשט-ו-1955-330307/"
    assert out["50.0303"]["errors"] and out["50.0303"]["page"] is None
    assert "searched" in out["58.0214"] and "searched" not in out["50.0204"]


def test_the_monitor_fetches_only_orders():
    """snapshot() -- the monitor itself -- never touches pending or aliases."""
    ok = {"page_status": 200, "errors": [], "pdfs": {}}
    state = {"orders": {"1.0101": {"title": "t", "page": P + "a-10101/", "pdfs": {}}},
             "pending": {"2.0202": {"title": "u", "page": P + "b-20202/"}},
             "aliases": {"2.0202x": "2.0202"},
             "news_page": "/news/", "search_path": "/s/"}
    with _fake_portal({"pages": {P + "a-10101/": ok, P + "b-20202/": ok}, "search": {}}) as calls:
        current, _ = W.snapshot(state)
    assert set(current) == {"1.0101"}
    assert [c for c in calls if c[0] == "snapshot"] == [("snapshot", ["1.0101"])]


def test_renumber_hint_on_the_news_page():
    ours = [("PM-35.0402", {"title": "חופשות לחיילים המשרתים בשירות חובה"}),
            ("PM-33.0302", {"title": "דין משמעתי"})]
    hint = W.renumber_hint('פ"מ 04.101 – חופשות לחיילים המשרתים בשירות חובה', ours)
    assert "PM-35.0402" in hint and "מספור חדש" in hint
    assert "PM-35.0402" in W.renumber_hint("פ״מ 04.101 - חופשות לחיילים בשירות חובה", ours)
    assert "PM-33.0302" in W.renumber_hint('פ"מ 07.102 - דין משמעתי', ours)
    assert W.renumber_hint('33.0148 - מניעת ניגוד עניינים בצה"ל', ours) == ""
    assert W.renumber_hint('09.0101 - ארגון הצבא ויחידותיו, תקינה ושיאי כ"א', ours) == ""


def test_news_markers_in_the_report():
    state = {"orders": {"PM-35.0402": {"title": "חופשות לחיילים המשרתים בשירות חובה",
                                       "page": P + "תנאי-שירות-35/350402-חופשות/", "pdfs": {}}},
             "pending": {"33.0148": {"title": "מניעת ניגוד עניינים", "page": P + "משמעת-33/330148-ניגוד/"},
                         "PM-33.0307": {"title": "דרגות לצורכי חוק השיפוט",
                                        "page": P + "משמעת-33/דרגות-תשט-ו-1955-330307/"}}}
    news = [{"h": P + "תנאי-שירות-35/350402-חופשות/", "t": "35.0402 – חופשות לחיילים המשרתים בשירות חובה"},
            {"h": P + "משמעת-33/330148-ניגוד/", "t": "33.0148 - מניעת ניגוד עניינים בצה\"ל"},
            {"h": P + "פקודות-חדשות/פ-מ-04101-חופשות/", "t": "פ\"מ 04.101 – חופשות לחיילים המשרתים בשירות חובה"},
            {"h": P + "ארגון-09/090101-ארגון/", "t": "09.0101 - ארגון הצבא ויחידותיו"},
            {"h": P + "כללי-01/הנחיות-1955-ועוד/", "t": "הנחיות שונות"}]
    lines = W.render_report(state, [], news).splitlines()
    marked = [line for line in lines if line.startswith("- [")]
    assert "בקורפוס שלנו" in marked[0]
    assert "בקורפוס שלנו" in marked[1], "a pending order is ours too"
    assert "PM-35.0402" in marked[2] and "מספור חדש" in marked[2]
    assert "←" not in marked[3]
    assert "←" not in marked[4], "a year in a pending slug is not one of our numbers"


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
    raise SystemExit(1 if fails else 0)
