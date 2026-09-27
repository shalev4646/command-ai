# -*- coding: utf-8 -*-
"""Block-support audit: sentences in a curated block that the order's raw_text does not support.

The reverse of night/coverage_audit.py. Coverage asks „is every rule of the order in the block?";
support asks „is every sentence of the block in the order?". No gate checked that direction:
night.curate.check tests a clause's vocabulary against the WHOLE raw_text (MAX_UNGROUNDED) and
night.numbers tests each number against the WHOLE raw_text. Both pass a sentence whose words and
digits all occur somewhere in a long order, even when no passage says what the sentence says.

What made this concrete (27.09, v161): 36.0505's old block said „תוך 30 יום מקבלת המפתח… שכ"ד
בגובה שליש מן המשכורת לכל יום עיכוב". The order says 60 days from the committee's decision and
1/30 of the salary (סע' 28). Every word and „30" occur somewhere in the order — the gates passed.
And 32.0220's „40 חודשים… 16 חודשי שירות" passed because „16" occurs elsewhere; the passage that
carries the rule says 24 and 36.

Per block sentence (clauses split at . and ;):
  SUPPORT  — the best passage of the raw_text (a window of WINDOW consecutive raw units) and the
             share of the sentence's content words it carries, weighted by rarity (log N/df over
             the corpus, so „חייל" or „יהיה" cannot carry a sentence). Words match by stem, by
             defective spelling (חיפשו ~ לחפש), and by acronym: „ראש ענף תנאי שירות קבע" is supported
             by „ראש עת"ש קבע" when the initials of consecutive words spell an acronym of the order.
             (The review of 27.09 wrongly called 36.0505's סע' 29 invented because a text search
             missed „עת"ש" — a tool that repeats that mistake is worse than none.)
  ENTITIES — checked against the supporting passage (±NEAR units), not the whole order:
             numbers (digits, or the Hebrew number words of the same value), fractions (חצי, שליש,
             רבע…), ranks. Acronyms/roles with gershayim are checked against the whole order.

„Unsupported" is a signal for a human with the page, never a verdict: a block that paraphrases
freely scores low, and on raw_text whose digits are scrambled (night.digits.trustworthy False)
every number check is noise — those are reported apart. As in the coverage audit, the signal is
concentration per order.

    venv\\Scripts\\python.exe -m night.support_audit --doc 32.0220
    venv\\Scripts\\python.exe -m night.support_audit --report          # night/out/support_audit.json
    venv\\Scripts\\python.exe -m night.support_audit --write-baseline  # the ratchet
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from night import coverage_audit as ca  # noqa: E402

OUT = ROOT / "night" / "out"
BASELINE = ROOT / "night" / "support_baseline.json"
WINDOW = 3          # consecutive raw units per passage
NEAR = 2            # entity check: units on each side of the best passage
SUPPORT_MIN = 0.50  # calibrated below (tests/test_support_audit.py records the calibration cases)
NUM_RELIABLE = 0.80 # a block's own numbers found in their passages at this rate ⇒ the raw digits are usable
NUM_MIN = 3         # …judged on at least this many numbers
MIN_WORDS = 4       # sentences with fewer content words are not judged

# acronym ranks only: „סמל" is also an emblem, „סגן" a deputy — a word, not an entity
RANKS = ['רב"ט', 'סמ"ר', 'רס"ל', 'רס"ר', 'רס"ם', 'רס"ב', 'רנ"ג', 'סג"ם', 'רס"ן', 'סא"ל', 'אל"ם', 'תא"ל', 'רא"ל', 'קא"ב', 'קמ"א']
# abbreviations of ordinary Hebrew, never an entity of the order
# army-wide institutions and instruments: never an entity that a block could invent for one order
GENERIC_ABBR = {'צה"ל', 'אכ"א', 'רמטכ"ל', 'מטכ"ל', 'פ"מ', 'הק"א', 'הפ"ע', 'משהב"ט', 'מטכ"לי', 'ת"ש',
                'אח"כ', 'וכו"', 'ע"י', 'עפ"י', 'ע"פ', 'כנ"ל', 'הנ"ל', 'בד"כ', 'בי"ס', 'ביה"ס', 'אא"כ', 'עד"מ', 'לדוג"'}
FRACTIONS = {"חצי": "1/2", "מחצית": "1/2", "שליש": "1/3", "רבע": "1/4", "שלושה רבעים": "3/4", "שלושת רבעי": "3/4",
             "שני שלישים": "2/3", "שני שלישי": "2/3"}
_ONES = {1: ["אחד", "אחת"], 2: ["שניים", "שתיים", "שני", "שתי"], 3: ["שלושה", "שלוש", "שלושת"], 4: ["ארבעה", "ארבע", "ארבעת"],
         5: ["חמישה", "חמש", "חמשת"], 6: ["שישה", "שש", "ששת"], 7: ["שבעה", "שבע", "שבעת"], 8: ["שמונה", "שמונת"],
         9: ["תשעה", "תשע", "תשעת"], 10: ["עשרה", "עשר", "עשרת"]}
_TENS = {20: "עשרים", 30: "שלושים", 40: "ארבעים", 50: "חמישים", 60: "שישים", 70: "שבעים", 80: "שמונים", 90: "תשעים"}
_HUNDREDS = {100: ["מאה"], 200: ["מאתיים"], 300: ["שלוש מאות"], 400: ["ארבע מאות"], 500: ["חמש מאות"]}


def number_words(n: int) -> list[str]:
    """Hebrew spellings of n (both genders, with and without the conjunctive ו), for n < 600."""
    if n in _ONES:
        return _ONES[n]
    if 11 <= n <= 19:
        u = _ONES[n - 10]
        return [f"{w} עשר" for w in u] + [f"{w} עשרה" for w in u]
    if n in _TENS:
        return [_TENS[n]]
    if 21 <= n <= 99:
        t, u = _TENS[n - n % 10], _ONES[n % 10]
        return [f"{t} ו{w}" for w in u] + [f"{t} ו-{w}" for w in u]
    if n in _HUNDREDS:
        return _HUNDREDS[n]
    if 100 < n < 600 and (n - n % 100) in _HUNDREDS:
        rest = number_words(n % 100)
        return [f"{h} ו{r}" for h in _HUNDREDS[n - n % 100] for r in rest] + [f"{h} {r}" for h in _HUNDREDS[n - n % 100] for r in rest]
    return []


# ── words ────────────────────────────────────────────────────────────────────
def _word_forms(w: str) -> set[str]:
    out = set(f for f in ca._stems(w) if len(f) >= 3)
    base = w[1:] if len(w) > 3 and w[0] in "ובלמהשכ" else w
    for b in {w, base}:
        if len(b) >= 4:
            d = b[0] + re.sub(r"[וי]", "", b[1:])
            if len(d) >= 3:
                out.add(d.translate(ca._FINALS))
    return out


def _words(text: str) -> list[str]:
    return [w for w in re.findall(r"[א-ת]{3,}", text or "") if w not in ca._STOP]


def _forms(text: str) -> set[str]:
    out: set[str] = set()
    for w in _words(text):
        out |= _word_forms(w)
    return out


def acronyms(raw: str) -> set[str]:
    """The order's own acronyms, letters only: עת"ש -> עתש, רמ"ח -> רמח."""
    out = set()
    for tok in re.findall(r"[א-ת]+[\"״][א-ת]+", (raw or "").replace("״", '"')):
        letters = tok.replace('"', "")
        if 2 <= len(letters) <= 6:
            out.add(letters.translate(ca._FINALS))
    return out


