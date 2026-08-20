"""Full digest of an order: every operative rule, not a five-clause summary.

Why this exists (2026-08-19). Two adjudication rounds — 29 zeros of the yellow
sets and 17 zeros of the red-band "typical" set — split the fixable failures
evenly between "the answering order never entered the window" and "the order
was served but its curated block does not contain the answering clause". The
block is 4-7 clauses out of orders that run 1,000-28,000 words, and since
RETRIEVE_CURATED_ONLY the block IS what the model knows about the order. The
question-guided deepening (`night.deepen`) closes that gap one question at a
time — it moved the held-out set 15->29/47 and the typical set not at all.
This closes it per ORDER: read the whole text in windows and extract every
rule a soldier or commander could ask about.

Same faithfulness gates as curation (`night.curate.check`), same digit rule
(an order whose digits did not survive extraction gets its digest under the
no-digits rule, in a `key-facts-nodigits` section), same additive contract:
existing clauses are never rewritten, duplicates are dropped by stem overlap.

    python -m night.digest                       # dry run: plan + cost for hub30
    python -m night.digest --apply               # paid, through the ledger
    python -m night.digest --apply --ids 61.0104 PM-33.0302
    python -m night.digest --targets night/out/hub30.json --apply
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
from night.curate import (CITE_RULE, DIGIT_FREE_NOTE, NO_DIGITS_RULE, SCHEMA,
                          RETRY_SUFFIX, _norm as _stems, check, is_numbered)
from night.ledger import BudgetExceeded, Ledger, cost_usd
from night.rehearse import doc_path

MODEL = "claude-opus-4-8"
WINDOW_WORDS = 2500
OVERLAP_WORDS = 250
MAX_NEW_PER_WINDOW = 8
DUP_JACCARD = 0.6          # stem overlap above which a candidate repeats an existing clause
TARGETS_FILE = C.OUT / "hub30.json"
ACCEPTED = C.OUT / "digest_accepted.jsonl"
REJECTED = C.OUT / "digest_rejected.jsonl"

PROMPT = """לפניך קטע מתוך פקודת מטכ"ל, ורשימת הסעיפים שכבר נכתבו לה בסעיף „עיקרי הפקודה".
המטרה: שלכל כלל אופרטיבי בפקודה — זכות, חובה, סמכות, תנאי, הליך, מועד, איסור — יהיה
סעיף שחייל או מפקד שמחפש אותו ימצא.

כותרת הפקודה: {title}
קטע {part} מתוך {parts}.

הסעיפים שכבר קיימים (אל תחזור עליהם):
{existing}

הקטע מהטקסט הגולמי:
{raw}

כתוב **סעיפים חדשים בלבד** מתוך הקטע הזה, לכל כלל אופרטיבי שעדיין אינו מכוסה.

כללים מחייבים:
1. **רק מה שכתוב בקטע.** אל תשלים מידע-עולם, אל תפענח ראשי-תיבות שהפקודה לא מפענחת,
   ואל „תתקן" מספרים — העתק כפי שכתוב. עדיף סעיף אחד מדויק משלושה שאחד מהם מומצא.
2. {cite_rule}
3. דלג על הגדרות בלבד, על קולופון/היסטוריית-עדכונים, ועל מה שכבר מכוסה ברשימה למעלה.
4. `number` הוא תווית בשפת המשתמש — איך חייל או מפקד היה שואל את זה בפועל
   (תרחיש או פעולה: „מותר לי…", „מי מאשר…", „מה קורה אם…", „תוך כמה זמן…").
5. ‏40–120 מילים לסעיף, עד {max_new} סעיפים לקטע. אם הקטע לא מכיל כלל חדש — החזר רשימה ריקה.
6. `title` — החזר „{section_title}" כלשונה."""


def _kf_sections(doc: dict) -> list[dict]:
    return [s for s in doc.get("sections") or [] if "key-facts" in (s.get("id") or "")]


def _windows(raw: str) -> list[str]:
    words = raw.split()
    if len(words) <= WINDOW_WORDS:
        return [" ".join(words)]
    out, start = [], 0
    while start < len(words):
        out.append(" ".join(words[start:start + WINDOW_WORDS]))
        if start + WINDOW_WORDS >= len(words):
            break
        start += WINDOW_WORDS - OVERLAP_WORDS
    return out


