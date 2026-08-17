"""Does the context we serve the model actually contain the text that answers
the question? Measured for free, with no API call anywhere in the loop.

Why this exists
---------------
Five paid re-measures ran on 2026-08-16 and bought noise: the frozen-30 harness
has a ±3-part noise floor at n=30, and every retrieval change we wanted to try
is smaller than that at first attempt. Paying $1.20 to learn "within noise" is
not a measurement, it is a coin flip with a receipt.

The adjudication gives us something the grader cannot: for a set of questions
that scored zero, a human-checked verbatim quote from an order's raw_text that
answers them. That turns a fuzzy question ("did the answer get better?") into a
deterministic one ("was the answering sentence in the 8 chunks we served?").
It is free, it is repeatable, and it fails loudly — a retrieval change either
puts the sentence in front of the model or it does not.

What it does NOT claim
----------------------
Serving the answering sentence is necessary, not sufficient: the model can
still fail to use it. This instrument is the inner loop, and a paired
re-measure remains the outer one. It also inherits the adjudication's own
sample — questions that already failed — so `served` here is a floor on the
retrieval gap, never an estimate of overall answer quality.

Router bypassed, same convention as `night.gate`: production adds a +0.05
routing bonus, so a hit here is strictly stronger evidence than a hit there,
and the loop costs nothing.

    python -m night.evidence --build   # rebuild targets from the adjudication
    python -m night.evidence           # measure the current retrieval
"""
from __future__ import annotations

import json
import re
import sys
import unicodedata

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import backend
from night import config as C

TARGETS = C.OUT / "evidence_targets.json"
RESULT = C.OUT / "evidence.json"
ROUTE_CACHE = C.OUT / "route_cache.json"

# A verbatim span shorter than this is not evidence, it is a common phrase:
# "חייל שלא קיים פקודה" is 4 words and appears in a dozen orders. Eight words
# of exact Hebrew is specific enough that a match means the adjudicator copied
# it out of that document.
MIN_SPAN_WORDS = 8
# Paraphrase credit: a curated key-facts clause restates the source rather than
# quoting it, so a strict substring test scores it zero even when the soldier
# would have got the answer. Content-word recall of the span against a single
# served chunk, at this threshold, is the "covered in substance" reading.
COVER_RECALL = 0.60

_DIACRITICS = re.compile(r"[֑-ׇ]")
_QUOTES = str.maketrans({"״": '"', "”": '"', "“": '"', "׳": "'", "’": "'", "‘": "'"})
_DOC_ID = re.compile(r"(?:PM-)?\d{2}\.\d{4}")


def norm(s: str) -> str:
    """Whitespace, quote-glyph and niqqud normalization — nothing else.

    The adjudicators wrote their quotes "whitespace normalized from the OCR",
    and the corpus mixes ״ with " freely, so both sides have to be flattened
    the same way or every comparison is a false negative. Deliberately does
    NOT strip prefixes or fold finals: `has_unknown_terms` was broken for
    months by exactly that kind of clever normalization turning כשורה into
    שורה and declaring the typo understood.
    """
    s = unicodedata.normalize("NFKC", str(s or ""))
    s = _DIACRITICS.sub("", s.translate(_QUOTES))
    return re.sub(r"\s+", " ", s).strip()


def content_words(s: str) -> set[str]:
    """Words of 3+ characters — Hebrew has no stop-word list worth the risk of
    dropping a word that carries the answer (לא, אין, עד are all decisive)."""
    return {w for w in re.findall(r"[\w\"']+", norm(s)) if len(w) >= 3}


# --- building targets from the adjudication ----------------------------------