def acronym_covered(words: list[str], acr: set[str]) -> set[int]:
    """Indexes of sentence words whose initials, over 2–5 consecutive words, spell an acronym of the
    order (a leading prefix letter of the first word ignored): „ענף תנאי שירות" -> עתש."""
    hit: set[int] = set()
    inits = []
    for w in words:
        i = w[0]
        inits.append((i, w[1] if len(w) > 3 and w[0] in "ובלמהשכ" else i))
    for start in range(len(words)):
        for ln in range(2, 6):
            if start + ln > len(words):
                break
            for first in {inits[start][0], inits[start][1]}:
                s = (first + "".join(inits[k][0] for k in range(start + 1, start + ln))).translate(ca._FINALS)
                if s in acr:
                    hit |= set(range(start, start + ln))
    return hit


# ── entities ────────────────────────────────────────────────────────────────
_NUM = re.compile(r"(?<![\d/])(\d+(?:[.,:]\d+)*(?:/\d+)?)(?![\d/])")


# clause citations are checked by night.curate's citation gate, not here: „(סעיפים 7, 9, 13)",
# „(סעיף 45ב–ה)", „סע' 28", „בסעיף 10 לחוק"
_CITE = re.compile(r"\((?:סעיף|סעיפים|סע'|ס')[^)]*\)|(?<![א-ת])[ובלמהשכ]{0,2}(?:סעיף|סעיפים|סע'|ס')\s*\d+[א-ת]?(?:\s*[–\-—,]\s*\d+[א-ת]?)*")


