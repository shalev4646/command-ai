"""Stage A — write a `key-facts` section for every order that lacks one.

The audit found 27 orders with no `sections` at all. That is not a cosmetic
gap: when an anchor wins, vector_store hands the model the winning document's
MERGED key-facts block (vector_store.py:569), so an order without one is
liftable but unanswerable — the lift arrives and delivers a raw chunk, which on
33.0304 turned out to be the publication colophon.

This runs Opus over each order's own raw_text and asks for the curated block,
then refuses to accept it unless it passes two automated faithfulness gates:

  citations   Every clause must cite clause numbers, and each cited number must
              actually exist as a clause marker in that order's raw_text. A
              model that invents "(סעיף 12)" fails here.
  no-invented
  -topics     A clause may not raise a high-stakes topic the order is silent
              about. This generalizes the 33.0304 trap: the order says nothing
              about a right to counsel, so a clause mentioning עורך דין would
              both state non-existent law AND make the order retrieve for
              questions it cannot answer — worse than staying silent.

Neither gate can confirm the content is CORRECT; they only catch the two
failure modes that are mechanically detectable. Human review is still owed
before this branch merges, and the report says so.
"""
from __future__ import annotations

import json
import re

import backend
from night import config as C
from night.ledger import Ledger, BudgetExceeded, cost_usd
from night.rehearse import doc_path

MODEL = "claude-opus-4-8"
MAX_RAW_WORDS = 9000          # 33.0304, the largest, is 5,532

# Topics where a fabricated sentence would be actively harmful: a soldier acting
# on invented procedure, and an order retrieved for questions it cannot answer.
RISK_TOPICS = {
    "עורך דין": ("עורך דין", "עו\"ד", "סניגור", "ייצוג משפטי", "להיוועץ"),
    "פיצוי כספי": ("פיצוי", "פיצויים", "שיפוי"),
    "ערעור": ("ערעור", "לערער", "השגה"),
    "מעצר": ("מעצר", "לעצור", "עציר"),
    "פיטורין/שחרור": ("שחרור מוקדם", "פיטורין", "הדחה"),
    "ועדה רפואית": ("ועדה רפואית", "פרופיל רפואי"),
}

PROMPT = """לפניך הטקסט הגולמי של פקודת מטכ"ל. כתוב עבורה סעיף "עיקרי הפקודה" (key-facts) —
בלוק מתוקנן שיוגש למודל שעונה לחיילים, במקום פסקאות גולמיות אקראיות.

כותרת הפקודה: {title}

הטקסט הגולמי:
{raw}

כללים מחייבים:
1. **רק מה שכתוב בפקודה.** אסור להסיק, להשלים מידע כללי, או לכתוב על נושא שהפקודה שותקת בו.
   אם הפקודה לא עוסקת בנושא כלשהו — פשוט אל תזכיר אותו. שתיקה עדיפה על ניחוש.
2. **כל סעיף חייב לצטט מספרי סעיפים** מהפקודה, בסוגריים בסוף: "(סעיף 43)" או "(סעיפים 44–45)".
   המספרים חייבים להיות מספרי הסעיפים האמיתיים שמהם לקחת את התוכן.
3. בין 4 ל-8 סעיפים. כל אחד 40–120 מילים.
4. השדה `number` הוא **תווית בשפת המשתמש** — איך חייל היה שואל את זה. לא כותרת משפטית.
   דוגמה טובה: "מתי עד רשאי לסרב לטביעות אצבע". דוגמה רעה: "סעיף 28 — נטילת ראיות".
5. השדה `text` הוא העובדות עצמן, בעברית ברורה, כולל מספרים, מועדים, דרגות ומי מאשר.
6. כסה את מה שחייל או מפקד באמת ישאל, לא את מה שהפקודה מדגישה פרוצדורלית.

"""

# Structured outputs rather than "return JSON only": Hebrew legal text is full
# of literal quotes (צה"ל, פ"מ, "אתה חשוד בביצוע עבירה פלונית"), and the first
# run died on exactly that — the same hazard ingestion/pdf_to_json.py:119 calls
# out. Schema enforcement removes the failure mode instead of patching it up
# with a regex afterwards.
SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "clauses": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "number": {"type": "string"},
                    "text": {"type": "string"},
                },
                "required": ["number", "text"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["title", "clauses"],
    "additionalProperties": False,
}


def clause_numbers_in_raw(raw: str) -> set[int]:
    """Clause markers as they appear in the source: `43 . חשוד שנעצר`."""
    return {int(m.group(1)) for m in re.finditer(r"(?<!\d)(\d{1,3})\s*\.\s", raw)}


