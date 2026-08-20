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


def run_sync(reqs, meta, ledger: Ledger, out_path, label: str, workers: int = 4) -> None:
    """The same requests, sent directly instead of through the Batch API.

    Twice the price and minutes instead of hours: the 2026-08-18 round sat
    28 hours in the provider's batch queue while the decision it fed waited.
    When the measurement is on the critical path, that trade is wrong.
    REMEASURE_SYNC=1 routes run_batch here.
    """
    import concurrent.futures as cf
    est = len(reqs) * 0.056          # ~2x the batch estimate
    rid = ledger.reserve(label, est)
    C.log(f"[probe] {label}: sending {len(reqs)} requests synchronously, {workers} at a time "
          f"(~${est:.2f}, ${ledger.remaining():.2f} left after)")

    def one(i_req):
        i, req = i_req
        params = dict(req["params"])
        try:
            m = backend.client.with_options(timeout=240.0, max_retries=2).messages.create(**params)
        except Exception as e:
            return i, None, 0.0, f"{type(e).__name__}: {str(e)[:80]}"
        text = "".join(bl.text for bl in m.content if bl.type == "text")
        usd = cost_usd(backend.MODEL, input_tokens=m.usage.input_tokens,
                       output_tokens=m.usage.output_tokens,
                       cache_write_tokens=getattr(m.usage, "cache_creation_input_tokens", 0) or 0,
                       cache_read_tokens=getattr(m.usage, "cache_read_input_tokens", 0) or 0,
                       batch=False)
        return i, {"answer": text, "truncated": m.stop_reason == "max_tokens",
                   "refused_flag": _is_refusal(text)}, usd, ""

    rows: list[dict | None] = [None] * len(reqs)
    actual, failed = 0.0, 0
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        for i, res, usd, err in ex.map(one, list(enumerate(reqs))):
            actual += usd
            if res is None:
                failed += 1
                rows[i] = {**meta[i], "answer": None, "error": err}
            else:
                rows[i] = {**meta[i], **res}
            done = sum(1 for r in rows if r is not None)
            if done % 10 == 0:
                C.log(f"[probe]   {done}/{len(reqs)} answered (${actual:.2f})")
    ledger.settle(rid, actual)
    C.write_jsonl(out_path, rows)
    C.log(f"[probe] {label}: {len(reqs) - failed}/{len(reqs)} answered, ${actual:.2f} "
          f"(ledger total ${ledger.spent:.2f})" + (f"  ⚠ {failed} failed" if failed else ""))
    if failed > len(reqs) // 4:
        raise SystemExit(f"{failed}/{len(reqs)} requests failed — the arm would be measuring "
                         f"a sample, not the set")


def run_batch(reqs, meta, ledger: Ledger, out_path, label: str) -> None:
    import os
    if os.environ.get("REMEASURE_SYNC") == "1":
        return run_sync(reqs, meta, ledger, out_path, label)
    est = len(reqs) * 0.028          # measured ~$0.05 unbatched on this corpus
    rid = ledger.reserve(label, est)
    C.log(f"[probe] {label}: submitting {len(reqs)} requests (~${est:.2f}, "
          f"${ledger.remaining():.2f} left after)")

    batch = backend.client.messages.batches.create(requests=reqs)
    # A batch is paid for at submit and lives on the server for 24h, but `meta`
    # — the map from custom_id back to which question was asked — lives only in
    # this process. Lose the process and the results become unattributable text.
    # That is a real failure mode, not a hypothetical: a 30-request batch sat in
    # the provider's queue for three hours here, well past the point where
    # keeping a session open to babysit it is reasonable. Writing the claim
    # ticket to disk lets `night.collect` finish the job later.
    ticket = C.OUT / f"batch_{label}.json"
    ticket.write_text(json.dumps(
        {"batch_id": batch.id, "label": label, "rid": rid,
         "out_path": str(out_path), "meta": meta}, ensure_ascii=False),
        encoding="utf-8")
    C.log(f"[probe] batch {batch.id} submitted; polling every 60s "
          f"(claim ticket: {ticket.name})")
    # A poll is a GET on a batch that is safe on the server; a transient
    # network error must not kill the watcher (2026-08-18: two arms died on
    # APITimeoutError mid-poll and had to be finished by night.collect by
    # hand). Ten consecutive failures is a real outage and still raises.
    failures = 0
    while True:
        try:
            b = backend.client.messages.batches.retrieve(batch.id)
        except Exception as e:
            failures += 1
            C.log(f"[probe]   poll error {failures}/10: {type(e).__name__}")
            if failures >= 10:
                raise
            time.sleep(60)
            continue
        failures = 0
        if b.processing_status == "ended":
            break
        C.log(f"[probe]   {b.processing_status} "
              f"succeeded={b.request_counts.succeeded} "
              f"processing={b.request_counts.processing}")
        time.sleep(60)

    collect_batch(batch.id, meta, rid, out_path, label, ledger)


def collect_batch(batch_id: str, meta: list[dict], rid: str | None,
                  out_path, label: str, ledger: Ledger) -> None:
    """Turn a finished batch into graded rows. Split out so it can run later.

    `rid` may be None when the reservation was already settled by a process
    that died after paying — the results are still worth collecting, they were
    bought and the ledger already knows about the money.
    """
    actual = 0.0
    rows = []
    for res in backend.client.messages.batches.results(batch_id):
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
    if rid:
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
