"""Hypothetical-answer queries: cache, cost control, and the gate runs.

The finding this exists to test (2026-08-17, all of it on the free evidence
instrument first):

    query                     answering order in the window   span served
    the question              4/16                            3/91 spans
    the answering sentence   16/16                            —  (oracle)
    a Haiku-written order     9/16                            6/16 questions

The oracle row is the argument. Retrieval is not failing on phrasing — the
typo-free variant of every ugly question scores exactly what the ugly one does
(4/16) — it is failing because a soldier's question and a פקודת מטכ"ל clause
are far apart in this embedding space no matter how the question is worded.
Querying with order-shaped prose closes that distance.

Why this is not the direction that was already rejected: `night.rewrite` turned
a question into a better question, which keeps both sides of the asymmetry in
place, and it scored 345/415 against 387. Nothing about that result predicts
this one, and nothing about this one rehabilitates that one.

The gate is the guardrail, not the evidence set. Rewrite's failure was never
"it helps nothing" — it saved 22 questions and broke 64. So the number that
decides this is the 415-case gate, and a variant that does not hold it is
finished regardless of what it does for the sixteen.

    python -m night.hyde --gate          # generate (cached), then gate each variant
    python -m night.hyde --evidence      # the same variants on the evidence set
"""
from __future__ import annotations

import json
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import backend
from night import config as C
from night.ledger import BudgetExceeded, Ledger, cost_usd

MODEL = "claude-haiku-4-5"
CACHE = C.OUT / "hyde_cache.json"
USD_PER_QUESTION = 0.00115      # measured over the first 16, not estimated

PROMPT = """שאלה של חייל: {q}

כתוב פסקה קצרה (עד 60 מילים) בניסוח של פקודת מטכ"ל, כפי שהיא הייתה מנוסחת בפקודה
שעונה על השאלה. כתוב בלשון הפקודות — "חייל אשר...", "מפקד יחידה רשאי...", "יהיה זכאי".
אל תענה לחייל ואל תוסיף הסתייגויות: רק את נוסח הפקודה המשוער. אם אינך יודע את
הפרטים, כתוב את הנוסח הכללי עם המונחים המקצועיים הצפויים."""


def preload_backend() -> int:
    """Push the disk cache into backend's per-process one.

    Two reasons, and the second is the one that matters. A measurement should
    not re-buy text it already owns — but more than that, it must not buy a
    DIFFERENT draw of it: the variant that held 390/415 on the gate was gated on
    these exact paragraphs, and regenerating would price a treatment that was
    never the one measured. Same discipline as the frozen question decomposition
    in question_parts.json.
    """
    backend._hyde_cache.update(_load_cache())
    return len(backend._hyde_cache)


def _load_cache() -> dict[str, str]:
    if CACHE.exists():
        return json.loads(CACHE.read_text(encoding="utf-8"))
    return {}


def generate(questions: list[str]) -> dict[str, str]:
    """Hypothetical order-text per question, cached on disk.

    Failures are counted and a run that loses more than a quarter of its calls
    aborts instead of returning a number. A silent fall back to the raw
    question would report the baseline as if the treatment had run — which is
    exactly how the rewrite sweep produced a perfect score from 387 dead calls.
    """
    cache = _load_cache()
    todo = [q for q in dict.fromkeys(questions) if q not in cache]
    if not todo:
        return cache

    est = USD_PER_QUESTION * len(todo)
    ledger = Ledger(C.LEDGER)
    rid = ledger.reserve("hyde:generate", est)
    C.log(f"[hyde] generating {len(todo)} hypotheticals, ~${est:.2f} "
          f"(${ledger.remaining():.2f} left after)")

    spent, failures = 0.0, 0
    try:
        for i, q in enumerate(todo, 1):
            try:
                r = backend.client.with_options(timeout=30.0, max_retries=2).messages.create(
                    model=MODEL, max_tokens=300, temperature=0,
                    messages=[{"role": "user", "content": PROMPT.format(q=q)}])
                cache[q] = "".join(b.text for b in r.content if b.type == "text").strip()
                spent += cost_usd(MODEL, input_tokens=r.usage.input_tokens,
                                  output_tokens=r.usage.output_tokens)
            except Exception as e:
                failures += 1
                C.log(f"[hyde] FAIL {type(e).__name__}: {str(e)[:90]}")
            if i % 50 == 0:
                CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1),
                                 encoding="utf-8")
                C.log(f"[hyde] {i}/{len(todo)} (${spent:.2f}, {failures} failed)")
    finally:
        ledger.settle(rid, spent)
        CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")

    C.log(f"[hyde] generated {len(todo) - failures}/{len(todo)}, {failures} failures, ${spent:.2f}")
    if failures > len(todo) // 4:
        raise RuntimeError(
            f"{failures}/{len(todo)} generations failed — the run would be measuring "
            f"the baseline while reporting the treatment")
    return cache


