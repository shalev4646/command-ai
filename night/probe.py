"""Stage 3 — the only paid measurement: does the model actually answer?

Everything before this measures whether the right content REACHES the context.
That is necessary and not sufficient: 35.0206 sat at rank 5 on 2026-08-08 and
the model answered correctly anyway, so retrieval rank is evidence, not proof.
This stage closes the gap by running real questions through the real answering
model and grading what comes back.

Two decisions keep it affordable and honest:

  sample the middle   Questions whose retrieval is clearly strong or clearly
                      empty teach little — we can already predict them. The
                      yellow band is where the outcome is genuinely unknown, so
                      that is where the money goes.
  reuse production    The user turn is taken verbatim from `stream_ai_answer`,
                      which means the batch sends byte-identical prompts to
                      what the live app sends. No lookalike, no drift.

Batch API throughout: nothing here is latency-sensitive at 3am, and it halves
the bill.
"""
from __future__ import annotations

import json
import random
import time

import backend
from night import config as C
from night.ledger import Ledger, cost_usd

rng = random.Random(20260811)


def build_requests(rows: list[dict]) -> tuple[list, list[dict]]:
    """Compose each question through the real pipeline, then wrap for Batch."""
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request

    reqs, meta = [], []
    for i, r in enumerate(rows):
        # the generator is never consumed, so no Opus tokens are billed here —
        # this only runs retrieval and returns the exact user turn production
        # would have sent (verified with a trap on client.messages.stream)
        _gen, sources, user_content, _usage = backend.stream_ai_answer(
            r["q"], None, r["role"], None)
        del _gen
        system_prompt = backend.SYSTEM_PROMPTS.get(r["role"], backend.SYSTEM_PROMPT_SOLDIER)
        reqs.append(Request(
            custom_id=f"p{i}",
            params=MessageCreateParamsNonStreaming(
                model=backend.MODEL,
                max_tokens=backend.MAX_OUTPUT_TOKENS,
                thinking={"type": "adaptive"},
                system=[{"type": "text", "text": system_prompt,
                         "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": user_content}],
            )))
        meta.append({**r, "sources": [s.get("doc_id") for s in sources],
                     "context_words": len(user_content.split())})
        if (i + 1) % 25 == 0:
            C.log(f"[probe] composed {i + 1}/{len(rows)}")
    return reqs, meta


def run_batch(reqs, meta, ledger: Ledger, out_path, label: str) -> None:
    est = len(reqs) * 0.028          # measured ~$0.05 unbatched on this corpus
    rid = ledger.reserve(label, est)
    C.log(f"[probe] {label}: submitting {len(reqs)} requests (~${est:.2f}, "
          f"${ledger.remaining():.2f} left after)")

    batch = backend.client.messages.batches.create(requests=reqs)
    C.log(f"[probe] batch {batch.id} submitted; polling every 60s")
    while True:
        b = backend.client.messages.batches.retrieve(batch.id)
        if b.processing_status == "ended":
            break
        C.log(f"[probe]   {b.processing_status} "
              f"succeeded={b.request_counts.succeeded} "
              f"processing={b.request_counts.processing}")
        time.sleep(60)

    actual = 0.0
    rows = []
    for res in backend.client.messages.batches.results(batch.id):
        i = int(res.custom_id[1:])
        if res.result.type != "succeeded":
            rows.append({**meta[i], "answer": None, "error": res.result.type})
            continue
        m = res.result.message
        text = "".join(bl.text for bl in m.content if bl.type == "text")
        actual += cost_usd(backend.MODEL, input_tokens=m.usage.input_tokens,
                           output_tokens=m.usage.output_tokens,
                           cache_write_tokens=getattr(m.usage, "cache_creation_input_tokens", 0) or 0,
                           cache_read_tokens=getattr(m.usage, "cache_read_input_tokens", 0) or 0,
                           batch=True)
        rows.append({**meta[i], "answer": text,
                     "truncated": m.stop_reason == "max_tokens",
                     "refused_flag": _is_refusal(text)})
    ledger.settle(rid, actual)
    C.write_jsonl(out_path, rows)
    C.log(f"[probe] {label}: {sum(1 for r in rows if r.get('answer'))}/{len(rows)} answered, "
          f"${actual:.2f} (ledger total ${ledger.spent:.2f})")


def _is_refusal(text: str) -> bool:
    """The app's own coarse flag, kept for comparison against the 4-way grade."""
    from common import is_refusal
    return is_refusal(text or "")


def baseline() -> None:
    ledger = Ledger(C.LEDGER)
    sweep = C.read_jsonl(C.SWEEP)
    if not sweep:
        raise SystemExit("no sweep.jsonl — run `python -m night.run_sweep` first")

    yellow = [r for r in sweep if r["band"] == C.BAND_YELLOW]
    pool = yellow or [r for r in sweep if r["band"] != C.BAND_GREEN]
    n = min(C.N_PROBE, len(pool))
    # Stratify by source so the blind (honest-coverage) and inside-out
    # (reachability) halves are both represented rather than whichever is larger.
    blind = [r for r in pool if r["source"] == "blind"]
    inside = [r for r in pool if r["source"] == "inside_out"]
    take_b = min(len(blind), n * 2 // 3)
    sample = rng.sample(blind, take_b) + rng.sample(inside, min(len(inside), n - take_b))
    C.log(f"[probe] pool: {len(pool)} ({len(yellow)} yellow) -> sampling {len(sample)} "
          f"({take_b} blind / {len(sample) - take_b} inside-out)")

    reqs, meta = build_requests(sample)
    run_batch(reqs, meta, ledger, C.PROBE_BASE, "probe-baseline")


if __name__ == "__main__":
    baseline()
