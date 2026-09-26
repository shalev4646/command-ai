# -*- coding: utf-8 -*-
"""Bring every portal order in the corpus under order-watch (_check_updates.py).

storage/order_watch.json["orders"] was built on 21-22.07 for the orders of the
day and never grew with the corpus: on 26.09 the corpus held 297 documents and
the monitor watched 77 of them. This script closes that gap and keeps it
closed. It never modifies an entry that is already in "orders" -- the monitor's
verdicts for those stay exactly what they were.

The state file gains two sections next to "orders" and "manual":
  aliases  corpus id -> the orders/pending key that already covers the same
           portal page (baseline keys from an old parser that read the header
           digits backwards, e.g. 5040.05 = 50.0405; duplicate corpus docs of
           one order). _check_updates.py does not read it.
  pending  corpus orders with a known -- or searchable -- portal page that were
           not fingerprinted yet. _check_updates.py does not read it either.

Usage:
    python _watch_enroll.py              # offline coverage report
    python _watch_enroll.py --plan --index FILE [--index FILE ...]
                                         # offline: every uncovered numbered
                                         # order -> pending (or aliases)
    python _watch_enroll.py --enroll [--only ID ...] [--limit N]
                                         # Playwright: fingerprint pending pages
                                         # with the monitor's own JS and move
                                         # them into "orders"
    python _watch_enroll.py --enroll --accept-drift --only ID [ID ...]

Exit codes: 0 = fine, 4 = a corpus doc is covered by nothing (read the
report), 3 = portal unreachable (nothing written); anything else is a crash.

Why --enroll does not simply record today's portal state: the baseline means
"the portal as it was when our copy was taken". An order ingested in August may
have been updated on the portal since, and writing today's hashes would hide
that update for good. So by default an order is enrolled only when one of its
page's PDFs is byte-identical to our pdf-ldf_law/<source_file>. Any other order
stays in pending with the portal's current fingerprint ("portal_now"), and the
report lists it for a decision: compare / re-ingest, then
`--enroll --accept-drift --only <id>` -- one decision per id, which is why
--accept-drift refuses to run without --only.

--index takes site-walk dumps: JSON lists of {num, path[, title]}
(night/out/index_entries.json, night/out/downloadable.json -- untracked, in the
main checkout) or "num|path" lines (night/out/paths.txt). An order found in
none of them is planned with page=null, and --enroll finds its page through the
portal's own search page by order number.
"""
import argparse
import datetime
import glob
import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import safe_print
from _check_updates import (BASE, HOME, SNAPSHOT_JS, STATE_PATH, load_state,
                            order_digits)

ROOT = Path(__file__).parent
JSON_STORE = ROOT / "storage" / "json_store"
PDF_DIR = ROOT / "pdf-ldf_law"
ENROLL_REPORT = ROOT / "storage" / "order_watch_enroll_report.md"
PORTAL_PREFIX = "/אתרי-יחידות/אתר-הפקודות/"

# A portal order id as the corpus writes it: 33.0302, 3.0110 (הפ"ע), PM-33.0302.
# Everything else (HKA-*, FOI-*, CHOK-*, Hebrew slugs) is not on the orders
# portal and belongs in "manual" with a reason a human wrote.
_ORDER_ID = re.compile(r"^(?:PM-)?(\d{1,2})\.(\d{2,4})$")

# Below this token overlap two titles are not "the same order" -- guards the
# digits-only alias match against a numbering collision.
_ALIAS_MIN_TITLE_OVERLAP = 0.3

# The portal search, for pages the site walk never saw. Unlike the monitor's
# SEARCH_JS it keeps every order-page link -- the caller picks the one whose
# slug carries the number, and the page chrome's own links must not crowd the
# real hits out of a top-5. Untested live: if the results page renders by JS,
# this returns nothing and the order simply stays pending.
ENROLL_SEARCH_JS = r"""
async (args) => {
  const [searchPath, q] = args;
  try {
    const r = await fetch(encodeURI(searchPath) + '?q=' + encodeURIComponent(q),
                          { signal: AbortSignal.timeout(45000) });
    if (!r.ok) return [];
    const doc = new DOMParser().parseFromString(await r.text(), 'text/html');
    return [...new Set([...doc.querySelectorAll('a[href]')]
      .map(a => { try { return decodeURIComponent(a.getAttribute('href')); } catch (e) { return a.getAttribute('href'); } })
      .filter(h => h && /\/(פקודות-מטכ-ל|הוראות-הפיקוד-העליון)\//.test(h)))];
  } catch (e) { return []; }
}
"""


