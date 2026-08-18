"""Deepen an order's key-facts block where soldiers' questions reach the order
and the block does not answer them.

Measured on 2026-08-16 across five paired runs of the frozen 30 questions: 16
score zero in every run. Of those, 11 were adjudicated PRESENT_NOT_RETRIEVED,
and after the wave-2 fixes the named order IS retrieved in 8 of them — and the
answer is still zero. Retrieval reaches the right order; the chunk it serves
does not contain the answering clause. Six of the eight land on PM-33.0302
(דין משמעתי, 27,681 words), whose 21 curated clauses cover lawyer
representation, the punishment tables and summons, and nothing about
annulment (§241), the offence annex (§122-124), wrongful punishment (§303-313),
doctors (§36) or "the trial is one tool among several" (§2).

This differs from `night.extend` in two ways that matter:

  question-driven, not anchor-driven
      extend closed the gap between anchors and blocks and was measured at
      3 improvements against 11 regressions — optimising a proxy. Here the
      target is a question a soldier actually asked, and the evidence is a
      verbatim quote an adjudicator located in the raw text.

  windowed, not truncated
      extend hands the model the first 9,000 words. The clauses that answer
      these questions sit at characters 117,000-143,000 of a 170,000-character
      order — the "answering section 70% into the document" mechanism. Each
      target quote is located in raw_text and the model sees windows around the
      hits, so it is reading the part of the order that answers.

Same schema and the same faithfulness gates as curation (`night.curate.check`),
plus the digits gate: an order whose digits did not survive extraction is
skipped, exactly as curation skips it. Additive only — existing clauses are
never rewritten.

Honesty note on measurement: these targets come from the frozen 30, so a
re-measure on the frozen 30 is a tuning-set score. The held-out 24
(REMEASURE_SET=heldout) is the number that says whether deepening generalises.

    python -m night.deepen            # dry run: targets, windows, cost
    python -m night.deepen --apply    # paid run through the ledger
    python -m night.deepen --targets night/out/deepen_targets.json [--apply]
                                      # targets from an adjudication file:
                                      # {doc_id: [[question, [verbatim quotes]], ...]}

Digit-free orders (`night.curate --digit-free`, section id `key-facts-nodigits`)
are deepened under the same no-digits rule and gate as their curation, so the
added clauses say what/who/how without a single number. An order whose digits
are broken and that has NO digit-free block is still skipped.
"""
from __future__ import annotations

import json
import re
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import backend
from night import config as C
from night import digits
from night.curate import CITE_RULE, NO_DIGITS_RULE, SCHEMA, check, is_numbered
from night.ledger import BudgetExceeded, Ledger, cost_usd
from night.rehearse import doc_path

MODEL = "claude-opus-4-8"
WINDOW_WORDS = 1200        # each side of a located quote
MAX_WINDOW_WORDS = 9000    # total per order, matches curation's truncation
ACCEPTED = C.OUT / "deepen_accepted.jsonl"
REJECTED = C.OUT / "deepen_rejected.jsonl"
DEFAULT_TARGETS_FILE = C.OUT / "deepen_targets.json"

# --- targets -----------------------------------------------------------------
# doc_id -> list of (question, [verbatim quotes from the adjudication evidence]).
# The quotes are what the adjudicators located in raw_text; deepen.py locates
# them again and reads around them. A quote that is not found is logged and
# dropped, never guessed at.
TARGETS: dict[str, list[tuple[str, list[str]]]] = {
    "PM-33.0302": [
        ("אם חייל קיבל ענישה כי הוא לא היה מודע לפקודה שיצאה ביום הקודם, אני יכול לבטל את זה?",
         ["הוטל עונש בדין משמעתי, הסמכות לבטלו או להקל בו"]),
        ("חייל שלי טוען שהוא לא קיבל הנחיה ברורה לפני שעשה את הדבר. מה זה אומר בחוק לעניין הענישה?",
         ["חייל שבמזיד לא קיים פקודה שניתנה לו מאת מפקד",
          "חייל שקיים ברשלנות פקודה או הוראה שניתנה לו"]),
        ("חייל אמר לי שהוא חוקי — אם אני עדיין נותן לו ענישה ויוצא שהוא באמת היה חוקי, מה קורה לי?",
         ["יבטלו פסק שניתן בדין משמעתי, אם נוכחו"]),
        ("מי אני פונה אם יש לי בעיה עם הרופא של הגדוד?",
         ["רופא שהוגשה נגדו תלונה בגין עבירה שביסודה רשלנות מקצועית"]),
        ("חייל שלי נכנס לשמירה בשעה לא נכונה כמה פעמים החודש. צריך לתת לו ענישה או יש משהו קודם?",
         ["הדין המשמעתי הוא אחד מן האמצעים הפיקודיים"]),
        ("מה יכול להיות כתוצאה מכך שחייל לא ביצע פקודה בדיוק כמו שצוויתי?",
         ["חייל שלא קיים פקודה שניתנה לו"]),
    ],
    "33.0201": [
        ("כמה זמן צריך לתת לחייל על בקשת השעות המוקדשות שלו אם הוא נשוי וגר בחוץ?",
         ["מוסמך לקצר את שעות הפעילות של חייל פלוני בשעה אחת"]),
    ],
    "33.0351": [
        ("חייל שלי עשה טעות והודה לי מיד. צריך בכל זאת לתת ענישה, או שיש דרך אחרת?",
         []),   # 370 words — the whole order is the window
    ],
    "61.0104": [
        ("מי אני פונה אם יש לי בעיה עם הרופא של הגדוד? חד\"ש או מישהו אחר?",
         []),
    ],
}

