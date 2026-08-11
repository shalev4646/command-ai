"""Stage 2 — the free retrieval sweep, and the caches that make it free.

Fidelity is the whole point here: a sweep that measures a lookalike pipeline
measures nothing. So instead of reimplementing retrieval, this module calls
`backend.stream_ai_answer` — the real production entry point — and simply never
consumes the generator it returns. Everything expensive about retrieval
(normalization, routing, the rewrite/raw union, the reserved-slot merge) runs
eagerly inside that call; only the Opus request lives in the lazy generator. So
we get the exact production context, byte for byte, for the price of retrieval.

Two of retrieval's steps do cost money, and both are memoized to disk:

  _route_docs          ~$0.0025 per call and explicitly NOT prompt-cacheable
                       (backend.py:242 — the title block sits under Haiku's
                       cache minimum). Its output depends only on the question,
                       the role, and the set of order titles — and none of our
                       fixes touch titles. So it is correct to pay once and
                       reuse it for every sweep of the night.
  _standalone_question temperature=0 typo repair, already gated by a free
                       vocabulary check, so only mangled questions pay at all.

With both memoized, the first sweep costs ~$1.7 and every later sweep in the
fix loop costs nothing. That is what buys an all-night loop.
"""
from __future__ import annotations

import hashlib
import json
import time

import anthropic

import backend
from night import config as C
from night.ledger import Ledger, cost_usd

ROUTE_MODEL = backend.REWRITE_MODEL
client = anthropic.Anthropic()


def _titles_hash(role: str) -> str:
    """Fingerprint of the routing input that is NOT the question.

    Keyed on the title block rather than on corpus mtime: the fix loop edits
    anchors and key-facts constantly, and none of that changes routing. Only a
    title change (a new or renamed order) should invalidate a cached route.
    """
    return hashlib.sha1(backend._titles_block(role).encode("utf-8")).hexdigest()[:12]


def _rkey(question: str, role: str) -> str:
    return f"{role}|{_titles_hash(role)}|{hashlib.sha1(question.encode('utf-8')).hexdigest()[:16]}"


class RouteCache:
    """Disk-backed memo for the router, installed over backend._route_docs."""

    def __init__(self, path=C.ROUTES):
        self.path = path
        self.data: dict[str, list[str]] = {}
        if path.exists():
            try:
                self.data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        self.misses = 0
        self._orig = backend._route_docs

    def save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)

    def install(self) -> None:
        """Monkeypatch the router. Deliberate: it keeps the production call
        path byte-identical while removing its repeat cost, which no amount of
        reimplementation could do without risking drift."""
        def cached(question: str, role: str) -> set[str]:
            k = _rkey(question, role)
            hit = self.data.get(k)
            if hit is None:
                self.misses += 1
                hit = sorted(self._orig(question, role))
                self.data[k] = hit
                if self.misses % 25 == 0:
                    self.save()
            return set(hit)
        backend._route_docs = cached

    # --- batched pre-warm ----------------------------------------------------

    def prewarm(self, rows: list[dict], ledger: Ledger) -> None:
        """Fill the cache with one Batch API pass — half price, and it turns
        every later router lookup in the night into a dict hit."""
        pending = [(r["q"], r["role"]) for r in rows if _rkey(r["q"], r["role"]) not in self.data]
        # the sweep also routes on the NORMALIZED question when it differs, so
        # those keys are filled lazily by `install()`; only the raw pass is
        # worth batching (it is the one that runs for every single question).
        uniq = {}
        for q, role in pending:
            uniq.setdefault(_rkey(q, role), (q, role))
        if not uniq:
            C.log("[sweep] router cache already warm")
            return

        est = len(uniq) * 0.00125          # ~2.4K in / 60 out at Haiku batch rates
        rid = ledger.reserve("router-prewarm", est)
        C.log(f"[sweep] routing {len(uniq)} questions via Batch API (~${est:.2f})")

        from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
        from anthropic.types.messages.batch_create_params import Request

        keys = list(uniq)
        reqs = [
            Request(
                custom_id=f"r{i}",
                params=MessageCreateParamsNonStreaming(
                    model=ROUTE_MODEL, max_tokens=60, temperature=0,
                    messages=[{"role": "user", "content": backend._ROUTE_PROMPT.format(
                        titles=backend._titles_block(uniq[k][1]), q=uniq[k][0])}],
                ),
            )
            for i, k in enumerate(keys)
        ]

        batch = client.messages.batches.create(requests=reqs)
        C.log(f"[sweep] batch {batch.id} submitted; polling")
        while True:
            b = client.messages.batches.retrieve(batch.id)
            if b.processing_status == "ended":
                break
            time.sleep(30)

        allowed_by_role = {
            role: {d["document_id"] for d in backend._docs_for_role(role) if d.get("document_id")}
            for role in C.ROLES
        }
        actual = 0.0
        for res in client.messages.batches.results(batch.id):
            if res.result.type != "succeeded":
                continue
            i = int(res.custom_id[1:])
            k = keys[i]
            role = uniq[k][1]
            msg = res.result.message
            raw = "".join(b.text for b in msg.content if b.type == "text")
            picked = {t.strip() for t in raw.replace("\n", ",").split(",") if t.strip()}
            # same guard as backend._route_docs: the model invents order numbers
            self.data[k] = sorted(picked & allowed_by_role[role])
            actual += cost_usd(ROUTE_MODEL, input_tokens=msg.usage.input_tokens,
                               output_tokens=msg.usage.output_tokens, batch=True)
        self.save()
        ledger.settle(rid, actual)
        C.log(f"[sweep] router cache warm: {len(self.data)} entries, ${actual:.2f}")


class NormalizeCache:
    """Memo for _standalone_question. temperature=0, so caching changes nothing
    observable — it only stops us paying twice for the same typo repair."""

    def __init__(self, path=C.OUT / "normalized.json"):
        self.path = path
        self.data: dict[str, str] = {}
        if path.exists():
            try:
                self.data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        self._orig = backend._standalone_question
        self.misses = 0

    def save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)

    def install(self) -> None:
        def cached(question: str, history):
            if history:                      # follow-ups are not part of this sweep
                return self._orig(question, history)
            if question not in self.data:
                self.misses += 1
                self.data[question] = self._orig(question, None)
                if self.misses % 25 == 0:
                    self.save()
            return self.data[question]
        backend._standalone_question = cached


# --- the sweep itself --------------------------------------------------------

def retrieval_for(question: str, role: str) -> dict:
    """Run the real production retrieval and report what it produced.

    `stream_ai_answer` does every retrieval step eagerly and defers only the
    Opus request into its generator, which we drop on the floor. Nothing is
    billed to Opus here.
    """
    _gen, sources, user_content, _usage = backend.stream_ai_answer(question, None, role, None)
    del _gen
    return {"sources": sources, "context": user_content}


def band(row: dict, top_score: float, target_hit: bool | None, thresholds: dict) -> str:
    """Green / yellow / red from the score distribution.

    Thresholds are calibrated against the golden set rather than guessed, so
    "green" means "this scores like a retrieval we already know answers
    correctly" instead of "this cleared a number someone typed."
    """
    if top_score >= thresholds["green"] and target_hit is not False:
        return C.BAND_GREEN
    if top_score < thresholds["red"]:
        return C.BAND_RED
    return C.BAND_YELLOW
