"""Recover the digits of orders whose PDF text layer substitutes them.

`night/digits.py` finds the documents whose digits did not survive extraction.
This puts them back.

The corruption is not noise. Two independent dates in 32.0314 decode under one
and the same table — "התשע\"א (5 בספטמבר 9155)" is 2011 and "התשע\"ב
(5 בספטמבר 9159)" is 2012, both requiring 9->2, 1->0, 5->1 — and that table then
turns "במינוי59 חודש" into a minimum seniority of 12 months, which is the value
the order actually states. So each document carries a fixed permutation of the
ten digits, and recovering it recovers every number in the file at once.

Switching PDF libraries does not help: fitz and pdfplumber return byte-identical
digits, because the damage is in the file's own character map, not in the
reader. What does work is bypassing the text layer — the page renders correctly
as an image, since the glyphs themselves are right and only their codepoints
lie. So: pull the digit runs out of one page's text, render that page, and ask
what those runs actually say.

The mapping is then derived mechanically from the pairs and refuses to apply
unless it earns it:

  * every pair must preserve length — a substitution cannot change how many
    digits there are, so a length change means the model misread rather than
    decoded
  * no digit may map to two different values
  * it must be injective — two source digits collapsing onto one is a misread,
    not a permutation
  * and applying it must actually raise the share of plausible years, which is
    the same measurement that flagged the document in the first place

A mapping that fails any of these is discarded and the document is left alone.
Half-decoded numbers are worse than known-bad ones.

    python -m night.unscramble            # dry run: what would be attempted
    python -m night.unscramble --apply    # paid, through the ledger
    python -m night.unscramble --apply 5  # first 5 only
"""
from __future__ import annotations

import base64
import io
import json
import re
import sys
from collections import Counter

# reconfigure rather than re-wrap: a second TextIOWrapper over the same buffer
# closes the first when it is collected, which kills stdout for anything that
# imports two of these modules at once.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import backend
from night import config as C
from night.digits import YEAR_HI, YEAR_LO, is_broken, year_share
from night.ledger import BudgetExceeded, Ledger, cost_usd
from night.rehearse import doc_path

MODEL = "claude-haiku-4-5"
DPI = 150
MIN_PAIRS = 4           # too few to trust a ten-digit table
MIN_DIGITS_MAPPED = 4
ACCEPTED = C.OUT / "unscramble_accepted.jsonl"
REJECTED = C.OUT / "unscramble_rejected.jsonl"

RUN = re.compile(r"(?<!\d)(\d{2,4})(?!\d)")

SCHEMA = {
    "type": "object",
    "properties": {
        "readings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "extracted": {"type": "string"},
                    "actual": {"type": "string"},
                },
                "required": ["extracted", "actual"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["readings"],
    "additionalProperties": False,
}

PROMPT = """התמונה היא עמוד מתוך פקודת מטכ"ל. שכבת הטקסט של ה-PDF פגומה **בספרות בלבד** —
האותיות העבריות חולצו נכון, והספרות עברו החלפה. הצורות בתמונה תקינות.

לפניך קטעים שחולצו מהטקסט של העמוד הזה. בכל קטע סימנתי מספר אחד ב-«». העברית
מסביב נכונה ומשמשת אותך כעוגן לאיתור המקום המדויק בתמונה:

{items}

לכל קטע: מצא את המשפט הזה בתמונה, והסתכל במקום שבו יושב המספר המסומן.

כללים:
- `extracted` — המספר בדיוק כפי שהוא בין ה-«» שנתתי לך.
- `actual` — מה שכתוב שם בתמונה, **באותו מספר ספרות בדיוק**. החלפה אינה משנה אורך.
- **אל תתאים לפי דמיון מספרי.** אם המשפט לא נמצא בתמונה, או שאינך בטוח שזה אותו
  מקום — **השמט את הקטע.** קריאה שגויה אחת מרעילה את כל הטבלה; קטע חסר רק מקטין
  אותה, וזה מחיר זול בהרבה.
- אל תשלים לפי הקשר או לפי מה שנשמע הגיוני. קרא את הצורות שבתמונה."""


def busiest_page(pdf) -> int:
    """The page with the most digit runs — the most alignment signal per image."""
    best, best_n = 0, -1
    for i, page in enumerate(pdf):
        n = len(RUN.findall(page.get_text()))
        if n > best_n:
            best, best_n = i, n
    return best


def derive(pairs: list[tuple[str, str]]) -> dict[str, str] | None:
    """A digit table from observed pairs, or None if they do not agree."""
    table: dict[str, str] = {}
    for got, real in pairs:
        if len(got) != len(real) or not real.isdigit():
            return None
        for a, b in zip(got, real):
            if table.setdefault(a, b) != b:
                return None
    if len(table) < MIN_DIGITS_MAPPED:
        return None
    if len(set(table.values())) != len(table):
        return None                      # not injective: a misread, not a permutation
    return table


def apply_table(text: str, table: dict[str, str]) -> str:
    return "".join(table.get(c, c) for c in text)


def improves(raw: str, table: dict[str, str]) -> tuple[bool, float, float]:
    before, _ = year_share(raw)
    after, _ = year_share(apply_table(raw, table))
    b = before if before is not None else 0.0
    a = after if after is not None else 0.0
    return a > b and a >= 0.34, b, a


def read_page(doc: dict, ledger: Ledger) -> tuple[list[tuple[str, str]], float, str]:
    import fitz

    path = C.ROOT / "pdf-ldf_law" / str(doc.get("source_file", ""))
    if not path.exists():
        return [], 0.0, f"pdf missing: {path.name}"
    pdf = fitz.open(str(path))
    idx = busiest_page(pdf)
    page = pdf[idx]
    # Each number goes out inside its own Hebrew sentence. The letters survived
    # extraction, so they locate the number unambiguously on the page — without
    # that anchor the model matched numbers by resemblance and produced readings
    # like 1262 -> 1967 that belong to different places entirely.
    text = re.sub(r"\s+", " ", page.get_text())
    items, seen = [], set()
    for m in RUN.finditer(text):
        num = m.group(1)
        if num in seen:
            continue
        seen.add(num)
        ctx = text[max(0, m.start() - 55):m.start()] + f"«{num}»" + text[m.end():m.end() + 55]
        items.append(f"- {ctx.strip()}")
        if len(items) >= 24:
            break
    if len(items) < MIN_PAIRS:
        return [], 0.0, f"page {idx} has only {len(items)} digit runs"
    png = page.get_pixmap(dpi=DPI).tobytes("png")

    est = 0.006
    rid = ledger.reserve(f"unscramble:{doc['document_id']}", est)
    try:
        r = backend.client.messages.create(
            model=MODEL, max_tokens=2000,
            output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                             "data": base64.b64encode(png).decode()}},
                {"type": "text", "text": PROMPT.format(items="\n".join(items))},
            ]}],
        )
    except Exception as e:
        ledger.settle(rid, 0.0)
        return [], 0.0, f"api error: {type(e).__name__}: {e}"
    usd = cost_usd(MODEL, input_tokens=r.usage.input_tokens,
                   output_tokens=r.usage.output_tokens)
    ledger.settle(rid, usd)

    try:
        data = json.loads("".join(b.text for b in r.content if b.type == "text"))
    except json.JSONDecodeError:
        return [], usd, "unparseable response"
    pairs = [(str(x["extracted"]), str(x["actual"])) for x in data.get("readings", [])]
    return pairs, usd, ""