# --- retrieval variants -------------------------------------------------------
# All three keep the question's own retrieval intact and differ only in how much
# of the window the hypothetical is allowed to claim. Reserved slots rather than
# a merged score sort, for the reason already learnt in stream_ai_answer: the
# two retrievals normalize their lexical bonus against their own query, so the
# scores are not on one scale and sorting the union lets one side silently
# evict the other.
#
# Merged by INTERLEAVING RANKS, not by appending. Rank is the only thing the two
# lists share — 0.83 from the hypothetical's retrieval and 0.83 from the
# question's are not the same quantity — and position is not cosmetic: the gate
# asserts on the first three distinct orders, so a variant whose contribution
# lands at the end of the list is scored as though it contributed nothing. The
# question keeps position 1 either way, so the merge can only add.

def retrieve_variant(question: str, role: str, hyp: str | None,
                     hyde_slots: int, route: set[str] | None = None) -> list[dict]:
    """`hyde_slots` of the window go to the hypothetical's retrieval, the rest
    to the question's. 0 is production; MAX_CONTEXT_CHUNKS is hypothetical-only."""
    route = route or set()
    if not hyp or hyde_slots <= 0:
        return backend.retrieve_for_role(question, role, route=route)

    hyde_chunks = backend.retrieve_for_role(hyp, role, route=route)
    if hyde_slots >= backend.MAX_CONTEXT_CHUNKS:
        return hyde_chunks[:backend.MAX_CONTEXT_CHUNKS]

    q_chunks = backend.retrieve_for_role(question, role, route=route)
    merged, seen, taken = [], set(), 0
    for i in range(backend.MAX_CONTEXT_CHUNKS):
        for source, is_hyde in ((q_chunks, False), (hyde_chunks, True)):
            if is_hyde and taken >= hyde_slots:
                continue
            if i >= len(source):
                continue
            c = source[i]
            key = (c["doc_id"], c.get("section"), c.get("clause"))
            if key in seen:
                continue
            seen.add(key)
            merged.append(c)
            taken += is_hyde
            if len(merged) >= backend.MAX_CONTEXT_CHUNKS:
                return merged
    return merged


def retrieve_boost(question: str, role: str, hyp: str | None,
                   n_docs: int, route: set[str] | None = None) -> list[dict]:
    """The hypothetical as a HINT instead of a tenant.

    Slot-taking costs 13 of the gate's top-3 placements to rescue 5, because
    every slot the hypothetical occupies is one the question's own ranking
    does not. But the gain on the evidence set came from the answering order
    ENTERING the window at all, not from the hypothetical's chunks being good
    — so the hypothetical's verdict is worth more than its text. Here it names
    documents and the router's existing bonus lifts them, exactly the shape
    `_route_docs` already has, and the question keeps all eight slots.
    """
    route = set(route or set())
    if hyp and n_docs > 0:
        picks: list[str] = []
        for c in backend.retrieve_for_role(hyp, role, route=set()):
            if c["doc_id"] not in picks:
                picks.append(c["doc_id"])
            if len(picks) >= n_docs:
                break
        route |= set(picks)
    return backend.retrieve_for_role(question, role, route=route)


# Hint-only variants are gone from the sweep: on the evidence set boost1/2/3 all
# scored exactly production (4/16 orders, and 2 spans against 3), which is the
# same wall `_ROUTE_BOOST` already documents — a perfect routing list returns an
# identical top-10, because 0.05 cannot lift a document over a gap of 0.15. The
# hypothetical only helps when it is allowed to take a slot, so the question the
# gate has to answer is how few slots buy the gain: one already buys 7 of the 8
# spans that three buy.
VARIANTS = {"prod": 0, "hyde1": 1, "prod7": ("narrow", 7), "widen9": ("widen", 1)}


def retrieve_narrow(question: str, role: str, hyp, n: int, route=None) -> list[dict]:
    """The question's own retrieval, one slot short. THE CONTROL.

    hyde1 and hyde3 cost the same 12-13 top-3 placements, which cannot be about
    the hypothetical's content — one foreign chunk and three do not damage
    equally. The suspect is the slot budget itself: inserting anything at rank 2
    pushes the question's eighth chunk out, and in the cases that "broke", the
    third distinct order lived in that eighth chunk. If this control costs the
    same 12, then nothing was measured about hypotheticals at all, and the fix
    is to widen the window rather than to rent out a slot.
    """
    return backend.retrieve_for_role(question, role, route=route or set())[:n]


