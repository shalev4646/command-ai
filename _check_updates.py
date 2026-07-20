# -*- coding: utf-8 -*-
"""Watch אתר הפקודות (idf.il) for updates to the orders in our corpus.

storage/order_watch.json is the committed baseline: for every corpus document
the portal page path, its PDF media paths and their SHA-256, plus a text hash
for the two documents that were ingested from HTML rather than PDF. This
script re-fetches everything and reports what drifted.

The portal sits behind an Incapsula WAF, so plain HTTP clients get a JS
challenge instead of content. We therefore drive a real headless Chromium
(Playwright), let it pass the challenge once on the section home page, and
then do all fetching + SHA-256 hashing INSIDE the page (crypto.subtle) so PDF
bytes never cross the browser boundary.

Usage:
    python _check_updates.py            # check, print Hebrew report, write
                                        # storage/order_watch_report.md
    python _check_updates.py --accept   # after the corpus was re-ingested:
                                        # re-check and rebase the baseline
    python _check_updates.py --ci       # same as bare run, plus writes
                                        # changed=true/false to $GITHUB_OUTPUT

Exit codes: 0 = ran fine (changed or not — read the report/output),
3 = could not reach the portal (WAF block / network).

Needs: pip install playwright && playwright install chromium
"""
import argparse
import datetime
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import safe_print

ROOT = Path(__file__).parent
STATE_PATH = ROOT / "storage" / "order_watch.json"
REPORT_PATH = ROOT / "storage" / "order_watch_report.md"

BASE = "https://www.idf.il"
HOME = "/אתרי-יחידות/אתר-הפקודות/"

# Runs inside the portal page. Receives {id: {page, pdfs: [...], text: bool}},
# returns the current snapshot in the same shape as baseline entries.
SNAPSHOT_JS = r"""
async (orders) => {
  const sha = async buf => [...new Uint8Array(await crypto.subtle.digest('SHA-256', buf))]
    .map(b => b.toString(16).padStart(2, '0')).join('');
  // Every fetch MUST be time-bounded: a single stalled connection with no
  // timeout wedges the whole evaluate() forever (learned the hard way).
  const tf = (u, ms) => fetch(encodeURI(u), { signal: AbortSignal.timeout(ms) });
  const out = {};
  const work = Object.entries(orders);
  const one = async (id, m) => {
    const entry = { pdfs: {}, errors: [] };
    try {
      const r = await tf(m.page, 45000);
      if (!r.ok) {
        entry.page_status = r.status;
        return entry;
      }
      entry.page_status = 200;
      const doc = new DOMParser().parseFromString(await r.text(), 'text/html');
      // Only same-origin /media/ files: order pages also link to the army's
      // INTERNAL portal (portal.army.idf) which is unreachable from the
      // internet and would read as a permanent error.
      const pdfs = [...new Set([...doc.querySelectorAll('a[href]')]
        .map(a => a.getAttribute('href'))
        .filter(h => h && /^\/media\//.test(h) && /\.pdf/i.test(h))
        .map(h => { try { return decodeURIComponent(h); } catch (e) { return h; } }))].sort();
      const cont = doc.querySelector('.content-page-container') || doc.body;
      const textNorm = (cont.textContent || '').replace(/\s+/g, ' ').trim();
      entry.text_sha256 = await sha(new TextEncoder().encode(textNorm));
      entry.text_len = textNorm.length;
      for (const p of pdfs) {
        // Two attempts: the largest order PDFs are ~8MB and a slow network
        // can blow the first window.
        for (let attempt = 0; attempt < 2; attempt++) {
          try {
            const pr = await tf(p, 180000);
            if (!pr.ok) { entry.pdfs[p] = { error: pr.status }; break; }
            const buf = await pr.arrayBuffer();
            entry.pdfs[p] = { sha256: await sha(buf), bytes: buf.byteLength };
            break;
          } catch (e) { entry.pdfs[p] = { error: String(e).slice(0, 80) }; }
        }
      }
    } catch (e) { entry.errors.push(String(e).slice(0, 120)); }
    return entry;
  };
  const worker = async () => {
    while (work.length) {
      const [id, m] = work.shift();
      out[id] = await one(id, m);
    }
  };
  await Promise.all([worker(), worker()]);
  return out;
}
"""