def run(limit: int | None = None, apply: bool = False) -> None:
    ledger = Ledger(C.LEDGER)
    targets = [d for d in backend.load_documents()
               if d.get("document_id") and is_broken(d) and d.get("source_file")]
    if limit:
        targets = targets[:limit]

    C.log(f"[unscramble] {len(targets)} orders with unrecoverable digits "
          f"(~${0.006 * len(targets):.2f}, budget left ${ledger.remaining():.2f})")
    if not apply:
        for d in targets[:15]:
            C.log(f"[unscramble]   would read {d['document_id']} ({d['source_file']})")
        C.log("[unscramble] dry run - nothing spent. Use --apply.")
        return

    fixed = failed = 0
    for i, doc in enumerate(targets, 1):
        did = doc["document_id"]
        try:
            pairs, usd, err = read_page(doc, ledger)
        except BudgetExceeded as e:
            C.log(f"[unscramble] STOPPING: {e}")
            break
        if err:
            failed += 1
            C.log(f"[unscramble] {i}/{len(targets)} {did} SKIP: {err}")
            continue

        table = derive(pairs)
        if not table:
            failed += 1
            C.append_jsonl(REJECTED, {"doc_id": did, "reason": "pairs disagree",
                                      "pairs": pairs})
            C.log(f"[unscramble] {i}/{len(targets)} {did} REJECTED: "
                  f"{len(pairs)} readings do not form one table")
            continue

        raw = str(doc.get("raw_text", ""))
        good, before, after = improves(raw, table)
        if not good:
            failed += 1
            C.append_jsonl(REJECTED, {"doc_id": did, "reason": "no improvement",
                                      "table": table, "before": before, "after": after})
            C.log(f"[unscramble] {i}/{len(targets)} {did} REJECTED: plausible years "
                  f"{before:.2f} -> {after:.2f}")
            continue

        doc["raw_text"] = apply_table(raw, table)
        doc["digits_recovered"] = {"table": table, "year_share": round(after, 2)}
        json.dump(doc, open(doc_path(doc), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        fixed += 1
        C.append_jsonl(ACCEPTED, {"doc_id": did, "table": table,
                                  "before": before, "after": after, "usd": usd})
        C.log(f"[unscramble] {i}/{len(targets)} {did} OK  {len(table)} digits, "
              f"years {before:.2f} -> {after:.2f}, ${usd:.4f} | spent ${ledger.spent:.2f}")

    C.log(f"[unscramble] done: {fixed} recovered, {failed} left alone, "
          f"spent ${ledger.spent:.2f}")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--apply"]
    run(limit=int(args[0]) if args else None, apply="--apply" in sys.argv)