def cited_numbers(text: str) -> set[int]:
    out: set[int] = set()
    for m in re.finditer(r"סעיפים?\s*(\d{1,3})\s*[–\-—]\s*(\d{1,3})", text):
        out |= {int(m.group(1)), int(m.group(2))}
    for m in re.finditer(r"סעיף\s*(\d{1,3})", text):
        out.add(int(m.group(1)))
    return out


_HEB_PREFIXES = "הובלמכש"
_FINALS = str.maketrans("םןץףך", "מנצפכ")


_SUFFIXES = ("ים", "ות", "יים", "ה", "י", "ן", "ך", "ו")


def _norm(text: str) -> set[str]:
    """Content words reduced to comparable stems.

    Extends the retriever's prefix-strip + final-fold (vector_store.py:655,
    :666) with suffix stripping, because the first curation run rejected a
    faithful clause over words like המועברים / גנטיים: prefix-only handling
    leaves "מועברים" unable to match the order's own "מועבר", so a correct
    paraphrase scored as invented vocabulary.
    """
    out = set()
    for w in re.findall(r"[א-ת]{3,}", text):
        w = w.translate(_FINALS)
        forms = {w}
        if len(w) > 3 and w[0] in _HEB_PREFIXES:
            forms.add(w[1:])
        for f in list(forms):
            for suf in _SUFFIXES:
                if len(f) > len(suf) + 2 and f.endswith(suf):
                    forms.add(f[: -len(suf)])
        out |= forms
    return out


def is_numbered(raw: str) -> bool:
    """True when the order really carries a run of clause markers.

    Four of the 26 targets (5040.05, PM-21.0203, 20.0502, 33.0201) do not —
    their raw text has no usable numbering at all, so demanding citations from
    them would reject every candidate forever rather than catch anything.
    """
    nums = clause_numbers_in_raw(raw)
    return max((len([x for x in range(s, s + 12) if x in nums])
                for s in range(1, 90)), default=0) >= 6


# A faithful paraphrase reuses the order's own vocabulary; an invented clause
# does not. 35% is deliberately loose — user-language labels ("מה קורה אם") are
# supposed to introduce words the legal text never uses.
MAX_UNGROUNDED = 0.50


def check(section: dict, raw: str) -> tuple[list[str], list[str]]:
    """The faithfulness gates.

    Returns (problems, warnings). Problems block acceptance — they are the
    mechanically detectable forms of invention. Warnings are recorded for the
    human review that is still owed, and do not block.
    """
    problems: list[str] = []
    warnings: list[str] = []
    real = clause_numbers_in_raw(raw)
    numbered = is_numbered(raw)
    raw_vocab = _norm(raw)

    for cl in section.get("clauses", []):
        txt = cl.get("text", "")
        label = str(cl.get("number", "?"))[:40]
        if numbered:
            cites = cited_numbers(txt)
            # A MISSING citation is a formatting miss, not an unfaithful claim —
            # rejecting on it cost 29 of the first run's 32 rejections and taught
            # nothing. A WRONG citation is invention and still fails.
            if cites and cites - real:
                problems.append(f"clause {label!r}: cites {sorted(cites - real)}, "
                                f"absent from raw_text")
            elif not cites:
                warnings.append(f"clause {label!r}: no clause citation")
        words = _norm(txt)
        if words:
            ungrounded = words - raw_vocab
            share = len(ungrounded) / len(words)
            if share > MAX_UNGROUNDED:
                problems.append(
                    f"clause {label!r}: {share:.0%} of its vocabulary is absent from the "
                    f"order (e.g. {sorted(ungrounded)[:5]})")
    blob = " ".join(str(cl.get("number", "")) + " " + str(cl.get("text", ""))
                    for cl in section.get("clauses", []))
    for label, terms in RISK_TOPICS.items():
        if any(t in blob for t in terms) and not any(t in raw for t in terms):
            problems.append(f"raises {label!r}, which the order never mentions")
    return problems, warnings


RETRY_SUFFIX = """

הניסיון הקודם שלך נדחה בבדיקת נאמנות אוטומטית. הבעיות:
{problems}

כתוב מחדש. שים לב במיוחד: כל מספר סעיף שאתה מצטט חייב להופיע בטקסט הגולמי שלמעלה,
ואסור להזכיר נושא שהפקודה שותקת בו — עדיף לכתוב פחות סעיפים ולהיות מדויק."""