PROMPT = """לפניך קטעים מתוך פקודת מטכ"ל, סעיף „עיקרי הפקודה" שכבר נכתב לה, ושאלות
שחיילים ומפקדים שאלו — שהפקודה עונה עליהן, אבל הסעיף הקיים לא נוגע בהן.

כותרת: {title}

הסעיף הקיים:
{existing}

השאלות שאינן מכוסות:
{questions}

כתוב **סעיפים נוספים בלבד** שיענו על השאלות האלה, מתוך הקטעים שלהלן.

קטעים מהטקסט הגולמי:
{raw}

כללים מחייבים:
1. **רק מה שכתוב בפקודה.** אם הפקודה בעצם לא עונה על שאלה מסוימת — אל תכתוב עליה כלום.
   עדיף להחזיר סעיף אחד מדויק מאשר שלושה שאחד מהם מומצא.
2. {cite_rule}
3. **אל תחזור** על מה שכבר מכוסה בסעיף הקיים.
4. אסור לפענח ראשי-תיבות שהפקודה לא מפענחת, ואסור „לתקן" מספרים — העתק כפי שכתוב.
5. `number` הוא תווית בשפת המשתמש — איך חייל היה שואל את זה.
6. ‏40–120 מילים לסעיף. עד {max_new} סעיפים.
7. `title` — החזר את כותרת הסעיף הקיים כלשונה."""


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s)


def windows(raw: str, quotes: list[str]) -> tuple[str, list[str]]:
    """Text around each located quote, merged and capped. Short orders pass
    whole. Returns (text, quotes_not_found)."""
    words = raw.split()
    if len(words) <= MAX_WINDOW_WORDS:
        return " ".join(words), []
    flat = _norm(raw)
    # word index of each character offset, built once
    starts, pos = [], 0
    for w in words:
        pos = flat.find(w, pos)
        starts.append(pos)
        pos += len(w)
    spans, missing = [], []
    for q in quotes:
        key = _norm(q)[:60]
        at = flat.find(key)
        if at < 0:
            missing.append(q)
            continue
        # nearest word index at/after `at`
        lo, hi = 0, len(starts) - 1
        while lo < hi:
            mid = (lo + hi) // 2
            if starts[mid] < at:
                lo = mid + 1
            else:
                hi = mid
        spans.append((max(0, lo - WINDOW_WORDS), min(len(words), lo + WINDOW_WORDS)))
    if not spans:
        return "", missing
    spans.sort()
    merged = [list(spans[0])]
    for a, b in spans[1:]:
        if a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    out, total = [], 0
    for a, b in merged:
        seg = words[a:b]
        if total + len(seg) > MAX_WINDOW_WORDS:
            seg = seg[:MAX_WINDOW_WORDS - total]
        out.append(" ".join(seg))
        total += len(seg)
        if total >= MAX_WINDOW_WORDS:
            break
    return "\n\n[…]\n\n".join(out), missing


def _kf_section(doc: dict) -> dict | None:
    for s in doc.get("sections") or []:
        if (s.get("id") or "") == "key-facts":
            return s
    for s in doc.get("sections") or []:
        if "key-facts" in (s.get("id") or ""):
            return s
    return None


def _is_digit_free(sec: dict) -> bool:
    return bool(sec.get("digit_free")) or (sec.get("id") or "") == "key-facts-nodigits"


def load_targets(path) -> dict[str, list[tuple[str, list[str]]]]:
    """Targets from an adjudication file: {doc_id: [[question, [quotes]], ...]}.
    Same shape as TARGETS; a quote list may be empty for a short order."""
    data = json.loads(open(path, encoding="utf-8").read())
    out: dict[str, list[tuple[str, list[str]]]] = {}
    for doc_id, items in data.items():
        out[doc_id] = [(str(q), [str(x) for x in (qs or [])]) for q, qs in items]
    return out


