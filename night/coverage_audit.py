# -*- coding: utf-8 -*-
"""Block-coverage audit: rules that are in an order's raw_text but not in its curated block.

PM-33.0309 „מעצר וחיפושים כללי" had a 4-clause block, all on arrest, and a whole search chapter
(clauses 119-129) that no clause carried. A soldier asking about a search got „not in the orders".
This finds that class of miss across the corpus. Free: no model, no network, reads json_store only.

Four checks per order, all against the order's curated sections (every section in `sections`):

(a) TITLE TERMS — content words of the title with no clause mentioning them. The check that would
    have caught 33.0309 at once („חיפושים"). The part before " - " is usually a series name
    („משטר המחנה - חומרים משכרים") and is skipped; parentheses (an issuer, a date) are skipped;
    generic words (TITLE_GENERIC) are skipped. A term is covered when any of its stems
    (night.curate._norm: prefix and suffix stripping) appears in some clause's title or text.
(b) RULE SENTENCES — raw units carrying a normative marker (NORMATIVE) whose best content overlap
    with any single clause is below night.sectprobe.CONTENT_MIN (0.30) — the same exact-token rule,
    calibrated on this same pairing (a raw quote against a curated clause). A unit that ends in ":"
    with a marker passes its normativity to the list items after it (13: „…יהיה רשאי לנהוג
    כלהלן: א. לערוך חיפוש…"), until the next numbered clause. Units with fewer than
    MIN_CONTENT content words are skipped; units over LONG_UNIT words are cut at commas.
    FILTER (administrative, not a soldier's rule), recorded here as the manager asked: a unit is
    dropped when it matches ADMIN — distribution, oversight and audit, cancellation of earlier
    orders, responsibility for implementing the order, the clause-validity header lines, and
    web-page debris („אהבתי", „שיתוף").
    SOLDIER-RELEVANT (for the report's ranking only): the unit names a serving person (SOLDIER).
(c) HEADINGS — short raw lines that look like chapter headings (1-6 words, no digits, not a
    sentence fragment, not boilerplate) with no clause carrying at least half of their words.
    Orders scraped from the web keep headings inline, so this check is blind there.
(d) NUMBERS — numbers in a digit block that do not appear in the order's raw_text
    (night.numbers, the gate apply_defs already runs), over the whole corpus.

Raw chunks are still indexed; „uncovered" means „not in the curated block", which is what
RETRIEVE_DOC_BLOCKS serves and what the router reads.

    venv\\Scripts\\python.exe -m night.coverage_audit --doc PM-33.0309     # one order, in full
    venv\\Scripts\\python.exe -m night.coverage_audit --report             # night/out/coverage_audit.json + summary
    venv\\Scripts\\python.exe -m night.coverage_audit --relevance          # windows of ruler/head-100/real24 (loads the model)
    venv\\Scripts\\python.exe -m night.coverage_audit --write-baseline     # the ratchet (tests/test_coverage_ratchet.py)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

JSON_DIR = ROOT / "storage" / "json_store"
BASELINE = ROOT / "night" / "coverage_baseline.json"
OUT = ROOT / "night" / "out"

CONTENT_MIN = 0.30          # night.sectprobe.CONTENT_MIN — kept equal by tests/test_coverage_audit.py
MIN_CONTENT = 4             # content words a unit needs before it can be called a rule
LONG_UNIT = 60              # words; longer units are cut at commas into pieces of <= 40
HEADING_SHARE = 0.34
TITLE_MIN_RAW = 6

# the sectprobe content rule, copied (importing sectprobe loads the backend)
_STOP = frozenset(
    "של על את עם לא אם או כל גם רק יש אין זה זו הוא היא הם הן אני אתה כי מה מי "
    "איך מתי אבל אשר כדי לפי בין עד בכל לכל ואת שלא אלא ידי ואם ולא".split())
_PUNCT = ".,;:!?()[]{}'„“”‘’׳״-–—/|" + chr(34)
_PUNCT_TABLE = str.maketrans({c: " " for c in _PUNCT})


def content_words(s: str) -> set[str]:
    return {w for w in (s or "").translate(_PUNCT_TABLE).split()
            if len(w) >= 3 and w not in _STOP and not w.isdigit()}


def overlap(unit: str, text: str) -> float:
    uw = content_words(unit)
    if len(uw) < 3:
        return 0.0
    return len(uw & content_words(text)) / len(uw)


_P = "(?<![א-ת])[ולבמכשה]{0,2}"
NORMATIVE = re.compile(
    _P + r"(רשאי|רשאית|רשאים|רשאיות|זכאי|זכאית|זכאים|זכאיות|אסור|אסורה|אסורים|אסורות|ייאסר|תיאסר|ייאסרו"
    r"|חייב|חייבת|חייבים|חייבות|מותר|מותרת|מותרים|מותרות|יוטל|תוטל|יוטלו|ייערך|תיערך|ייערכו"
    r"|יוגש|תוגש|יוגשו)(?![א-ת])"
    r"|(?<![א-ת])ב?תוך\s+\S+\s+(ימים|יום|שעות|שעה|חודשים|חודש|שבועות|שבוע)(?![א-ת])")
ADMIN = re.compile(
    r"הפצה|יופץ|יופצו|יפיץ|פיקוח ובקרה|(?<![א-ת])[ית]בקר(?![א-ת])|ביקורות"
    r"|מבטלת|(?<![א-ת])בטלה|(?<![א-ת])בטלות|בוטלה|בוטלו|תבוטל|יבוטל"
    r"|אחריות ליישום|ליישום פקודה|לביצוע פקודה|תוקף סעיף|תוקף הסעיפים|תוקף הפקודה"
    r"|אהבתי|שיתוף במייל|שיתוף ב")
SOLDIER = re.compile(
    _P + r"(חייל|חיילת|חיילים|חיילות|חיילי|משרת|משרתת|משרתים|מילואים|מתגייס|מתגייסת|מתגייסים|מלש\"ב"
    r"|עציר|עצור|עצורים|נאשם|נפגע|נפגעת|משוחרר|משוחררת|מועמד|מועמדת|טירון|טירונים)(?![א-ת])")
TITLE_GENERIC = {
    "כללי", "כללית", "כלליות", "כלליים", "פקודה", "פקודות", "הוראה", "הוראות", "נוהל", "נהלים", "נוהלי",
    "צה\"ל", "בצה\"ל", "לצה\"ל", "הצבא", "בצבא", "צבאי", "צבאית", "צבאיים", "נושאים", "שונים", "עקרונות",
    "מדיניות", "טיפול", "הטיפול", "ניהול", "סדרי", "דרכי", "אופן", "הסדרת", "שימוש", "השימוש", "מטכ\"ל",
    "קצין", "ראשי", "יוני", "מאי", "והנחיות", "הנחיות", "עדכון", "בדבר", "לגבי", "בנושא",
    # framing words: the title frames the subject, the block states the rule („הגבלת שימוש בטלפון")
    "הגבלת", "הגבלה", "הגבלות", "סדרים", "הסדרים", "נהלי", "כללים",
    "הגדרות", "מטעם", "בעת", "במהלך", "שאינם", "חלק",
}
_DANGLING = {"או", "ואם", "אם", "עוד", "כל", "את", "של", "על", "עם", "בדבר", "לפי", "ואת", "כי", "אשר", "לא", "גם"}
_DANGLING_START = {"ראה", "כל", "או", "ואם", "אם", "את", "של", "אשר", "כי", "לצורך", "יצוינו"}
HEADING_BOILER = re.compile(
    r"^(בלמ\"ס|שמור|סודי|פקודות מטכ\"ל|הגדרות|כללי|מטרה|מטרת הפקודה|מטרות|הפצה|פיקוח ובקרה|תחולה|סימוכין"
    r"|נספח|נספחים|אחריות|מבוא|רקע|כללים|עקרונות|שיתוף|אהבתי|תוכן העניינים|שונות|ביטול|תוקף)\b")


_FINALS = str.maketrans("םןץףך", "מנצפכ")
_SUFFIXES = ("ויות", "יות", "יים", "ים", "ות", "ה", "י", "ן", "ך", "ו", "ת", "א")


def _stems(text: str) -> set[str]:
    """Comparable stems: one prefix letter, then one suffix, THEN the final-letter fold.
    (night.curate._norm folds first, so its „ים" never strips — „משכרים" stays „משכרימ".)"""
    out: set[str] = set()
    for w in re.findall(r"[א-ת]{3,}", text or ""):
        forms = {w}
        if len(w) > 3 and w[0] in "הובלמכש":
            forms.add(w[1:])
        for f in list(forms):
            for suf in _SUFFIXES:
                if len(f) > len(suf) + 2 and f.endswith(suf):
                    forms.add(f[: -len(suf)])
        out |= {f.translate(_FINALS) for f in forms}
    return out


def block_clauses(doc: dict) -> list[dict]:
    out = []
    for s in doc.get("sections") or []:
        for c in s.get("clauses") or []:
            out.append({"section": s.get("id"), "digit_free": bool(s.get("digit_free")) or "nodigits" in (s.get("id") or ""),
                        "number": str(c.get("number", "")), "text": str(c.get("text", ""))})
    return out


# ── (a) title terms ─────────────────────────────────────────────────────────
def title_terms(title: str) -> list[str]:
    t = re.sub(r"\([^)]*\)", " ", title or "")
    parts = re.split(r"\s[-–—]\s", t)
    t = parts[-1] if len(parts) > 1 else t
    t = t.replace("״", '"')
    out = []
    for w in t.split():
        w = w.strip(".,;:()[]\"'׳„“”-–—")
        if len(re.sub(r"[^א-ת]", "", w)) < 3 or re.search(r"\d", w):
            continue
        bare = w[1:] if len(w) > 3 and w[0] in "ו" else w
        if w in TITLE_GENERIC or bare in TITLE_GENERIC:
            continue
        if w not in out:
            out.append(w)
    return out


def _term_covered(term: str, text: str, stems: set[str]) -> bool:
    t = term.replace("״", '"')
    if '"' in t:                          # an acronym (נח"ל, מצ"ח): the regex stemmer cannot see it
        bare = t.lstrip("ובלמהשכ") if t[0] in "ובלמהשכ" and len(t) > 4 else t
        return t in text or bare in text
    ts = _stems(t)
    if ts & stems:
        return True
    for a in ts:
        if len(a) >= 5 and any(len(b) >= 4 and a[:len(a) - 1] == b[:len(a) - 1] for b in stems):
            return True
    return False


def raw_frequency(term: str, raw: str) -> int:
    """How often the order itself uses the term (any inflection; an acronym as a string)."""
    t = term.replace("״", '"')
    if '"' in t:
        return (raw or "").replace("״", '"').count(t)
    ts = _stems(t)
    return sum(1 for w in re.findall(r"[א-ת]{3,}", raw or "") if _stems(w) & ts)


def uncovered_title_terms(title: str, clauses: list[dict], raw: str | None = None) -> list[str]:
    """Title terms with no clause. With `raw`, only terms the order uses at least TITLE_MIN_RAW
    times: a term the text barely uses frames the title rather than naming a chapter
    („הפקעת", „הענקת" — 3 and 1 uses; „חיפושים" in 33.0309 — 25)."""
    text = " ".join(c["number"] + " " + c["text"] for c in clauses).replace("״", '"')
    stems = _stems(text)
    out = []
    for t in title_terms(title):
        if _term_covered(t, text, stems):
            continue
        if raw is not None and raw_frequency(t, raw) < TITLE_MIN_RAW:
            continue
        out.append(t)
    return out


# ── (b) rule units ──────────────────────────────────────────────────────────
_SPLIT = re.compile(r"(\s[.;:](?=\S)|(?<=\S)[.;:](?=\s|$))")


def rule_units(raw: str) -> list[dict]:
    """[{text, normative, admin, soldier}] in raw order."""
    text = re.sub(r"\s+", " ", (raw or "").replace("\n", " "))
    parts = _SPLIT.split(text)
    units: list[dict] = []
    inherit = False
    for i in range(0, len(parts), 2):
        unit = parts[i].strip()
        delim = parts[i + 1].strip() if i + 1 < len(parts) else ""
        if not unit:
            continue
        # a unit that ends in a clause number („…אחריות מפקדים 13") opens a new clause
        if re.search(r"(?<![\d.])\d{1,3}$", unit):
            inherit = False
            unit = re.sub(r"\s*\d{1,3}$", "", unit).strip()
            if not unit:
                continue
        if re.fullmatch(r"[א-ת]{1,2}\)?", unit):        # a list letter
            continue
        own = bool(NORMATIVE.search(unit))
        pieces = [unit]
        if len(unit.split()) > LONG_UNIT:
            pieces, cur = [], []
            for seg in re.split(r"(?<=,)\s", unit):
                if cur and len(" ".join(cur + [seg]).split()) > 40:
                    pieces.append(" ".join(cur)); cur = []
                cur.append(seg)
            if cur:
                pieces.append(" ".join(cur))
        lead_in = delim == ":" and len(unit.split()) < 15
        for p in pieces:
            units.append({"text": p, "normative": (own or inherit or bool(NORMATIVE.search(p))) and not lead_in,
                          "admin": bool(ADMIN.search(p)), "soldier": bool(SOLDIER.search(p))})
        if own and delim == ":":
            inherit = True
    return units


def uncovered_rules(raw: str, clauses: list[dict]) -> list[dict]:
    texts = [c["number"] + " " + c["text"] for c in clauses]
    out = []
    for u in rule_units(raw):
        if not u["normative"] or u["admin"] or len(content_words(u["text"])) < MIN_CONTENT:
            continue
        best, which = 0.0, None
        for i, t in enumerate(texts):
            o = overlap(u["text"], t)
            if o > best:
                best, which = o, i
        if best < CONTENT_MIN:
            out.append({"text": u["text"], "overlap": round(best, 2), "soldier": u["soldier"],
                        "best_clause": clauses[which]["number"][:60] if which is not None else None})
    return out


# ── (c) headings ────────────────────────────────────────────────────────────
def headings(raw: str, title: str = "") -> list[str]:
    lines = [ln.strip() for ln in (raw or "").splitlines()]
    out = []
    tnorm = re.sub(r"\s+", " ", (title or "").strip())
    for i, ln in enumerate(lines):
        if re.match(r"^נספח", ln):
            break                         # annexes are forms: their field names are not chapters
        if not ln or re.search(r"\d", ln) or ln[0] in ".,;:)(-–" or ln[-1] in ".,;:-–(":
            continue
        if re.search(r"[\u0591-\u05C7]", ln) or re.search(r"\s{3,}", ln):
            continue                      # niqqud or form columns
        words = ln.split()
        if not (1 <= len(words) <= 6):
            continue
        heb = [w for w in words if len(re.sub(r"[^א-ת]", "", w)) >= 3]
        if not heb or (len(words) == 1 and len(re.sub(r"[^א-ת]", "", words[0])) < 4):
            continue
        if HEADING_BOILER.search(ln) or ln == tnorm:
            continue
        if ("," in ln or re.search(r"[א-ת]\.[א-ת]", ln) or ln[0] in "\"'"
                or words[-1] in _DANGLING or words[0] in _DANGLING_START):
            continue                    # a sentence fragment broken across PDF lines, not a heading
        # a heading is followed by a clause number — its own line („112", „16.") or the start of
        # the next line („13. מפקד…") — before any running text; else it is a PDF line break
        ahead = [x for x in lines[i + 1:i + 6] if x][:4]
        numbered = False
        for x in ahead:
            if re.fullmatch(r"[\d.\s]+", x) and re.search(r"\d", x) or re.match(r"^\d{1,3}\.\s", x):
                numbered = True
                break
            if len(x.split()) > 6:
                break
        if not numbered:
            continue
        if len(words) == 1:
            first = ahead[0] if ahead else ""
            if re.search(r"\d", first) or not (1 <= len(first.split()) <= 6):
                continue                  # a lone word is a chapter heading only above a sub-heading
        if ln not in out:
            out.append(ln)
    return out


def uncovered_headings(raw: str, title: str, clauses: list[dict]) -> list[str]:
    cstems = [_stems(c["number"] + " " + c["text"]) for c in clauses]
    tstems = _stems(title)
    out = []
    for h in headings(raw, title):
        words = [w for w in h.split() if len(re.sub(r"[^א-ת]", "", w)) >= 3 and w not in TITLE_GENERIC
                 and not (_stems(w) & tstems)]
        if not words:
            continue
        best = max((sum(1 for w in words if _stems(w) & cs) / len(words) for cs in cstems), default=0.0)
        if best < HEADING_SHARE:
            out.append(h)
    return out


# ── (d) numbers ─────────────────────────────────────────────────────────────
def number_misses(raw: str, clauses: list[dict]) -> list[str]:
    from night import numbers as N
    miss = []
    for c in clauses:
        if c["digit_free"]:
            continue
        for x in sorted(N.numbers_in(c["text"])):
            try:
                ok = N.present(x, raw)
            except Exception:
                ok = x in raw
            if not ok:
                miss.append(f"{c['number'][:30]}: {x}")
    return miss


# ── per order and corpus ────────────────────────────────────────────────────
def audit_doc(doc: dict) -> dict:
    clauses = block_clauses(doc)
    raw = doc.get("raw_text") or ""
    title = doc.get("title") or ""
    r = {"doc_id": doc.get("document_id"), "title": title, "clauses": len(clauses)}
    if not clauses:
        r.update({"no_block": True, "title_uncovered": [], "rules": [], "headings_uncovered": [], "number_misses": []})
        return r
    r["no_block"] = False
    r["title_uncovered"] = uncovered_title_terms(title, clauses, raw)
    r["rules"] = uncovered_rules(raw, clauses)
    r["headings_uncovered"] = uncovered_headings(raw, title, clauses)
    r["number_misses"] = number_misses(raw, clauses)
    return r


def load_corpus(json_dir: Path = JSON_DIR) -> list[dict]:
    docs = []
    for f in sorted(json_dir.glob("*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        if d.get("document_id"):
            docs.append(d)
    return docs


def audit_corpus(json_dir: Path = JSON_DIR) -> dict[str, dict]:
    return {r["doc_id"]: r for r in (audit_doc(d) for d in load_corpus(json_dir))}


def ratchet_view(results: dict[str, dict]) -> dict[str, dict]:
    """What the ratchet locks per order: the uncovered title terms (a set) and three counts."""
    return {k: {"title": sorted(v["title_uncovered"]), "rules": len(v["rules"]),
                "headings": len(v["headings_uncovered"]), "numbers": len(v["number_misses"]),
                "no_block": v["no_block"]}
            for k, v in sorted(results.items())}


def regressions(now: dict[str, dict], base: dict[str, dict]) -> list[str]:
    bad = []
    for k, v in now.items():
        b = base.get(k)
        if b is None:
            if v["title"] or v["numbers"]:
                bad.append(f"{k}: new order with uncovered title terms {v['title']} / {v['numbers']} number misses")
            continue
        if b.get("no_block") and not v["no_block"]:
            continue                      # an order that just got its first block is judged from the next baseline
        new_terms = sorted(set(v["title"]) - set(b["title"]))
        if new_terms:
            bad.append(f"{k}: title term(s) lost their clause: {new_terms}")
        for m in ("rules", "headings", "numbers"):
            if v[m] > b[m]:
                bad.append(f"{k}: uncovered {m} {b[m]} -> {v[m]}")
    return bad


# ── relevance: how often each order reaches a free window ──────────────────
V159 = {"RETRIEVE_GLOSSARY": "1", "RETRIEVE_FULL_BLOCKS": "1", "RETRIEVE_ROUTER_SLOTS": "2",
        "RETRIEVE_DOC_BLOCKS": "7", "RETRIEVE_LACK_CLAUSES": "3", "RETRIEVE_KEEP_RULING": "1",
        "RETRIEVE_FULL_BLOCK_MAX_WORDS": "2000", "RETRIEVE_HOMONYMS": "1", "RETRIEVE_QUOTELESS": "1",
        "RETRIEVE_HYDE": "0"}


def relevance(real_questions: Path | None) -> dict[str, int]:
    import os
    for k, v in V159.items():
        os.environ[k] = v
    os.environ.pop("ANTHROPIC_API_KEY", None)
    from night import sectprobe as sp
    qs = [(t["q"], t["role"]) for t in sp.targets()]
    h = json.loads((ROOT / "night" / "head100" / "targets.json").read_text(encoding="utf-8"))
    qs += [(r["question"], r.get("role") or "soldier") for r in h]          # dev and held, aggregate only
    if real_questions and real_questions.exists():
        qs += [(r.get("clean_q") or r["q"], r.get("role") or "soldier")
               for r in json.loads(real_questions.read_text(encoding="utf-8"))]
    count: dict[str, int] = {}
    for q, role in qs:
        seen = {c["doc_id"] for c in sp._free_window(q, role)}
        for d in seen:
            count[d] = count.get(d, 0) + 1
    print(f"[relevance] {len(qs)} windows (ruler + head-100 + real24), {len(count)} orders reached at least once")
    return dict(sorted(count.items(), key=lambda x: -x[1]))


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--doc")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--relevance", action="store_true")
    ap.add_argument("--real", default=r"D:/app_soldier/night/out/real_questions.json")
    ap.add_argument("--write-baseline", action="store_true")
    a = ap.parse_args(argv)
    if a.doc:
        d = next(x for x in load_corpus() if x["document_id"] == a.doc)
        print(json.dumps(audit_doc(d), ensure_ascii=False, indent=1))
        return 0
    if a.relevance:
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "coverage_relevance.json").write_text(
            json.dumps(relevance(Path(a.real)), ensure_ascii=False, indent=1), encoding="utf-8")
        return 0
    res = audit_corpus()
    if a.write_baseline:
        BASELINE.write_text(json.dumps(ratchet_view(res), ensure_ascii=False, indent=1) + "\n",
                            encoding="utf-8", newline="\n")
        print(f"[coverage] baseline written: {len(res)} orders -> {BASELINE.relative_to(ROOT)}")
    if a.report:
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "coverage_audit.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    blocks = [r for r in res.values() if not r["no_block"]]
    print(f"[coverage] {len(res)} orders, {len(blocks)} with a block, {len(res) - len(blocks)} without | "
          f"title terms uncovered in {sum(1 for r in blocks if r['title_uncovered'])} | "
          f"uncovered rules {sum(len(r['rules']) for r in blocks)} "
          f"({sum(1 for r in blocks for u in r['rules'] if u['soldier'])} soldier-relevant) | "
          f"headings {sum(len(r['headings_uncovered']) for r in blocks)} | "
          f"number misses {sum(len(r['number_misses']) for r in blocks)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