def verbatim_spans(evidence: str, raw: str, min_words: int = MIN_SPAN_WORDS) -> list[str]:
    """The maximal word-runs of `evidence` that appear verbatim in `raw`.

    The adjudication evidence is English prose with Hebrew quotations embedded,
    and the quotation marks cannot be parsed: Hebrew gershayim sit *inside*
    words (פ"מ, מטכ"ל, חד"ש), so any quote-pairing heuristic splits words in
    half. Matching against the source instead sidesteps the punctuation
    entirely — whatever the adjudicator copied out of the document is, by
    definition, findable in the document.
    """
    raw_n = norm(raw)
    words = norm(evidence).split()
    spans, i = [], 0
    while i < len(words):
        if words[i] not in raw_n:      # English prose skips at one find() each
            i += 1
            continue
        best, j = None, i + 1
        while j <= len(words):
            cand = " ".join(words[i:j])
            if cand not in raw_n:
                break
            best, j = cand, j + 1
        if best and len(best.split()) >= min_words:
            spans.append(best)
            i += len(best.split())
        else:
            i += 1
    return spans


def _question_index() -> dict[str, tuple[str, str]]:
    """normalized question -> (role, question as measured).

    Roles come from the probe rows rather than from the adjudication text: the
    adjudicator wrote "(role: commander — ...)" as a note to a human, and the
    measured question is the one the harness will re-ask.
    """
    index: dict[str, tuple[str, str]] = {}
    for name in ("probe_remeasure6.jsonl", "probe_baseline.jsonl",
                 "probe_heldout_after.jsonl"):
        for row in C.read_jsonl(C.OUT / name):
            q = row.get("q") or ""
            if q:
                index.setdefault(norm(q), (row.get("role") or "soldier", q))
    return index


def _match_question(text: str, index: dict[str, tuple[str, str]]) -> tuple[str, str] | None:
    """Find the measured question behind an adjudication heading.

    Headings drift from the harness text — a parenthetical role note is
    appended, a dash becomes a hyphen — so an exact lookup finds maybe half.
    The fallback scores CONTAINMENT of the measured question in the heading,
    not similarity between them: an adjudicator's heading is the question plus
    their own gloss, so a symmetric ratio punishes the right answer for the
    gloss's length (it scored the correct pairing 0.58 and dropped it).
    """
    stripped = norm(re.sub(r"\(role:.*?\)", "", text, flags=re.I))
    if stripped in index:
        return index[stripped]
    want = content_words(stripped)
    if not want:
        return None
    scored = []
    for key, value in index.items():
        have = content_words(key)
        if have:
            scored.append((len(want & have) / len(have), value))
    scored.sort(key=lambda s: s[0], reverse=True)
    if not scored or scored[0][0] < 0.85:
        return None
    # a near-tie means the heading matches two measured questions equally well
    # and picking either would attach the evidence to the wrong one
    if len(scored) > 1 and scored[1][0] > scored[0][0] - 0.05:
        return None
    return scored[0][1]


