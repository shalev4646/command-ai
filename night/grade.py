"""Stage 4 — grade answers on a four-level scale instead of a boolean.

`common.is_refusal` asks whether the refusal sentence opens the answer. That is
the right rule for the UI chip it mirrors, and the wrong ruler for measuring
coverage: on 2026-08-09 the WhatsApp-harassment answer delivered the group =
public-publication rule, the classified-information prohibition and the
sanction, refused only on the escalation half, and was counted as a total
refusal. Grading a night's work with that ruler produces a number nobody can
act on.

The four levels separate the two things that get conflated:

  full        answered the question
  led_known   answered what the orders do cover and said plainly what they
              do not — the behaviour the "חוק-2" upgrade is meant to produce,
              and a SUCCESS, not a failure
  partial     refused, but the answer still carries usable content
  refused     no usable content

Haiku, batched: a few cents for the whole set.
"""
from __future__ import annotations

import json
import time

import backend
from night import config as C
from night.ledger import Ledger, cost_usd

MODEL = "claude-haiku-4-5"

LEVELS = ("full", "led_known", "partial", "refused")

SCHEMA = {
    "type": "object",
    "properties": {
        "level": {"type": "string", "enum": list(LEVELS)},
        "answered_parts": {"type": "integer"},
        "unanswered_parts": {"type": "integer"},
        "reason": {"type": "string"},
    },
    "required": ["level", "answered_parts", "unanswered_parts", "reason"],
    "additionalProperties": False,
}

PROMPT = """אתה מדרג תשובה של עוזר דיגיטלי לחיילים על פקודות מטכ"ל.

השאלה:
{q}

התשובה:
{a}

דרג לאחת מארבע רמות:
- "full" — התשובה עונה על השאלה. גם אם יש סייגים, המשתמש קיבל את מה שביקש.
- "led_known" — השאלה כללה כמה חלקים; התשובה ענתה על מה שהפקודות כן מכסות
  ואמרה במפורש על מה אין מידע. **זו הצלחה, לא כישלון.**
- "partial" — התשובה בעיקרה סירוב, אבל יש בה תוכן שימושי כלשהו.
- "refused" — אין תוכן שימושי. רק "המידע לא קיים בפקודות" או שווה ערך.

ספור גם לכמה חלקים של השאלה נענה ולכמה לא.
`reason` — משפט אחד קצר בעברית."""


def grade_file(path, ledger: Ledger, label: str) -> None:
    rows = [r for r in C.read_jsonl(path) if r.get("answer")]
    if not rows:
        C.log(f"[grade] {label}: nothing to grade")
        return

    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request

    reqs = [
        Request(custom_id=f"g{i}", params=MessageCreateParamsNonStreaming(
            model=MODEL, max_tokens=500,
            output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
            messages=[{"role": "user", "content": PROMPT.format(
                q=r["q"], a=r["answer"][:6000])}]))
        for i, r in enumerate(rows)
    ]
    est = len(reqs) * 0.0015
    rid = ledger.reserve(f"grade-{label}", est)
    batch = backend.client.messages.batches.create(requests=reqs)
    C.log(f"[grade] {label}: batch {batch.id}, {len(reqs)} answers (~${est:.2f})")
    while True:
        b = backend.client.messages.batches.retrieve(batch.id)
        if b.processing_status == "ended":
            break
        time.sleep(30)

    actual = 0.0
    for res in backend.client.messages.batches.results(batch.id):
        i = int(res.custom_id[1:])
        if res.result.type != "succeeded":
            rows[i]["grade"] = None
            continue
        m = res.result.message
        actual += cost_usd(MODEL, input_tokens=m.usage.input_tokens,
                           output_tokens=m.usage.output_tokens, batch=True)
        try:
            rows[i]["grade"] = json.loads("".join(b.text for b in m.content if b.type == "text"))
        except json.JSONDecodeError:
            rows[i]["grade"] = None
    ledger.settle(rid, actual)

    out = C.OUT / f"grades_{label}.jsonl"
    C.write_jsonl(out, rows)

    from collections import Counter
    dist = Counter((r.get("grade") or {}).get("level", "ungraded") for r in rows)
    n = len(rows)
    C.log(f"[grade] {label}: {dict(dist)}")
    answered = dist["full"] + dist["led_known"]
    C.log(f"[grade] {label}: usable answers {answered}/{n} ({100*answered/n:.0f}%)")
    # the comparison that motivates the whole grader
    coarse = sum(1 for r in rows if r.get("refused_flag"))
    C.log(f"[grade] {label}: common.is_refusal would report {coarse}/{n} refusals "
          f"({100*coarse/n:.0f}%) vs {dist['refused']}/{n} flat refusals here")


if __name__ == "__main__":
    import sys
    which = sys.argv[1] if len(sys.argv) > 1 else "baseline"
    grade_file(C.PROBE_BASE if which == "baseline" else C.PROBE_AFTER,
               Ledger(C.LEDGER), which)