def numbers_of(text: str) -> list[str]:
    out = []
    text = _CITE.sub(" ", text or "")
    for m in _NUM.finditer(text):
        tok = m.group(1).replace(",", "")
        if re.fullmatch(r"\d{1,2}:\d{2}", tok):
            out.append(tok)
        elif "/" in tok:
            out.append(tok)
        else:
            out.append(tok.rstrip("."))
    return out


def number_in(num: str, passage: str) -> bool:
    p = (passage or "").replace(",", "")
    if re.search(rf"(?<![\d]){re.escape(num)}(?![\d])", p):
        return True
    if re.fullmatch(r"\d+", num):
        n = int(num)
        if any(w in p for w in number_words(n)):
            return True
    if "/" in num:
        for word, val in FRACTIONS.items():
            if val == num and word in p:
                return True
    return False


def fractions_of(text: str) -> list[str]:
    return [w for w in FRACTIONS if re.search(rf"(?<![א-ת])[ובלמהשכ]?{w}(?![א-ת])", text or "")]


def _strip_prefix(tok: str) -> set[str]:
    """An acronym token and its forms without up to two prefix letters (לסמ"ר -> סמ"ר)."""
    out = {tok}
    for _ in range(2):
        out |= {t[1:] for t in out if len(t.split('"')[0]) >= 2 and t[0] in "ובלמהשכ"}
    return out


def ranks_of(text: str) -> list[str]:
    t = (text or "").replace("״", '"')
    return [r for r in RANKS if re.search(rf"(?<![א-ת])[ובלמהשכ]{{0,2}}{re.escape(r)}(?![א-ת])", t)]


def roles_of(text: str) -> list[str]:
    t = (text or "").replace("״", '"')
    out = set()
    for m in re.findall(r"[א-ת]+\"[א-ת]+", t):
        forms = _strip_prefix(m)
        if forms & GENERIC_ABBR or any(f in RANKS for f in forms):
            continue
        out.add(m)
    return sorted(out)


def _in_order(tok: str, raw_norm: str, raw_letters: str) -> bool:
    """An acronym of the block is in the order in any prefixed form, with or without its gershayim."""
    for f in _strip_prefix(tok):
        if f in raw_norm or f.replace('"', "") in raw_letters:
            return True
        # OCR writes the gershayim as „יי" (רמ"ח -> רמייח, היועכ"ל -> היועכ"יל); PDF extraction often
        # drops it to a space (צה"ל -> „צה ל", אכ"א -> „אכ א")
        if '"' in f and (f.replace('"', "יי") in raw_norm or f.replace('"', "י") in raw_letters
                         or f.replace('"', " ") in raw_norm):
            return True
        # extraction splits a long acronym into letters („מ ח ה"ס"): compare with spaces and dots removed
        letters = f.replace('"', "")
        if len(letters) >= 4 and letters in _squeezed(raw_norm):
            return True
    return False


_SQ: dict[int, str] = {}


def _squeezed(raw_norm: str) -> str:
    k = id(raw_norm)
    if k not in _SQ:
        _SQ.clear()
        _SQ[k] = re.sub(r'[\s."]', "", raw_norm)
    return _SQ[k]


# ── per order ───────────────────────────────────────────────────────────────
def sentences(clause_text: str) -> list[str]:
    parts = re.split(r"(?<=[.;])\s+|;\s*", clause_text or "")
    return [p.strip() for p in parts if p and len(_words(p)) >= 1]


def passages(raw: str) -> tuple[list[str], list[str]]:
    """(windows of WINDOW consecutive raw units, the units)."""
    units = [u["text"] for u in ca.rule_units(raw)] or [raw or ""]
    return [" ".join(units[i:i + WINDOW]) for i in range(max(1, len(units) - WINDOW + 1))], units


def build_df(docs: list[dict]) -> tuple[dict[str, int], int]:
    df: dict[str, int] = {}
    for d in docs:
        for f in _forms(d.get("raw_text") or ""):
            df[f] = df.get(f, 0) + 1
    return df, len(docs)