def build() -> list[dict]:
    """Targets = (question, role, order, verbatim spans that answer it).

    Two sources, both human-checked: `night.deepen.TARGETS`, whose quotes were
    read out of raw_text by hand, and the adjudication batches, whose evidence
    a second agent challenged claim-by-claim. Only spans that still locate in
    the current corpus survive — a span that no longer matches is dropped and
    logged, never guessed at, exactly as deepen.py drops a quote it cannot find.
    """
    docs = {d["document_id"]: d for d in backend.load_documents() if d.get("document_id")}
    index = _question_index()
    # (question, evidence blobs, order if already known, minimum span length).
    # The 8-word floor guards span EXTRACTION from prose, where a common phrase
    # would otherwise attach the evidence to the wrong order. deepen's quotes
    # need no such guard: a human read them out of a named order, so the order
    # is given and the only question is whether the sentence is served. Holding
    # them to 8 words dropped four of them ("חייל שלא קיים פקודה שניתנה לו" is
    # six words and is exactly the clause at issue).
    raw_targets: list[tuple[str, list[str], str | None, int]] = []

    from night.deepen import TARGETS as DEEPEN
    for doc_id, items in DEEPEN.items():
        for question, quotes in items:
            if quotes:
                raw_targets.append((question, quotes, doc_id, 4))

    adjudication = json.loads((C.OUT / "adjudication.json").read_text(encoding="utf-8"))
    for key in ("batch1", "batch2"):
        for row in adjudication.get(key, []):
            if row.get("verdict") != "PRESENT_NOT_RETRIEVED":
                continue
            raw_targets.append((row.get("question", ""), [row.get("evidence", "")], None, MIN_SPAN_WORDS))
    for row in adjudication.get("challenges", []):
        if row.get("holds"):
            raw_targets.append((row.get("question", ""), [row.get("reason", "")], None, MIN_SPAN_WORDS))

    built: dict[tuple[str, str], dict] = {}
    for question, quotes, doc_hint, min_words in raw_targets:
        matched = _match_question(question, index)
        if not matched:
            C.log(f"[evidence] no measured question for {question[:56]!r} — skipped")
            continue
        role, measured = matched
        blob = "\n".join(quotes)
        candidates = [doc_hint] if doc_hint else _DOC_ID.findall(blob)
        scored: list[tuple[int, str, list[str]]] = []
        for cand in dict.fromkeys(c for c in candidates if c in docs):
            spans = verbatim_spans(blob, docs[cand].get("raw_text", ""), min_words)
            if spans:
                scored.append((sum(len(s.split()) for s in spans), cand, spans))
        if not scored:
            C.log(f"[evidence] no locatable span for {question[:56]!r} — skipped")
            continue
        scored.sort(reverse=True)
        _, doc_id, spans = scored[0]
        entry = built.setdefault((norm(measured), doc_id), {
            "question": measured, "role": role, "doc_id": doc_id, "spans": []})
        for span in spans:
            if span not in entry["spans"]:
                entry["spans"].append(span)

    targets = sorted(built.values(), key=lambda t: (t["doc_id"], t["question"]))
    TARGETS.write_text(json.dumps(targets, ensure_ascii=False, indent=1), encoding="utf-8")
    docs_hit = len({t["doc_id"] for t in targets})
    spans_n = sum(len(t["spans"]) for t in targets)
    C.log(f"[evidence] built {len(targets)} targets over {docs_hit} orders, {spans_n} spans")
    return targets


# --- measuring the current retrieval -----------------------------------------

def routes(targets: list[dict], use_router: bool) -> dict[str, set[str]]:
    """Cached router verdicts, so the inner loop stays free after the first run.

    The router is a Haiku call at ~$0.0025 with no cache of its own, which is
    why `night.gate` bypasses it. Bypassing is wrong HERE: routing is what
    decides whether the answering order enters the window at all, and the
    first free run showed the gap (4 of 16 orders retrieved without it). Its
    verdict depends only on the question and the title block, so it is cached
    against the corpus size and paid for once — $0.04 for the whole set, then
    zero for every iteration after.

    Booked at the estimate, not the actual: `_route_docs` returns ids, not
    usage. That is the known over-charging gap documented in `night.ledger`.
    """
    if not use_router:
        return {}
    from night.ledger import Ledger, BudgetExceeded
    stamp = str(len(backend.load_documents()))
    cache = {}
    if ROUTE_CACHE.exists():
        stored = json.loads(ROUTE_CACHE.read_text(encoding="utf-8"))
        if stored.get("corpus") == stamp:
            cache = stored.get("routes", {})
    missing = [t for t in targets if f'{t["role"]}|{norm(t["question"])}' not in cache]
    if missing:
        ledger = Ledger(C.LEDGER)
        est = 0.0025 * len(missing)
        try:
            rid = ledger.reserve("evidence:route", est)
        except BudgetExceeded as e:
            C.log(f"[evidence] router skipped — {e}")
            return {}
        for t in missing:
            cache[f'{t["role"]}|{norm(t["question"])}'] = sorted(
                backend._route_docs(t["question"], t["role"]))
        ledger.settle(rid, est)
        ROUTE_CACHE.write_text(json.dumps({"corpus": stamp, "routes": cache},
                                          ensure_ascii=False, indent=1), encoding="utf-8")
        C.log(f"[evidence] routed {len(missing)} questions, ~${est:.3f} "
              f"(cached for every later run)")
    return {k: set(v) for k, v in cache.items()}