def _jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def _dedupe(new: list[dict], existing_stems: list[set[str]]) -> tuple[list[dict], int]:
    kept, dropped = [], 0
    for cl in new:
        st = _stems(f"{cl.get('number', '')} {cl.get('text', '')}")
        if any(_jaccard(st, e) >= DUP_JACCARD for e in existing_stems):
            dropped += 1
            continue
        kept.append(cl)
        existing_stems.append(st)
    return kept, dropped


def load_targets(path) -> list[str]:
    data = json.loads(open(path, encoding="utf-8").read())
    if isinstance(data, dict):
        return list(data)
    return [d["doc_id"] if isinstance(d, dict) else str(d) for d in data]


def plan(doc: dict) -> dict | None:
    secs = _kf_sections(doc)
    if not secs:
        return None
    raw = str(doc.get("raw_text", ""))
    wins = _windows(raw)
    digit_free = not digits.trustworthy(doc)
    est = sum((len(w.split()) * 1.6 * 5 + 1500 * 25) / 1_000_000 for w in wins)
    return {"doc": doc, "windows": wins, "digit_free": digit_free, "est": est,
            "existing": [c for s in secs for c in s.get("clauses", [])]}


def _call(prompt: str, ledger: Ledger, label: str, est: float):
    rid = ledger.reserve(label, est)
    try:
        r = backend.client.messages.create(
            model=MODEL, max_tokens=16000,
            thinking={"type": "adaptive"},
            output_config={"effort": "high",
                           "format": {"type": "json_schema", "schema": SCHEMA}},
            messages=[{"role": "user", "content": prompt}])
    except Exception as e:
        ledger.settle(rid, 0.0)
        return None, 0.0, f"api error: {type(e).__name__}: {str(e)[:120]}"
    usd = cost_usd(MODEL, input_tokens=r.usage.input_tokens, output_tokens=r.usage.output_tokens)
    ledger.settle(rid, usd)
    if r.stop_reason == "max_tokens":
        return None, usd, "hit max_tokens"
    try:
        parsed = json.loads("".join(b.text for b in r.content if b.type == "text"))
    except json.JSONDecodeError as e:
        return None, usd, f"bad JSON: {e}"
    return parsed, usd, ""