def run(apply: bool = False, targets: dict | None = None) -> None:
    targets = TARGETS if targets is None else targets
    ledger = Ledger(C.LEDGER)
    docs = {d["document_id"]: d for d in backend.load_documents() if d.get("document_id")}
    from storage.vector_store import index_document

    plan = []
    for doc_id, items in targets.items():
        doc = docs.get(doc_id)
        if not doc:
            C.log(f"[deepen] {doc_id}: not in corpus — skipped")
            continue
        sec = _kf_section(doc)
        if sec is None:
            C.log(f"[deepen] {doc_id}: no key-facts block to deepen — skipped")
            continue
        digit_free = _is_digit_free(sec)
        if not digits.trustworthy(doc) and not digit_free:
            C.log(f"[deepen] {doc_id}: digits did not survive extraction and no "
                  f"digit-free block — skipped, same rule as curation")
            continue
        raw = str(doc.get("raw_text", ""))
        quotes = [q for _, qs in items for q in qs]
        text, missing = windows(raw, quotes)
        for m in missing:
            C.log(f"[deepen] {doc_id}: quote not located, dropped: {m[:50]!r}")
        if not text:
            C.log(f"[deepen] {doc_id}: no window — skipped")
            continue
        est = (len(text.split()) * 1.6 * 5 + 1500 * 25) / 1_000_000
        plan.append((doc_id, doc, sec, items, text, est, digit_free))
        C.log(f"[deepen] {doc_id}: {len(items)} questions, window {len(text.split())} words "
              f"of {len(raw.split())}, ~${est:.3f}" + ("  [digit-free]" if digit_free else ""))
    C.log(f"[deepen] {len(plan)} orders, ~${sum(p[5] for p in plan):.2f} total")
    if not apply:
        C.log("[deepen] dry run — nothing spent. Use --apply to execute.")
        return

    done = 0
    for doc_id, doc, sec, items, text, est, digit_free in plan:
        existing = "\n".join(f"- {c.get('number','')}: {c.get('text','')}"
                             for c in sec.get("clauses", []))
        raw = str(doc.get("raw_text", ""))
        prompt = PROMPT.format(
            title=doc.get("title", ""), existing=existing,
            questions="\n".join(f"- {q}" for q, _ in items), raw=text,
            cite_rule=NO_DIGITS_RULE if digit_free else CITE_RULE[is_numbered(raw)],
            max_new=len(items) + 1)
        try:
            rid = ledger.reserve(f"deepen:{doc_id}", est)
        except BudgetExceeded as e:
            C.log(f"[deepen] STOPPING — {e}")
            break
        try:
            r = backend.client.messages.create(
                model=MODEL, max_tokens=16000,
                thinking={"type": "adaptive"},
                output_config={"effort": "high",
                               "format": {"type": "json_schema", "schema": SCHEMA}},
                messages=[{"role": "user", "content": prompt}])
        except Exception as e:
            ledger.settle(rid, 0.0)
            C.log(f"[deepen] {doc_id} api error: {type(e).__name__}: {str(e)[:120]}")
            continue
        usd = cost_usd(MODEL, input_tokens=r.usage.input_tokens,
                       output_tokens=r.usage.output_tokens)
        ledger.settle(rid, usd)
        try:
            parsed = json.loads("".join(b.text for b in r.content if b.type == "text"))
        except json.JSONDecodeError:
            C.log(f"[deepen] {doc_id} bad JSON ${usd:.3f}")
            continue
        new = parsed.get("clauses") or []
        if not new:
            C.log(f"[deepen] {doc_id}: model added nothing ${usd:.3f}")
            continue
        problems, warnings = check({"clauses": new}, raw, digit_free=digit_free)
        if problems:
            C.log(f"[deepen] {doc_id} REJECTED: {problems[0][:120]} ${usd:.3f}")
            C.append_jsonl(REJECTED, {"doc_id": doc_id, "problems": problems,
                                      "clauses": new, "usd": usd})
            continue
        path = doc_path(doc_id)
        on_disk = json.loads(path.read_text(encoding="utf-8"))
        target = _kf_section(on_disk)
        target["clauses"] = list(target.get("clauses", [])) + new
        path.write_text(json.dumps(on_disk, ensure_ascii=False, indent=2), encoding="utf-8")
        n = index_document(json.loads(path.read_text(encoding="utf-8")))
        done += 1
        C.log(f"[deepen] {doc_id} +{len(new)} clauses, {n} chunks, ${usd:.3f} "
              f"| spent ${ledger.spent:.2f}" + (f"  ⚠ {len(warnings)} warnings" if warnings else ""))
        for cl in new:
            C.log(f"[deepen]    • {cl.get('number','')[:60]}")
        C.append_jsonl(ACCEPTED, {"doc_id": doc_id, "added": new,
                                  "questions": [q for q, _ in items],
                                  "warnings": warnings, "usd": usd,
                                  "digit_free": digit_free})
    C.log(f"[deepen] done: {done}/{len(plan)} orders deepened")


if __name__ == "__main__":
    targets = None
    if "--targets" in sys.argv:
        i = sys.argv.index("--targets")
        path = sys.argv[i + 1] if i + 1 < len(sys.argv) and not sys.argv[i + 1].startswith("--") \
            else DEFAULT_TARGETS_FILE
        targets = load_targets(path)
        C.log(f"[deepen] {sum(len(v) for v in targets.values())} questions over "
              f"{len(targets)} orders from {path}")
    run(apply="--apply" in sys.argv, targets=targets)
