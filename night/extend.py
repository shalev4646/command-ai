"""Close the gap between what an anchor promises and what the block delivers.

`promise.py` measured 117 anchors (cosine < 0.35 to their nearest curated
clause) whose own order's key-facts block does not speak to them. Each one is a
question the corpus advertises it can answer and then cannot: the exact anchor
text still retrieves the order — it is indexed as its own chunk — so the gap
stays hidden until someone phrases the question their own way.

This is the only lever on answer quality that does not require new orders, so
it is what runs while the corpus is blocked on acquisition.

The edit is additive: existing clauses are never rewritten, only new ones
appended for the uncovered anchors. Same two faithfulness gates as the original
curation, and the result is re-measured against `promise.py` afterwards so the
claim "the gap closed" is a measurement rather than an intention.
"""
from __future__ import annotations

import json

import backend
from night import config as C
from night.curate import SCHEMA, check
from night.ledger import Ledger, BudgetExceeded, cost_usd
from night.promise import UNCOVERED
from night.rehearse import doc_path

MODEL = "claude-opus-4-8"

PROMPT = """לפניך פקודת מטכ"ל, סעיף „עיקרי הפקודה" שכבר נכתב לה, ורשימת שאלות
שהפקודה מפרסמת שהיא עונה עליהן — אבל הסעיף הקיים לא נוגע בהן.

כותרת: {title}

הסעיף הקיים:
{existing}

השאלות שאינן מכוסות:
{uncovered}

כתוב **סעיפים נוספים בלבד** שיכסו את השאלות האלה, מתוך הטקסט הגולמי.

הטקסט הגולמי:
{raw}

כללים מחייבים:
1. **רק מה שכתוב בפקודה.** אם הפקודה בעצם לא עונה על שאלה מסוימת — אל תכתוב עליה כלום.
   עדיף להחזיר שני סעיפים מדויקים מאשר חמישה שאחד מהם מומצא.
2. **אל תחזור** על מה שכבר מכוסה בסעיף הקיים.
3. כל סעיף מצטט מספרי סעיפים מהפקודה בסוגריים: „(סעיף 43)".
4. `number` הוא תווית בשפת המשתמש — איך חייל היה שואל את זה.
5. ‏40–120 מילים לסעיף."""


def uncovered_by_doc() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for r in C.read_jsonl(C.OUT / "promise.jsonl"):
        if r["best_cosine"] < UNCOVERED:
            out.setdefault(r["doc_id"], []).append(r["anchor"])
    return out


def run(limit: int | None = None) -> None:
    ledger = Ledger(C.LEDGER)
    targets = uncovered_by_doc()
    # promise.jsonl is the pre-extension snapshot, so a resumed run would pay
    # again for orders already handled
    already = {r["doc_id"] for r in C.read_jsonl(C.OUT / "extend_accepted.jsonl")}
    order = sorted((d for d in targets if d not in already),
                   key=lambda d: -len(targets[d]))
    if limit:
        order = order[:limit]
    C.log(f"[extend] {sum(len(v) for v in targets.values())} uncovered anchors "
          f"across {len(targets)} orders; processing {len(order)} "
          f"(budget ${ledger.remaining():.2f})")

    from storage.vector_store import index_document
    done = skipped = 0
    for i, doc_id in enumerate(order, 1):
        path = doc_path(doc_id)
        doc = json.loads(path.read_text(encoding="utf-8"))
        secs = doc.get("sections") or []
        if not secs:
            skipped += 1
            continue
        existing = "\n".join(f"- {c.get('number','')}: {c.get('text','')}"
                             for c in secs[0].get("clauses", []))
        raw = " ".join(str(doc.get("raw_text", "")).split()[:9000])
        anchors = targets[doc_id]

        est = (len(raw.split()) * 1.6 * 5 + 1500 * 25) / 1_000_000
        try:
            rid = ledger.reserve(f"extend:{doc_id}", est)
        except BudgetExceeded as e:
            C.log(f"[extend] STOPPING — {e}")
            break
        try:
            r = backend.client.messages.create(
                model=MODEL, max_tokens=8000,
                thinking={"type": "adaptive"},
                output_config={"effort": "high",
                               "format": {"type": "json_schema", "schema": SCHEMA}},
                messages=[{"role": "user", "content": PROMPT.format(
                    title=doc.get("title", ""), existing=existing,
                    uncovered="\n".join(f"- {a}" for a in anchors), raw=raw)}])
        except Exception as e:
            ledger.settle(rid, 0.0)
            C.log(f"[extend] {doc_id} api error: {type(e).__name__}")
            continue
        usd = cost_usd(MODEL, input_tokens=r.usage.input_tokens,
                       output_tokens=r.usage.output_tokens)
        ledger.settle(rid, usd)

        try:
            parsed = json.loads("".join(b.text for b in r.content if b.type == "text"))
        except json.JSONDecodeError:
            C.log(f"[extend] {doc_id} bad JSON")
            continue
        new = parsed.get("clauses") or []
        if not new:
            C.log(f"[extend] {doc_id}: model added nothing (order likely silent) ${usd:.3f}")
            continue

        problems, warnings = check({"clauses": new}, str(doc.get("raw_text", "")))
        if problems:
            C.log(f"[extend] {doc_id} REJECTED: {problems[0][:110]} ${usd:.3f}")
            C.append_jsonl(C.OUT / "extend_rejected.jsonl",
                           {"doc_id": doc_id, "problems": problems, "clauses": new})
            continue

        secs[0]["clauses"] = list(secs[0].get("clauses", [])) + new
        doc["sections"] = secs
        path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
        n = index_document(json.loads(path.read_text(encoding="utf-8")))
        done += 1
        C.log(f"[extend] {i}/{len(order)} {doc_id} +{len(new)} clauses for "
              f"{len(anchors)} anchors, {n} chunks, ${usd:.3f} | spent ${ledger.spent:.2f}")
        C.append_jsonl(C.OUT / "extend_accepted.jsonl",
                       {"doc_id": doc_id, "added": new, "anchors": anchors,
                        "warnings": warnings, "usd": usd})

    C.log(f"[extend] done: {done} extended, {skipped} skipped, spent ${ledger.spent:.2f}")


if __name__ == "__main__":
    import sys
    run(limit=int(sys.argv[1]) if len(sys.argv) > 1 else None)