def digest_one(doc: dict, ledger: Ledger, apply: bool) -> dict:
    from storage.vector_store import index_document
    p = plan(doc)
    did = doc["document_id"]
    if p is None:
        C.log(f"[digest] {did}: no key-facts block — skipped (curate first)")
        return {"doc_id": did, "added": 0}
    raw = str(doc.get("raw_text", ""))
    secs = _kf_sections(doc)
    section_title = secs[0].get("title", f"עיקרי הפקודה — {doc.get('title', '')}")
    if p["digit_free"]:
        section_title = re.sub(r"\s*\[.*?\]$", "", section_title)
    C.log(f"[digest] {did}: {len(p['windows'])} windows, {len(raw.split())} words, "
          f"{len(p['existing'])} existing clauses, ~${p['est']:.2f}"
          + ("  [digit-free]" if p["digit_free"] else ""))
    if not apply:
        return {"doc_id": did, "added": 0, "est": p["est"]}

    existing_stems = [_stems(f"{c.get('number', '')} {c.get('text', '')}") for c in p["existing"]]
    existing_labels = [str(c.get("number", "")) for c in p["existing"]]
    added_all: list[dict] = []
    spent = 0.0
    rule = NO_DIGITS_RULE if p["digit_free"] else CITE_RULE[is_numbered(raw)]
    for i, win in enumerate(p["windows"], 1):
        labels = existing_labels + [str(c.get("number", "")) for c in added_all]
        prompt = PROMPT.format(
            title=doc.get("title", ""), part=i, parts=len(p["windows"]),
            existing="\n".join(f"- {l}" for l in labels) or "- (אין)",
            raw=win, cite_rule=rule, max_new=MAX_NEW_PER_WINDOW, section_title=section_title)
        est = (len(win.split()) * 1.6 * 5 + 1500 * 25) / 1_000_000
        try:
            parsed, usd, err = _call(prompt, ledger, f"digest:{did}#{i}", est)
        except BudgetExceeded as e:
            C.log(f"[digest] STOPPING — {e}")
            break
        spent += usd
        if parsed is None:
            C.log(f"[digest] {did}#{i}: {err} ${usd:.3f}")
            continue
        new = parsed.get("clauses") or []
        if not new:
            continue
        problems, warnings = check({"clauses": new}, raw, digit_free=p["digit_free"])
        if problems:
            # one retry with the problems fed back, as curation does
            parsed2, usd2, err2 = _call(
                prompt + RETRY_SUFFIX.format(problems="\n".join(f"- {x}" for x in problems)),
                ledger, f"digest:{did}#{i}r", est)
            spent += usd2
            new2 = (parsed2 or {}).get("clauses") or []
            problems2, warnings = check({"clauses": new2}, raw, digit_free=p["digit_free"]) if new2 else (["empty retry"], [])
            if problems2:
                C.log(f"[digest] {did}#{i}: REJECTED after retry — {problems2[0][:100]}")
                C.append_jsonl(REJECTED, {"doc_id": did, "window": i, "problems": problems2, "clauses": new2, "usd": usd + usd2})
                continue
            new = new2
        kept, dropped = _dedupe(new, existing_stems)
        added_all.extend(kept)
        C.log(f"[digest] {did}#{i}: +{len(kept)} clauses ({dropped} dup) ${usd:.3f}")

    if not added_all:
        C.log(f"[digest] {did}: nothing added, ${spent:.2f}")
        return {"doc_id": did, "added": 0, "usd": spent}

    path = doc_path(did)
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    if p["digit_free"]:
        target = next((s for s in on_disk.get("sections") or []
                       if (s.get("id") or "") == "key-facts-nodigits"), None)
        if target is None:
            target = {"id": "key-facts-nodigits", "digit_free": True,
                      "title": f"{section_title} [{DIGIT_FREE_NOTE}]", "clauses": []}
            on_disk.setdefault("sections", []).append(target)
    else:
        target = next(s for s in on_disk.get("sections") or [] if "key-facts" in (s.get("id") or ""))
    target["clauses"] = list(target.get("clauses", [])) + added_all
    path.write_text(json.dumps(on_disk, ensure_ascii=False, indent=2), encoding="utf-8")
    n = index_document(json.loads(path.read_text(encoding="utf-8")))
    C.append_jsonl(ACCEPTED, {"doc_id": did, "added": added_all, "digit_free": p["digit_free"],
                              "windows": len(p["windows"]), "usd": spent})
    C.log(f"[digest] {did}: +{len(added_all)} clauses total, {n} chunks, ${spent:.2f} "
          f"| spent ${ledger.spent:.2f}")
    return {"doc_id": did, "added": len(added_all), "usd": spent}


def run(ids: list[str], apply: bool) -> None:
    ledger = Ledger(C.LEDGER)
    docs = {d["document_id"]: d for d in backend.load_documents() if d.get("document_id")}
    total_est, results = 0.0, []
    for did in ids:
        doc = docs.get(did)
        if not doc:
            C.log(f"[digest] {did}: not in corpus — skipped")
            continue
        p = plan(doc)
        if p:
            total_est += p["est"]
    C.log(f"[digest] {len(ids)} orders, ~${total_est:.2f} estimated"
          + ("" if apply else " — dry run, nothing spent; --apply to execute"))
    if not apply:
        for did in ids:
            if did in docs:
                digest_one(docs[did], ledger, apply=False)
        return
    for did in ids:
        if did in docs:
            results.append(digest_one(docs[did], ledger, apply=True))
    C.log(f"[digest] done: {sum(r['added'] for r in results)} clauses over "
          f"{sum(1 for r in results if r['added'])} orders, ${sum(r.get('usd', 0) for r in results):.2f}")


if __name__ == "__main__":
    args = sys.argv[1:]
    apply = "--apply" in args
    if "--ids" in args:
        i = args.index("--ids")
        ids = [a for a in args[i + 1:] if not a.startswith("--")]
    else:
        path = TARGETS_FILE
        if "--targets" in args:
            path = args[args.index("--targets") + 1]
        ids = load_targets(path)
    run(ids, apply)