def audit_doc(doc: dict, df: dict[str, int], n_docs: int) -> dict:
    raw = doc.get("raw_text") or ""
    # Whether the raw digits can be read is measured on the block itself: night.digits.trustworthy
    # passed 32.0220, whose raw_text writes „10.2001" where the page says 32.0223.
    trusted = None
    wins, units = passages(raw)
    win_forms = [_forms(w) for w in wins]
    acr = acronyms(raw)
    raw_norm = raw.replace("״", '"')
    raw_letters = raw_norm.replace('"', "")
    out = []
    num_total = num_found = 0
    pv = ca.page_verified(doc)            # read on the page ⇒ not counted (coverage_audit.page_verified)
    for c in ca.block_clauses(doc):
        for s in sentences(c["text"]):
            words = _words(s)
            if len(words) < MIN_WORDS:
                continue
            forms = [_word_forms(w) for w in words]
            weight = [max((math.log(n_docs / max(df.get(f, 0), 1)) for f in fs), default=0.0) or 0.1 for fs in forms]
            acr_idx = acronym_covered(words, acr)
            best, bi = -1.0, 0
            total = sum(weight) or 1.0
            for i, wf in enumerate(win_forms):
                got = sum(weight[k] for k, fs in enumerate(forms) if (fs & wf) or k in acr_idx)
                if got > best:
                    best, bi = got, i
            support = round(best / total, 2)
            lo, hi = max(0, bi - NEAR), min(len(units), bi + WINDOW + NEAR)
            near = " ".join(units[lo:hi]) if units else raw
            # numbers: in the supporting passage — only where the raw digits can be trusted; on scrambled
            # raw a correct value fails as often as a wrong one (measured on the v161 orders, 27.09)
            nums = ([x for x in numbers_of(s) if not ca.is_page_verified(pv, c["number"], x)]
                    if not c["digit_free"] else [])
            miss_num = [x for x in nums if not number_in(x, near)]
            num_total += len(nums)
            num_found += len(nums) - len(miss_num)
            miss_frac = [x for x in fractions_of(s) if not (x in near or number_in(FRACTIONS[x], near))]
            miss_rank = [r for r in ranks_of(s) if not _in_order(r, raw_norm, raw_letters)]
            miss_role = [r for r in roles_of(s) if not _in_order(r, raw_norm, raw_letters)]
            if support < SUPPORT_MIN or miss_num or miss_frac or miss_rank or miss_role:
                out.append({"clause": c["number"][:70], "sentence": s, "support": support,
                            "passage": " ".join(wins[bi].split()[:40]) if wins else "",
                            "numbers": miss_num, "fractions": miss_frac, "ranks": miss_rank, "roles": miss_role})
    trusted = num_total >= NUM_MIN and num_found / num_total >= NUM_RELIABLE
    if not trusted:                      # scrambled raw digits: number misses are noise, drop them
        for f in out:
            f["numbers"] = []
        out = [f for f in out if f["support"] < SUPPORT_MIN or f["fractions"] or f["ranks"] or f["roles"]]
    n_sent = sum(len([s for s in sentences(c["text"]) if len(_words(s)) >= MIN_WORDS]) for c in ca.block_clauses(doc))
    return {"doc_id": doc.get("document_id"), "trusted_digits": trusted,
            "numbers_local": f"{num_found}/{num_total}", "sentences": n_sent,
            "low_support": sum(1 for f in out if f["support"] < SUPPORT_MIN),
            "entity_misses": sum(1 for f in out if f["numbers"] or f["fractions"] or f["ranks"] or f["roles"]),
            "flags": out}


def audit_corpus() -> dict[str, dict]:
    docs = [d for d in ca.load_corpus() if ca.block_clauses(d)]
    df, n = build_df(ca.load_corpus())
    return {d["document_id"]: audit_doc(d, df, n) for d in docs}


def ratchet_view(res: dict[str, dict]) -> dict[str, dict]:
    return {k: {"low_support": v["low_support"], "entity_misses": v["entity_misses"]} for k, v in sorted(res.items())}


def regressions(now: dict, base: dict) -> list[str]:
    bad = []
    for k, v in now.items():
        b = base.get(k)
        if b is None:
            if v["low_support"] or v["entity_misses"]:
                bad.append(f"{k}: new block with {v['low_support']} unsupported sentence(s), {v['entity_misses']} entity miss(es)")
            continue
        for m in ("low_support", "entity_misses"):
            if v[m] > b[m]:
                bad.append(f"{k}: {m} {b[m]} -> {v[m]}")
    return bad


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--doc")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--write-baseline", action="store_true")
    a = ap.parse_args(argv)
    if a.doc:
        docs = ca.load_corpus()
        df, n = build_df(docs)
        d = next(x for x in docs if x["document_id"] == a.doc)
        print(json.dumps(audit_doc(d, df, n), ensure_ascii=False, indent=1))
        return 0
    res = audit_corpus()
    if a.report:
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "support_audit.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    if a.write_baseline:
        BASELINE.write_text(json.dumps(ratchet_view(res), ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    t = [v for v in res.values() if v["trusted_digits"]]
    print(f"[support] {len(res)} blocks, {sum(v['sentences'] for v in res.values())} sentences | "
          f"low support {sum(v['low_support'] for v in res.values())} | entity misses on trusted digits "
          f"{sum(v['entity_misses'] for v in t)} ({len(t)} blocks), on untrusted {sum(v['entity_misses'] for v in res.values() if not v['trusted_digits'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