def id_digits(doc_id: str) -> str | None:
    """'PM-33.0302' -> '330302', '3.0110' -> '30110': the digit run of the portal slug."""
    m = _ORDER_ID.match(doc_id)
    return m.group(1) + m.group(2) if m else None


def _same_digits(a: str | None, b: str | None) -> bool:
    return bool(a and b) and a.lstrip("0") == b.lstrip("0")


def slug_has_digits(page: str, digits: str | None) -> bool:
    """Does the page slug carry this order number anywhere? Unlike order_digits
    (first 4-7 digit run) this survives a year in the title, e.g.
    '...-תשט-ו-1955-330307/' is 33.0307, not '1955'."""
    last = [s for s in (page or "").split("/") if s][-1:] or [""]
    return any(_same_digits(run, digits) for run in re.findall(r"\d{4,7}", last[0]))


def _tokens(title: str) -> set[str]:
    return set(re.sub(r"[^֐-׿A-Za-z0-9]+", " ", title or "").split())


def title_overlap(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    return len(ta & tb) / max(1, len(ta | tb))


def load_corpus(store: Path = JSON_STORE) -> dict[str, dict]:
    docs = {}
    for f in sorted(store.glob("*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        docs[d["document_id"]] = {"title": d.get("title", ""),
                                  "source_file": d.get("source_file") or "",
                                  "source_url": d.get("source_url") or ""}
    return docs


def save_state(state: dict) -> None:
    # LF and no trailing newline: byte-identical to what --accept has always
    # written, so a rewrite shows only real changes in the diff.
    with open(STATE_PATH, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(state, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------- coverage

def coverage(state: dict, docs: dict) -> dict[str, list[str]]:
    """Which section covers each corpus doc; 'uncovered' must stay empty."""
    out = {"orders": [], "aliases": [], "manual": [], "pending": [], "uncovered": []}
    for doc_id in sorted(docs):
        for section in ("orders", "aliases", "manual", "pending"):
            if doc_id in state.get(section, {}):
                out[section].append(doc_id)
                break
        else:
            out["uncovered"].append(doc_id)
    return out


def coverage_problems(state: dict, docs: dict) -> list[str]:
    """Everything that would let a corpus order drift out of the monitor's sight."""
    problems = []
    orders, pending = state.get("orders", {}), state.get("pending", {})
    for doc_id in coverage(state, docs)["uncovered"]:
        if id_digits(doc_id):
            problems.append(f"{doc_id}: לא במעקב -- להריץ python _watch_enroll.py --plan")
        else:
            problems.append(f"{doc_id}: לא פקודה ממוספרת -- להוסיף ל-manual עם נימוק")
    for alias, target in state.get("aliases", {}).items():
        if target not in orders and target not in pending:
            problems.append(f"aliases[{alias}] -> {target}: היעד לא ב-orders ולא ב-pending")
    for section in ("orders", "manual"):
        for doc_id in set(pending) & set(state.get(section, {})):
            problems.append(f"{doc_id}: גם ב-pending וגם ב-{section}")
    return problems


def render_coverage(state: dict, docs: dict) -> str:
    cov = coverage(state, docs)
    pending = state.get("pending", {})
    no_page = [k for k in cov["pending"] if not pending[k].get("page")]
    drift = [k for k in cov["pending"] if pending[k].get("portal_now")]
    lines = [
        f"מסמכים בקורפוס: {len(docs)}",
        f"  במעקב אוטומטי (orders):        {len(cov['orders'])}",
        f"  במעקב דרך כינוי (aliases):      {len(cov['aliases'])}",
        f"  במעקב ידני (manual):            {len(cov['manual'])}",
        f"  ממתינים לרישום (pending):       {len(cov['pending'])}"
        f"  (בלי עמוד ידוע: {len(no_page)}, בסטייה מהעותק שנקלט: {len(drift)})",
        f"  לא מכוסים:                      {len(cov['uncovered'])}",
    ]
    for p in coverage_problems(state, docs):
        lines.append(f"  ✗ {p}")
    return "\n".join(lines)


# -------------------------------------------------------------------- plan

def load_index(paths: list[str]) -> dict[str, dict]:
    """num -> {path, title, from}; the first file that knows a number wins."""
    idx = {}
    for p in paths:
        text = Path(p).read_text(encoding="utf-8")
        try:
            rows = json.loads(text)
        except ValueError:
            rows = [dict(zip(("num", "path"), (s.strip() for s in line.split("|", 1))))
                    for line in text.splitlines() if "|" in line]
        for r in rows:
            if r.get("num") and r.get("path"):
                idx.setdefault(r["num"], {"path": r["path"], "title": r.get("title", ""),
                                          "from": Path(p).name})
    return idx


def _portal_page(doc: dict) -> str | None:
    url = doc.get("source_url") or ""
    if url.startswith(BASE + PORTAL_PREFIX):
        return url[len(BASE):]
    return None


def plan(state: dict, docs: dict, index: dict, today: str) -> list[str]:
    """Put every uncovered numbered order into pending, or into aliases when the
    same portal order is already covered. Never touches orders or manual.
    Returns log lines (one per decision) for the human reading the diff."""
    orders = state["orders"]
    aliases = state.setdefault("aliases", {})
    pending = state.setdefault("pending", {})
    log = []
    for doc_id in coverage(state, docs)["uncovered"]:
        doc, digits = docs[doc_id], id_digits(doc_id)
        if not digits:
            log.append(f"דילוג {doc_id}: לא פקודה ממוספרת -- צריך רשומת manual עם נימוק")
            continue
        entry = index.get(doc_id.removeprefix("PM-"))
        page = _portal_page(doc) or (entry or {}).get("path")
        # Already covered under another key: the same page, or the same order
        # number with a matching title (keys an old parser wrote reversed).
        target = None
        for key, o in list(orders.items()) + list(pending.items()):
            if page and o.get("page") == page:
                target = key
                break
            same_number = (slug_has_digits(o["page"], digits) if o.get("page")
                           else _same_digits(digits, id_digits(key)))
            if same_number and title_overlap(doc["title"], o.get("title", "")) >= _ALIAS_MIN_TITLE_OVERLAP:
                target = key
                break
        if target:
            aliases[doc_id] = target
            log.append(f"כינוי {doc_id} -> {target} ({doc['title'][:50]})")
            continue
        pending[doc_id] = {
            "title": doc["title"],
            "portal_title": (entry or {}).get("title", ""),
            "page": page,
            "page_from": ("source_url" if _portal_page(doc)
                          else (entry or {}).get("from", "חיפוש בפורטל בזמן --enroll")),
            "planned": today,
        }
        log.append(f"ממתין {doc_id}: {'עמוד מ-' + pending[doc_id]['page_from'] if page else 'בלי עמוד ידוע'}")
    return log


# ------------------------------------------------------------------ enroll

def _pick_hit(doc_id: str, hits: list[str]) -> str | None:
    """The single search hit whose slug carries this order's number, else None."""
    digits = id_digits(doc_id)
    found = {h[len(BASE):] if h.startswith(BASE) else h
             for h in hits if slug_has_digits(h, digits)}
    return found.pop() if len(found) == 1 else None


def fetch(state: dict, ids: list[str]) -> dict[str, dict]:
    """Fingerprint pending pages through the WAF with the monitor's own JS.

    Same handshake as _check_updates.snapshot: pass the Incapsula challenge once
    on the section home page, then fetch + hash everything inside the page.
    Pages without a known path, and pages that 404 (moved since the site walk),
    are looked up once through the portal search by order number.
    """
    from playwright.sync_api import sync_playwright

    pending, search_path = state["pending"], state.get("search_path", "")
    where = {oid: pending[oid].get("page") for oid in ids}
    searched = {}
    out = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
            locale="he-IL",
        )
        page.goto(BASE + HOME, wait_until="domcontentloaded", timeout=90_000)
        page.wait_for_timeout(4_000)
        html = page.content()
        if "_Incapsula_Resource" in html and "אתר הפקודות" not in html:
            browser.close()
            raise ConnectionError("WAF challenge not passed (Incapsula)")

        def search(oid):
            hits = page.evaluate(ENROLL_SEARCH_JS, [search_path, id_digits(oid)])
            searched[oid] = hits
            return _pick_hit(oid, hits)

        def snap(oids):
            oids = [o for o in oids if where.get(o)]
            for i in range(0, len(oids), 10):
                batch = {o: {"page": where[o]} for o in oids[i:i + 10]}
                out.update(page.evaluate(SNAPSHOT_JS, batch))
                safe_print(f"... {min(i + 10, len(oids))}/{len(oids)} נבדקו")

        for oid in ids:
            if not where[oid]:
                where[oid] = search(oid)
        snap(ids)
        moved = []
        for oid in ids:
            if (out.get(oid) or {}).get("page_status") == 404:
                new = search(oid)
                if new and new != where[oid]:
                    where[oid] = new
                    moved.append(oid)
        snap(moved)
        browser.close()

    for oid in ids:
        cur = out.setdefault(oid, {"pdfs": {}, "errors": [
            "לא נמצא עמוד: המספר לא באינדקס-האתר והחיפוש בפורטל לא החזיר עמוד יחיד"]})
        cur["page"] = where[oid]
        if oid in searched:
            cur["searched"] = searched[oid]
    return out


def _local_sha(source_file: str, pdf_dir: Path = PDF_DIR) -> str | None:
    if not source_file:
        return None
    f = pdf_dir / source_file
    if not f.is_file():
        # a short order ingested from OCR text may still sit in a subfolder
        # (pdf-ldf_law/_unextractable/)
        f = next((x for x in pdf_dir.rglob(glob.escape(Path(source_file).name)) if x.is_file()), None)
    return hashlib.sha256(f.read_bytes()).hexdigest() if f else None


def merge(state: dict, fetched: dict, docs: dict, today: str,
          accept_drift: bool = False, pdf_dir: Path = PDF_DIR) -> dict[str, tuple[str, str]]:
    """Move fetched pending entries into orders; {id: (verdict, detail)}.

    enrolled        a page PDF is byte-identical to our copy -> watched from now
    enrolled-drift  it is not, and --accept-drift said to take today's state
    drift           it is not -> stays pending with portal_now, for a decision
    no-pdf          the page links no /media/ PDF -> stays pending
    dead-link       the page links a PDF that is 404/410 -> stays pending: the
                    monitor would report that link as "new PDF" on every run
    unreachable     page or PDF fetch failed -> stays pending, retry later
    Every id that stays pending gets last_try, so --limit rotates through them.
    """
    orders, pending = state["orders"], state["pending"]
    outcome = {}
    for oid, cur in fetched.items():
        p = pending.get(oid)
        if p is None or oid in orders:
            continue
        if cur.get("errors") or cur.get("page_status") != 200:
            why = "; ".join(cur.get("errors") or [f"העמוד החזיר {cur.get('page_status')}"])
            outcome[oid] = ("unreachable", why)
            continue
        pdfs = cur.get("pdfs", {})
        broken = [path for path, v in pdfs.items() if "sha256" not in v]
        dead = [path for path in broken if pdfs[path].get("error") in (404, 410)]
        if broken and dead == broken:
            outcome[oid] = ("dead-link", f"קישור PDF מת ({pdfs[dead[0]]['error']}): {dead[0]}")
            continue
        if broken:
            outcome[oid] = ("unreachable", f"הורדת PDF נכשלה: {broken[0]}")
            continue
        p["page"] = cur.get("page") or p.get("page")
        if not pdfs:
            outcome[oid] = ("no-pdf", "בעמוד אין קובץ PDF")
            continue
        local = _local_sha(docs.get(oid, {}).get("source_file", ""), pdf_dir)
        match = local is not None and local in {v["sha256"] for v in pdfs.values()}
        if not match and not accept_drift:
            p["portal_now"] = {"checked": today, "pdfs": pdfs}
            outcome[oid] = ("drift", "אין עותק מקומי להשוואה" if local is None
                            else "אף PDF בעמוד אינו זהה לעותק שנקלט")
            continue
        orders[oid] = {
            "title": p.get("title", ""),
            "portal_title": p.get("portal_title", ""),
            "page": p["page"],
            "pdfs": pdfs,
            "text_sha256": cur.get("text_sha256"),
            "text_len": cur.get("text_len"),
            "enrolled": {"date": today, "local_match": match},
        }
        del pending[oid]
        outcome[oid] = ("enrolled" if match else "enrolled-drift", "")
    for oid in outcome:
        if oid in pending:
            pending[oid]["last_try"] = today
    return outcome


_VERDICT_HEADINGS = (
    ("enrolled", "✅ נרשמו למעקב (ה-PDF בפורטל זהה לעותק שנקלט)"),
    ("enrolled-drift", "☑️ נרשמו למעקב בהכרעה (--accept-drift), למרות שהפורטל שונה מהעותק"),
    ("drift", "⚠️ נשארו בהמתנה: ה-PDF בפורטל שונה מהעותק שנקלט. להשוות או לקלוט מחדש, "
              "ואז `--enroll --accept-drift --only <id>`"),
    ("no-pdf", "📄 נשארו בהמתנה: בעמוד אין קובץ PDF"),
    ("dead-link", "🔗 נשארו בהמתנה: בעמוד קישור PDF מת. רישום היה מדווח עליו כ„PDF חדש” בכל ריצה, "
                  "ולכן לבדוק ידנית"),
    ("unreachable", "🔁 נשארו בהמתנה: לא נגישים בריצה הזו. להריץ שוב"),
)


def render_enroll(state: dict, outcome: dict) -> str:
    today = datetime.date.today().isoformat()
    lines = [f"# רישום פקודות למעקב — {today}", ""]
    for verdict, heading in _VERDICT_HEADINGS:
        ids = sorted(k for k, (v, _) in outcome.items() if v == verdict)
        if not ids:
            continue
        lines += [f"## {heading} — {len(ids)}", ""]
        for oid in ids:
            entry = state["orders"].get(oid) or state["pending"].get(oid) or {}
            detail = outcome[oid][1]
            link = f" — [עמוד]({BASE}{entry['page']})" if entry.get("page") else ""
            lines.append(f"- {oid} — {entry.get('title', '')}{link}" + (f": {detail}" if detail else ""))
        lines.append("")
    lines.append(f"_במעקב אוטומטי: {len(state['orders'])} · ממתינים: {len(state['pending'])}._")
    return "\n".join(lines)


# -------------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser()
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--plan", action="store_true", help="offline: uncovered orders -> pending")
    mode.add_argument("--enroll", action="store_true", help="Playwright: pending -> orders")
    ap.add_argument("--index", action="append", default=[], help="site-walk dump (repeatable)")
    ap.add_argument("--only", nargs="+", default=None, help="enroll just these pending ids")
    ap.add_argument("--limit", type=int, default=None, help="enroll at most N pending ids")
    ap.add_argument("--accept-drift", action="store_true",
                    help="enroll even when no page PDF matches our copy (needs --only)")
    args = ap.parse_args()
    if args.accept_drift and not args.only:
        # a drift is a possibly-stale corpus order; baselining all of them in
        # one go would hide exactly the updates this tool exists to surface
        ap.error("--accept-drift דורש --only <id ...>: הכרעה לכל פקודה בנפרד")

    state, docs = load_state(), load_corpus()
    today = datetime.date.today().isoformat()

    if args.plan:
        for line in plan(state, docs, load_index(args.index), today):
            safe_print(line)
        save_state(state)
        safe_print("")
    elif args.enroll:
        pending = state.get("pending", {})
        for unknown in sorted(set(args.only or []) - set(pending)):
            safe_print(f"דילוג {unknown}: לא ב-pending")
        ids = [k for k in pending if args.only is None or k in args.only]
        ids.sort(key=lambda k: pending[k].get("last_try") or "")  # untried first
        ids = ids[:args.limit] if args.limit else ids
        if not ids:
            safe_print("אין ממתינים לרישום.")
            return 0
        try:
            fetched = fetch(state, ids)
        except Exception as e:  # WAF / network: nothing was written
            safe_print(f"שגיאה: לא הצלחתי להגיע לפורטל ({e})")
            return 3
        outcome = merge(state, fetched, docs, today, accept_drift=args.accept_drift)
        save_state(state)
        report = render_enroll(state, outcome)
        ENROLL_REPORT.write_text(report, encoding="utf-8")
        safe_print(report)
        safe_print(f"\nהבייסליין עודכן ({STATE_PATH.name}) — לעבור על ה-diff ולקמט.\n")

    safe_print(render_coverage(state, docs))
    return 4 if coverage_problems(state, docs) else 0


if __name__ == "__main__":
    sys.exit(main())