def measure(tag: str = "", use_router: bool = True) -> dict:
    """For each target: where the order ranks, and whether the answering text
    is in the context we would actually hand the model."""
    if not TARGETS.exists():
        build()
    targets = json.loads(TARGETS.read_text(encoding="utf-8"))
    route_by_q = routes(targets, use_router)

    rows, served_spans, total_spans = [], 0, 0
    doc_top1 = doc_any = covered_spans = 0
    for t in targets:
        route = route_by_q.get(f'{t["role"]}|{norm(t["question"])}', set())
        chunks = backend.retrieve_for_role(t["question"], t["role"], route=route)
        order: list[str] = []
        for c in chunks:
            if c["doc_id"] not in order:
                order.append(c["doc_id"])
        rank = order.index(t["doc_id"]) + 1 if t["doc_id"] in order else None
        context = norm(backend._context_from_chunks(chunks))
        chunk_words = [content_words(c["text"]) for c in chunks]

        spans = []
        for span in t["spans"]:
            want = content_words(span)
            recall = max((len(want & cw) / len(want) for cw in chunk_words), default=0.0) if want else 0.0
            hit = span in context
            spans.append({"span": span[:90], "served": hit, "recall": round(recall, 2)})
            total_spans += 1
            served_spans += hit
            covered_spans += (hit or recall >= COVER_RECALL)

        rows.append({
            "question": t["question"], "role": t["role"], "doc_id": t["doc_id"],
            "rank": rank, "docs": order,
            "served": sum(s["served"] for s in spans), "of": len(spans),
            "best_recall": round(max((s["recall"] for s in spans), default=0.0), 2),
            "spans": spans,
        })
        doc_any += rank is not None
        doc_top1 += rank == 1

    result = {
        "tag": tag, "targets": len(targets), "router": use_router,
        "doc_retrieved": doc_any, "doc_top1": doc_top1,
        "spans_total": total_spans, "spans_served": served_spans,
        "spans_covered": covered_spans,
        "questions_with_a_served_span": sum(1 for r in rows if r["served"]),
        "rows": rows,
    }
    path = C.OUT / (f"evidence_{tag}.json" if tag else "evidence.json")
    path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")

    C.log(f"[evidence] {tag or 'current'}: {len(targets)} targets")
    C.log(f"[evidence]   order retrieved at all  {doc_any}/{len(targets)}"
          f"   (top-1: {doc_top1})")
    C.log(f"[evidence]   answering text SERVED   {served_spans}/{total_spans} spans"
          f"   ({result['questions_with_a_served_span']}/{len(targets)} questions)")
    C.log(f"[evidence]   served or paraphrased   {covered_spans}/{total_spans} spans"
          f"   (recall >= {COVER_RECALL})")
    return result


if __name__ == "__main__":
    if "--build" in sys.argv:
        build()
    else:
        tag = ""
        for i, a in enumerate(sys.argv):
            if a == "--tag" and i + 1 < len(sys.argv):
                tag = sys.argv[i + 1]
        r = measure(tag, use_router="--no-router" not in sys.argv)
        for row in sorted(r["rows"], key=lambda x: (x["served"], x["best_recall"])):
            mark = "✓" if row["served"] else ("~" if row["best_recall"] >= COVER_RECALL else "✗")
            C.log(f"[evidence] {mark} rank={row['rank']} served={row['served']}/{row['of']} "
                  f"recall={row['best_recall']} {row['doc_id']} {row['question'][:52]}")
