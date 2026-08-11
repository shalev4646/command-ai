"""Grow the retrieval gate alongside the corpus.

The 415 gate cases were built against 98 orders — roughly four per order — and
they are the only instrument that says whether a corpus change broke something
that used to work. Adding 85 orders without adding cases does not keep the gate
constant; it dilutes it, and quietly.

So each new order gets three probes, written the way `eval_adversarial.json`'s
were: from angles the order's own anchors do NOT cover — a prohibition, an
authority or rank question, a cancellation, a time limit, an edge case — in
everyday phrasing that does not echo the title. An anchor-shaped probe would
pass by construction and test nothing.
"""
from __future__ import annotations

import json
import time

import backend
from night import config as C
from night.audit import _anchors
from night.ledger import Ledger, cost_usd

MODEL = "claude-haiku-4-5"
ADVERSARIAL = C.ROOT / "eval_adversarial.json"

SCHEMA = {
    "type": "object",
    "properties": {
        "probes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "role": {"type": "string", "enum": list(C.ROLES)},
                    "angle": {"type": "string"},
                },
                "required": ["question", "role", "angle"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["probes"],
    "additionalProperties": False,
}

PROMPT = """לפניך פקודת מטכ"ל והשאלות שכבר משמשות לה כעוגני-אחזור.

כותרת: {title}
מספר: {doc_id}

עוגנים קיימים:
{anchors}

תוכן:
{body}

כתוב **3 שאלות-בוחן** שהפקודה הזאת עונה עליהן, **מזוויות שהעוגנים לא מכסים**.
זוויות מועדפות: איסור · סמכות או דרגה · ביטול/שלילה · מגבלת-זמן · מקרה-קצה.

כללים:
1. **אסור** שהשאלה תחפוף בניסוח לעוגן קיים או לכותרת. שאלה שדומה לעוגן עוברת
   מעצם הבנייה ולא בודקת כלום.
2. ניסוח יומיומי של חייל או מפקד, לא שפת פקודות.
3. `role` — למי השאלה שייכת.
4. `angle` — הזווית, במילה או שתיים."""


def generate(doc_ids: list[str]) -> None:
    ledger = Ledger(C.LEDGER)
    docs = {d["document_id"]: d for d in backend.load_documents() if d.get("document_id")}
    todo = [d for d in doc_ids if d in docs]
    if not todo:
        C.log("[probes] nothing to do")
        return

    existing = json.loads(ADVERSARIAL.read_text(encoding="utf-8")) if ADVERSARIAL.exists() else []
    have = {p["expected"] for p in existing if isinstance(p.get("expected"), str)}
    todo = [d for d in todo if d not in have]
    C.log(f"[probes] {len(todo)} orders need gate probes (gate currently {len(existing)} cases)")
    if not todo:
        return

    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request

    reqs = []
    for i, doc_id in enumerate(todo):
        d = docs[doc_id]
        secs = d.get("sections") or []
        body = "\n".join(f"- {c.get('number','')}: {str(c.get('text',''))[:250]}"
                         for s in secs if isinstance(s, dict)
                         for c in (s.get("clauses") or []))[:2500] \
            or str(d.get("raw_text", ""))[:2500]
        reqs.append(Request(custom_id=f"pb{i}", params=MessageCreateParamsNonStreaming(
            model=MODEL, max_tokens=900,
            output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
            messages=[{"role": "user", "content": PROMPT.format(
                title=d.get("title", ""), doc_id=doc_id, body=body,
                anchors="\n".join(f"- {a}" for a in _anchors(d)[:12]) or "(אין)")}])))

    rid = ledger.reserve("gate-probes", len(reqs) * 0.0018)
    batch = backend.client.messages.batches.create(requests=reqs)
    C.log(f"[probes] batch {batch.id} for {len(reqs)} orders")
    while True:
        b = backend.client.messages.batches.retrieve(batch.id)
        if b.processing_status == "ended":
            break
        time.sleep(20)

    actual, added = 0.0, 0
    for res in backend.client.messages.batches.results(batch.id):
        i = int(res.custom_id[2:])
        if res.result.type != "succeeded":
            continue
        m = res.result.message
        actual += cost_usd(MODEL, input_tokens=m.usage.input_tokens,
                           output_tokens=m.usage.output_tokens, batch=True)
        try:
            probes = json.loads("".join(b.text for b in m.content if b.type == "text"))["probes"]
        except (json.JSONDecodeError, KeyError):
            continue
        for p in probes:
            existing.append({"role": p["role"], "question": p["question"],
                             "expected": todo[i], "angle": p.get("angle", "")})
            added += 1
    ledger.settle(rid, actual)

    ADVERSARIAL.write_text(json.dumps(existing, ensure_ascii=False, indent=1), encoding="utf-8")
    C.log(f"[probes] +{added} cases -> gate is now {len(existing)} adversarial cases, ${actual:.3f}")


if __name__ == "__main__":
    import sys
    generate(sys.argv[1:])