# Bonus signal: which orders the portal itself currently flags as new/updated.
NEWS_JS = r"""
async (newsPath) => {
  try {
    const r = await fetch(encodeURI(newsPath), { signal: AbortSignal.timeout(45000) });
    if (!r.ok) return [];
    const doc = new DOMParser().parseFromString(await r.text(), 'text/html');
    return [...doc.querySelectorAll('a[href]')]
      .map(a => ({ h: (() => { try { return decodeURIComponent(a.getAttribute('href')); } catch (e) { return a.getAttribute('href'); } })(),
                   t: (a.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 100) }))
      .filter(x => x.h && /\/(פקודות-מטכ-ל|הוראות-הפיקוד-העליון)\//.test(x.h) && x.h.split('/').filter(Boolean).length > 3);
  } catch (e) { return []; }
}
"""

SEARCH_JS = r"""
async (args) => {
  const [searchPath, q] = args;
  try {
    const r = await fetch(encodeURI(searchPath) + '?q=' + encodeURIComponent(q),
                          { signal: AbortSignal.timeout(45000) });
    if (!r.ok) return [];
    const doc = new DOMParser().parseFromString(await r.text(), 'text/html');
    return [...doc.querySelectorAll('a[href]')]
      .map(a => { try { return decodeURIComponent(a.getAttribute('href')); } catch (e) { return a.getAttribute('href'); } })
      .filter(h => h && /\/(פקודות-מטכ-ל|הוראות-הפיקוד-העליון)\//.test(h))
      .slice(0, 5);
  } catch (e) { return []; }
}
"""


def load_state() -> dict:
    return json.loads(STATE_PATH.read_text(encoding="utf-8"))


def order_digits(page_path: str) -> str | None:
    """The order-number digit run from the page slug (e.g. .../350402-חופשות.../)."""
    last = [s for s in page_path.split("/") if s][-1]
    m = re.search(r"(\d{4,7})", last)
    return m.group(1) if m else None


def snapshot(state: dict) -> tuple[dict, list]:
    """Drive Chromium through the WAF and return (current snapshot, news list)."""
    from playwright.sync_api import sync_playwright

    orders_arg = {
        oid: {"page": o["page"]} for oid, o in state["orders"].items()
    }
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
            locale="he-IL",
        )
        page.goto(BASE + HOME, wait_until="domcontentloaded", timeout=90_000)
        # Give the Incapsula challenge a moment to set cookies, then verify we
        # actually reached content and not the challenge shell.
        page.wait_for_timeout(4_000)
        html = page.content()
        if "_Incapsula_Resource" in html and "אתר הפקודות" not in html:
            browser.close()
            raise ConnectionError("WAF challenge not passed (Incapsula)")
        # Batches of 10 so slow networks still show liveness between batches
        # (a run over ~25MB of PDFs can legitimately take many minutes).
        current = {}
        ids = list(orders_arg)
        for i in range(0, len(ids), 10):
            batch = {oid: orders_arg[oid] for oid in ids[i:i + 10]}
            current.update(page.evaluate(SNAPSHOT_JS, batch))
            safe_print(f"... {min(i + 10, len(ids))}/{len(ids)} נבדקו")
        news = page.evaluate(NEWS_JS, state.get("news_page", ""))
        # Resolve possible moves for pages that came back 404.
        for oid, cur in current.items():
            if cur.get("page_status") == 404:
                digits = order_digits(state["orders"][oid]["page"])
                if digits:
                    hits = page.evaluate(SEARCH_JS, [state.get("search_path", ""), digits])
                    cur["moved_candidates"] = hits
        browser.close()
    return current, news


def diff(state: dict, current: dict) -> list[dict]:
    """Compare baseline vs current; one dict per document with a verdict."""
    findings = []
    for oid, base in state["orders"].items():
        cur = current.get(oid) or {}
        f = {"id": oid, "title": base.get("title", ""), "page": base["page"],
             "status": "ok", "details": []}
        if cur.get("errors"):
            f["status"] = "error"
            f["details"] += cur["errors"]
        elif cur.get("page_status") != 200:
            f["status"] = "page-error"
            f["details"].append(f"העמוד החזיר {cur.get('page_status')}")
            for cand in cur.get("moved_candidates", [])[:2]:
                f["details"].append(f"אולי עבר לכאן: {cand}")
        elif base.get("html_source"):
            if cur.get("text_sha256") != base.get("text_sha256"):
                f["status"] = "changed"
                f["details"].append(
                    f"תוכן העמוד השתנה (אורך {base.get('text_len')} → {cur.get('text_len')} תווים)")
        else:
            base_pdfs, cur_pdfs = base.get("pdfs", {}), cur.get("pdfs", {})
            for p in sorted(set(base_pdfs) - set(cur_pdfs)):
                f["status"] = "changed"
                f["details"].append(f"קובץ PDF נעלם מהעמוד: {p}")
            for p in sorted(set(cur_pdfs) - set(base_pdfs)):
                f["status"] = "changed"
                f["details"].append(f"קובץ PDF חדש בעמוד: {p}")
            for p in sorted(set(base_pdfs) & set(cur_pdfs)):
                b, c = base_pdfs[p], cur_pdfs[p]
                if c.get("error"):
                    if f["status"] == "ok":
                        f["status"] = "error"
                    f["details"].append(f"שגיאה בהורדת {p}: {c['error']}")
                elif b.get("sha256") != c.get("sha256"):
                    f["status"] = "changed"
                    f["details"].append(
                        f"תוכן ה-PDF השתנה ({b.get('bytes')} → {c.get('bytes')} בייטים): {p}")
        findings.append(f)
    return findings


