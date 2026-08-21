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

# Where the frozen decompositions live, keyed by question id. This file is the
# denominator of every before/after comparison: as long as it does not change,
# two runs are measured against the same yardstick.
PARTS = C.OUT / "question_parts.json"

PARTS_SCHEMA = {
    # no minItems/maxItems: the Batch API rejects them on array types
    # ("For 'array' type, property 'maxItems' is not supported") and the whole
    # batch dies at submit validation. The over-splitting guard lives in the
    # prompt, and ensure_parts drops empty lists on read.
    "type": "object",
    "properties": {
        "parts": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["parts"],
    "additionalProperties": False,
}

PARTS_PROMPT = """פרק את שאלת החייל הבאה לחלקים הענייניים שלה.

השאלה:
{q}

חלק ענייני = דבר נפרד שהשואל רוצה לדעת, שאפשר לענות עליו או לא לענות עליו
באופן עצמאי. נסח כל חלק כביטוי קצר.

⚠ אל תפרק יתר על המידה. שאלה פשוטה כמו „כמה שעות שינה מינימום בטירונות?" היא
חלק אחד, לא ארבעה. רקע, נימוס והסבר-מצב אינם חלקים — רק מה שנשאל בפועל."""

SCHEMA = {
    "type": "object",
    "properties": {
        "level": {"type": "string", "enum": list(LEVELS)},
        "answered": {"type": "array", "items": {"type": "boolean"}},
        "reason": {"type": "string"},
    },
    "required": ["level", "answered", "reason"],
    "additionalProperties": False,
}

PROMPT = """אתה מדרג תשובה של עוזר דיגיטלי לחיילים על פקודות מטכ"ל.

השאלה:
{q}

חלקי השאלה, לפי הסדר:
{parts}

התשובה:
{a}

`answered` — מערך בוליאני **באותו אורך ובאותו סדר** כמו רשימת החלקים: `true`
לחלק שהתשובה נתנה עליו מענה ממשי מתוך הפקודות, `false` לחלק שלא נענה, מכל סיבה.
(גם שלילה — „הפקודות אינן קובעות כלל כזה" — נספרת `false` כאן: המדרג אינו יכול
לדעת אם השלילה נכונה, וזיכוי שלילות תיגמל גם את השגויות. שלילות-כנות נספרות
בנפרד, בהצלבה עם בוררות-אמת.)

ואז סווג:
- "full" — לא נשאר אף חלק בלי מענה.
- "led_known" — חלק נענה וחלק לא, והתשובה אמרה במפורש על מה אין מידע.
- "partial" — חלק נענה וחלק לא, בלי לומר במפורש מה חסר.
- "refused" — אף חלק לא נענה.

`reason` — משפט אחד קצר בעברית.

⚠ רשימת החלקים נתונה ואינה ניתנת לשינוי. אל תוסיף, אל תסיר ואל תאחד — הרשימה
הזו נקבעה מהשאלה בלבד, לפני שמישהו ראה תשובה, בדיוק כדי ששתי ריצות שונות
יימדדו מול אותו מכנה.

⚠ ואל תעדיף רמה כלשהי. שתי ריצות קודמות החזירו תווית אחת כמעט לכל התשובות
(‏76 `led_known` באחת, 30 מתוך 30 בשנייה) — הסיווג הוא השדה החלש כאן, `answered`
הוא השדה שנמדד."""


def _batch(reqs, label: str, ledger: Ledger, per_req: float) -> dict[int, dict]:
    """Submit, wait, and return {index: parsed json} for a small grading batch."""
    rid = ledger.reserve(label, len(reqs) * per_req)
    batch = backend.client.messages.batches.create(requests=reqs)
    C.log(f"[grade] {label}: batch {batch.id}, {len(reqs)} requests "
          f"(~${len(reqs) * per_req:.2f})")
    failures = 0
    while True:
        try:
            b = backend.client.messages.batches.retrieve(batch.id)
        except Exception as e:      # transient poll error; the batch is safe server-side
            failures += 1
            C.log(f"[grade]   poll error {failures}/10: {type(e).__name__}")
            if failures >= 10:
                raise
            time.sleep(30)
            continue
        failures = 0
        if b.processing_status == "ended":
            break
        time.sleep(30)

    actual, out = 0.0, {}
    for res in backend.client.messages.batches.results(batch.id):
        i = int(res.custom_id[1:])
        if res.result.type != "succeeded":
            continue
        m = res.result.message
        actual += cost_usd(MODEL, input_tokens=m.usage.input_tokens,
                           output_tokens=m.usage.output_tokens, batch=True)
        try:
            out[i] = json.loads("".join(b.text for b in m.content if b.type == "text"))
        except json.JSONDecodeError:
            pass
    ledger.settle(rid, actual)
    return out


def ensure_parts(rows: list[dict], ledger: Ledger) -> dict[str, list[str]]:
    """Decompose each question ONCE, from the question alone, and cache it.

    This is the whole fix. The old grader was handed the question and the answer
    together and asked to split the question — so a richer answer invited a finer
    split. Measured across two runs of the same 30 questions, 19 came back with a
    different part count (15 up, 4 down; 63 parts total became 79), and the worst
    case took "כמה שעות שינה מינימום בטירונות?" from 1 part to 4. Since `full`
    means unanswered_parts == 0, inflating the denominator drives full toward
    zero on its own, which is exactly what the wave-1 re-measure reported.

    Decomposing without the answer in context removes the anchor, and caching the
    result means the baseline and every future re-measure divide by the same
    number. A cached question is never re-decomposed, so the yardstick cannot
    drift even if this prompt is later reworded.
    """
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request

    cache = json.loads(PARTS.read_text(encoding="utf-8")) if PARTS.exists() else {}
    todo = [r for r in rows if str(r["id"]) not in cache]
    if todo:
        reqs = [Request(custom_id=f"d{i}", params=MessageCreateParamsNonStreaming(
            model=MODEL, max_tokens=400,
            output_config={"format": {"type": "json_schema", "schema": PARTS_SCHEMA}},
            messages=[{"role": "user", "content": PARTS_PROMPT.format(q=r["q"])}]))
            for i, r in enumerate(todo)]
        got = _batch(reqs, "decompose", ledger, 0.0008)
        for i, r in enumerate(todo):
            if i in got and got[i].get("parts"):
                cache[str(r["id"])] = got[i]["parts"]
        PARTS.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
        C.log(f"[grade] decomposed {len(todo)} new questions; cache holds {len(cache)}")
    else:
        C.log(f"[grade] all {len(rows)} questions already decomposed")
    return cache


def grade_file(path, ledger: Ledger, label: str) -> None:
    rows = [r for r in C.read_jsonl(path) if r.get("answer")]
    if not rows:
        C.log(f"[grade] {label}: nothing to grade")
        return

    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request

    parts = ensure_parts(rows, ledger)
    rows = [r for r in rows if str(r["id"]) in parts]

    reqs = [
        Request(custom_id=f"g{i}", params=MessageCreateParamsNonStreaming(
            model=MODEL, max_tokens=500,
            output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
            messages=[{"role": "user", "content": PROMPT.format(
                q=r["q"], a=r["answer"][:6000],
                parts="\n".join(f"{j+1}. {p}"
                                for j, p in enumerate(parts[str(r["id"])])))}]))
        for i, r in enumerate(rows)
    ]
    got = _batch(reqs, f"grade-{label}", ledger, 0.0015)
    for i, r in enumerate(rows):
        g = got.get(i)
        if not g:
            r["grade"] = None
            continue
        # answered[] is authoritative and the part list is fixed, so the counts
        # are derived rather than reported — a grader that returns the wrong
        # array length cannot silently change the denominator
        want = len(parts[str(r["id"])])
        flags = (g.get("answered") or [])[:want]
        flags += [False] * (want - len(flags))
        r["grade"] = {"level": g.get("level"), "reason": g.get("reason"),
                      "answered_parts": sum(1 for f in flags if f),
                      "unanswered_parts": sum(1 for f in flags if not f),
                      "answered": flags, "parts": parts[str(r["id"])]}

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