def retrieve_widen(question: str, role: str, hyp: str | None, extra: int, route=None) -> list[dict]:
    """The question keeps all eight; the hypothetical adds slots on top."""
    chunks = backend.retrieve_for_role(question, role, route=route or set())
    if not hyp:
        return chunks
    seen = {(c["doc_id"], c.get("section"), c.get("clause")) for c in chunks}
    for c in backend.retrieve_for_role(hyp, role, route=route or set()):
        key = (c["doc_id"], c.get("section"), c.get("clause"))
        if key not in seen:
            chunks.append(c)
            if len(chunks) >= backend.MAX_CONTEXT_CHUNKS + extra:
                break
    return chunks


_MODES = {"boost": retrieve_boost, "narrow": retrieve_narrow, "widen": retrieve_widen}


def _retrieve(question, role, hyp, spec, route=None):
    if isinstance(spec, tuple):
        return _MODES[spec[0]](question, role, hyp, spec[1], route)
    return retrieve_variant(question, role, hyp, spec, route)


def gate(top_k: int = 3) -> dict:
    """The 415 retrieval cases under each variant. Router bypassed throughout,
    same convention as `night.gate` — a harsher setting, applied equally."""
    from night.gate import load_cases
    cases = load_cases()
    cache = generate([q for _, q, _, _ in cases])

    results = {}
    for name, slots in VARIANTS.items():
        by_set: dict[str, list[int]] = {}
        failures = []
        # Two readings, both reported, because top-3 is not neutral between a
        # single retrieval and a merged one: the gate's assertion was written
        # when one ranking filled the window, so any variant that inserts a
        # foreign order near the top pushes the expected order out of third
        # place without removing it from what the model reads. "in-window"
        # asks only whether the order reaches the model at all. The top-3
        # number stays the headline — it is the one comparable to the 390 —
        # and the second exists so a merge is not condemned for reordering
        # alone.
        in_window = 0
        for role, q, expected, tag in cases:
            accepted = tuple(expected) if isinstance(expected, (list, tuple)) else (expected,)
            tops: list[str] = []
            for c in _retrieve(q, role, cache.get(q), slots):
                if c["doc_id"] not in tops:
                    tops.append(c["doc_id"])
            ok = any(e in tops[:top_k] for e in accepted)
            in_window += any(e in tops for e in accepted)
            by_set.setdefault(tag, [0, 0])
            by_set[tag][1] += 1
            by_set[tag][0] += ok
            if not ok:
                failures.append({"set": tag, "role": role, "question": q,
                                 "expected": expected, "got": tops[:top_k]})
        passed = sum(v[0] for v in by_set.values())
        results[name] = {"passed": passed, "of": len(cases), "in_window": in_window,
                         "by_set": {k: {"passed": v[0], "of": v[1]} for k, v in by_set.items()},
                         "failures": failures}
        C.log(f"[hyde] gate {name:<10} top-{top_k} {passed}/{len(cases)}   "
              f"in-window {in_window}/{len(cases)}   " + "  ".join(
                  f"{k} {v[0]}/{v[1]}" for k, v in by_set.items()))

    # what each variant costs the ones that already work, and buys elsewhere
    base = {f["question"] for f in results["prod"]["failures"]}
    for name in VARIANTS:
        if name == "prod":
            continue
        now = {f["question"] for f in results[name]["failures"]}
        C.log(f"[hyde] gate {name:<10} rescued {len(base - now):>3}   broke {len(now - base):>3}")
    (C.OUT / "hyde_gate.json").write_text(
        json.dumps({k: {kk: vv for kk, vv in v.items() if kk != "failures"}
                    for k, v in results.items()}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    return results


def evidence() -> None:
    """The same variants on the evidence targets — the other half of the trade."""
    from night.evidence import COVER_RECALL, TARGETS, content_words, norm, routes
    targets = json.loads(TARGETS.read_text(encoding="utf-8"))
    cache = generate([t["question"] for t in targets])
    route_by_q = routes(targets, True)

    for name, slots in VARIANTS.items():
        doc_any = served_q = served_spans = total = 0
        for t in targets:
            route = route_by_q.get(f'{t["role"]}|{norm(t["question"])}', set())
            chunks = _retrieve(t["question"], t["role"], cache.get(t["question"]),
                               slots, route)
            ctx = norm(backend._context_from_chunks(chunks))
            hits = sum(1 for s in t["spans"] if s in ctx)
            doc_any += any(c["doc_id"] == t["doc_id"] for c in chunks)
            served_q += hits > 0
            served_spans += hits
            total += len(t["spans"])
        C.log(f"[hyde] evidence {name:<10} order {doc_any:>2}/{len(targets)}   "
              f"questions with a served span {served_q:>2}/{len(targets)}   "
              f"spans {served_spans:>2}/{total}")


if __name__ == "__main__":
    if "--evidence" in sys.argv:
        evidence()
    else:
        gate()