def render_report(state: dict, findings: list[dict], news: list) -> str:
    today = datetime.date.today().isoformat()
    changed = [f for f in findings if f["status"] == "changed"]
    errors = [f for f in findings if f["status"] in ("error", "page-error")]
    ours_digits = {order_digits(o["page"]) for o in state["orders"].values()}
    lines = [f"# דוח מעקב פקודות — {today}", ""]
    if changed:
        lines.append(f"## 🔔 {len(changed)} פקודות התעדכנו בפורטל")
        lines.append("")
        for f in changed:
            lines.append(f"### {f['id']} — {f['title']}")
            lines.append(f"[לעמוד הפקודה בפורטל]({BASE}{f['page']})")
            for d in f["details"]:
                lines.append(f"- {d}")
            lines.append("")
        lines.append("**מה עושים:** מורידים את ה-PDF המעודכן, מריצים ingest פרטני "
                      "לפקודות שהשתנו, ואז `python _check_updates.py --accept` "
                      "ומקמטים את הבייסליין המעודכן.")
        lines.append("")
    else:
        lines.append("## ✅ אין שינויים — כל הפקודות במעקב זהות לבייסליין")
        lines.append("")
    if errors:
        lines.append(f"## ⚠️ {len(errors)} בעיות גישה (לא בהכרח עדכון)")
        for f in errors:
            lines.append(f"- {f['id']} — {f['title']}: " + "; ".join(f["details"]))
        lines.append("")
    if news:
        lines.append("## 🗞️ מה שהפורטל עצמו מסמן כ'פקודות חדשות' כרגע")
        for n in news:
            d = order_digits(n["h"]) or ""
            marker = " ← **בקורפוס שלנו**" if d in ours_digits else ""
            lines.append(f"- [{n['t']}]({BASE}{n['h']}){marker}")
        lines.append("")
    manual = state.get("manual", {})
    if manual:
        lines.append("## 🖐️ מסמכים במעקב ידני (לא מפורסמים בפורטל)")
        for mid, m in manual.items():
            lines.append(f"- {mid} — {m.get('title','')}: {m.get('reason','')}")
        lines.append("")
    lines.append(f"_נבדקו {len(findings)} מסמכים מול הבייסליין מ-{state.get('built_at','?')}._")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--accept", action="store_true",
                    help="rebase the baseline to the portal's current state")
    ap.add_argument("--ci", action="store_true",
                    help="also write changed=... to $GITHUB_OUTPUT")
    args = ap.parse_args()

    state = load_state()
    try:
        current, news = snapshot(state)
    except Exception as e:  # WAF / network — a hard failure, not "no changes"
        safe_print(f"שגיאה: לא הצלחתי להגיע לפורטל ({e})")
        if args.ci and os.environ.get("GITHUB_OUTPUT"):
            with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as fh:
                fh.write("changed=error\n")
        return 3

    findings = diff(state, current)
    report = render_report(state, findings, news)
    REPORT_PATH.write_text(report, encoding="utf-8")
    safe_print(report)

    changed = any(f["status"] == "changed" for f in findings)
    if args.ci and os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as fh:
            fh.write(f"changed={'true' if changed else 'false'}\n")
            fh.write(f"report={REPORT_PATH.as_posix()}\n")

    if args.accept:
        for oid, order in state["orders"].items():
            cur = current.get(oid) or {}
            if cur.get("page_status") == 200:
                order["pdfs"] = {p: v for p, v in cur.get("pdfs", {}).items()
                                 if "sha256" in v}
                order["text_sha256"] = cur.get("text_sha256")
                order["text_len"] = cur.get("text_len")
        state["built_at"] = datetime.date.today().isoformat()
        STATE_PATH.write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        safe_print(f"\nהבייסליין עודכן ({STATE_PATH.name}) — יש לקמט את הקובץ.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