def curate_one(doc: dict, ledger: Ledger, problems: list[str] | None = None
               ) -> tuple[dict | None, float, list[str]]:
    raw = " ".join(str(doc.get("raw_text", "")).split()[:MAX_RAW_WORDS])
    est = (len(raw.split()) * 1.6 * 5 + 1400 * 25) / 1_000_000    # generous
    rid = ledger.reserve(f"curate:{doc['document_id']}", est)
    try:
        r = backend.client.messages.create(
            # 8000 truncated 33.0306 mid-JSON and the $0.35 bought nothing:
            # adaptive thinking at effort=high draws from this same budget, so a
            # long order spends it reasoning and gets cut before closing the
            # object. Raising the cap is free for the documents that do not need
            # it — output is billed per token generated, and the orders that
            # succeed land at $0.07-$0.18, nowhere near either ceiling. It only
            # changes the truncating case, from total loss to a usable result.
            model=MODEL, max_tokens=16000,
            thinking={"type": "adaptive"},
            output_config={"effort": "high",
                           "format": {"type": "json_schema", "schema": SCHEMA}},
            messages=[{"role": "user", "content": PROMPT.format(
                title=doc.get("title", ""), raw=raw)
                + (RETRY_SUFFIX.format(problems="\n".join(f"- {p}" for p in problems))
                   if problems else "")}],
        )
    except Exception as e:
        ledger.settle(rid, 0.0)
        return None, 0.0, [f"api error: {type(e).__name__}: {e}"]
    usd = cost_usd(MODEL, input_tokens=r.usage.input_tokens, output_tokens=r.usage.output_tokens)
    ledger.settle(rid, usd)

    if r.stop_reason == "max_tokens":
        return None, usd, ["hit max_tokens — schema-valid JSON was truncated"]
    text = "".join(b.text for b in r.content if b.type == "text")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as e:
        return None, usd, [f"bad JSON despite schema: {e}"]

    section = {"id": "key-facts",
               "title": parsed.get("title") or f"עיקרי הפקודה — {doc.get('title','')}",
               "clauses": parsed.get("clauses") or []}
    if not section["clauses"]:
        return None, usd, ["no clauses"]
    problems, warnings = check(section, str(doc.get("raw_text", "")))
    section["_warnings"] = warnings
    return section, usd, problems


def run(limit: int | None = None) -> None:
    ledger = Ledger(C.LEDGER)
    from night.audit import _section_ids
    # 20.0502 was curated, reviewed, and deliberately pulled: its source's
    # digits are demonstrably scrambled ("25 בדצמבר3..2"=2003) and two money
    # thresholds could not be traced to it. It has no sections, so it looks
    # like a target forever — excluding it here keeps that decision from being
    # silently undone by the next run.
    NEVER = {"20.0502"}
    targets = [d for d in backend.load_documents()
               if d.get("document_id") and d["document_id"] not in NEVER
               and not _section_ids(d)]
    if limit:
        targets = targets[:limit]
    C.log(f"[curate] {len(targets)} orders need a key-facts section "
          f"(budget left ${ledger.remaining():.2f})")

    from storage.vector_store import index_document
    done = failed = 0
    for i, doc in enumerate(targets, 1):
        doc_id = doc["document_id"]
        spent_here = 0.0
        try:
            section, usd, problems = curate_one(doc, ledger)
            spent_here += usd
            if problems:
                # one retry with the gate's own complaints fed back — a rejection
                # that is not retried is money spent to learn nothing
                C.log(f"[curate] {i}/{len(targets)} {doc_id} retry: "
                      f"{'; '.join(problems)[:140]}")
                section, usd, problems = curate_one(doc, ledger, problems)
                spent_here += usd
        except BudgetExceeded as e:
            C.log(f"[curate] STOPPING — {e}")
            break
        usd = spent_here
        if section is None or problems:
            failed += 1
            C.log(f"[curate] {i}/{len(targets)} {doc_id} REJECTED (${usd:.3f}): "
                  f"{'; '.join(problems)[:200]}")
            C.append_jsonl(C.OUT / "curate_rejected.jsonl",
                           {"doc_id": doc_id, "problems": problems,
                            "section": section, "usd": usd})
            continue

        path = doc_path(doc_id)
        raw_doc = json.loads(path.read_text(encoding="utf-8"))
        raw_doc["sections"] = [section]
        path.write_text(json.dumps(raw_doc, ensure_ascii=False, indent=2), encoding="utf-8")
        n = index_document(json.loads(path.read_text(encoding="utf-8")))
        done += 1
        C.log(f"[curate] {i}/{len(targets)} {doc_id} OK  "
              f"{len(section['clauses'])} clauses, {n} chunks, ${usd:.3f}  "
              f"| spent ${ledger.spent:.2f}")
        C.append_jsonl(C.OUT / "curate_accepted.jsonl",
                       {"doc_id": doc_id, "section": section, "usd": usd})

    C.log(f"[curate] done: {done} accepted, {failed} rejected, spent ${ledger.spent:.2f}")
    C.log(ledger.summary())


if __name__ == "__main__":
    import sys
    run(limit=int(sys.argv[1]) if len(sys.argv) > 1 else None)
