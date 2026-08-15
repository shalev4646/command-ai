"""Generate `anchor_questions` for orders that have none.

Wave 1 entered its paired measurement with 6.8 anchors per order against 16.1
for veterans — 42% of the retrieval surface missing — and the measurement read
"no detectable change". MORNING.md declares that a zero result on an
anchor-less wave is lack-of-information, not evidence; wave 2 must not enter
measurement with the same disability, which is what this stage closes.

An anchor is a retrieval-only question string (storage/vector_store.py:332):
indexed as its own chunk, boosts the document's real chunks, never shown in the
UI and never used as answer content.

Four measured traps, each with a mechanical gate here:

  definitions-only   33.0145 carried 19 definition anchors and none matched
                     "מישהו דיווח לי, מה לעשות" — the prompt demands
                     action/scenario phrasings and the report counts them.
  over-promising     An anchor that promises what the key-facts block does not
                     cover lifts the document and serves the wrong section
                     (the 33.0304 colophon trap). The prompt shows the block
                     and only asks for questions it can answer.
  invented topics    Same RISK_TOPICS gate as curation: an anchor may not raise
                     a topic the order is silent about.
  benchmark mirror   Rewriting a saved eval question as an anchor is rewriting
                     the test (rejected explicitly on 2026-08-08). Any
                     candidate overlapping a saved question at Jaccard >= 0.55
                     over content stems is dropped.

Deliberately NOT wired into night.intake — a parallel session owns the intake
path right now; wiring is a one-line follow-up once reviewed. Also deliberately
inert by default: running with no flags prints the target list and the priced
estimate and spends nothing. Indexing changes retrieval, so `--apply` must
never run while a paired measurement is in flight on the other side.

    python -m night.anchors              # dry run: targets + cost, $0
    python -m night.anchors --apply      # paid run through the ledger
    python -m night.anchors --apply 5    # first 5 targets only
"""
from __future__ import annotations

import json
import re

from night import config as C
from night.ledger import BudgetExceeded, cost_usd

MODEL = "claude-opus-4-8"
MAX_RAW_WORDS = 9000            # matches curation's truncation
MIN_KEEP = 6                    # fewer surviving anchors than this -> retry
MAX_KEEP = 14
MIN_CHARS = 12                  # vector_store skips shorter strings silently
MIRROR_JACCARD = 0.55
EST_OUT_TOKENS = 900            # ~12 short questions + schema + thinking tail

ACCEPTED = C.OUT / "anchors_accepted.jsonl"
REJECTED = C.OUT / "anchors_rejected.jsonl"

# Curated and deliberately pulled (scrambled digits, unverifiable thresholds) —
# same standing exclusion as curation, kept in sync by the test.
NEVER = {"20.0502"}

PROMPT = """לפניך פקודת מטכ"ל: הטקסט הגולמי שלה וסעיף "עיקרי הפקודה" (key-facts) שנאצר ממנה.
כתוב עבורה 8–12 שאלות-עוגן לאחזור: שאלות שחייל או מפקד אמיתי היה מקליד באפליקציה,
ושהתשובה להן נמצאת בפקודה הזו.

כותרת הפקודה: {title}

סעיף עיקרי הפקודה (מה שיוגש למודל כשהפקודה מאותרת):
{block}

הטקסט הגולמי:
{raw}

כללים מחייבים:
1. **רוב השאלות בניסוח פעולה או תרחיש בגוף ראשון** — "מה עושים כש...", "מותר לי...",
   "מי צריך לאשר...", "כמה מגיע לי...", "מישהו עשה X, מה עכשיו". לכל היותר 2–3
   שאלות הגדרה ("מה זה...").
2. **רק שאלות שסעיף עיקרי-הפקודה או הטקסט הגולמי באמת עונים עליהן.** שאלה שהפקודה
   לא עונה עליה תגרום לאפליקציה למשוך את הפקודה הזו ולהגיש תוכן לא רלוונטי — נזק,
   לא עזרה. אל תבטיח מה שאין.
3. אסור להעלות נושא שהפקודה שותקת בו.
4. כל שאלה 5–15 מילים, בעברית של חייל (מותר סלנג צבאי מקובל), בלי מספרי סעיפים
   ובלי שם הפקודה בתוך השאלה.
5. אל תחזור על השאלות הקיימות של הפקודה, המצורפות כאן:
{existing}
"""

RETRY_SUFFIX = """

הניסיון הקודם שלך נדחה בבדיקה אוטומטית. הבעיות:
{problems}

כתוב מחדש את כל הרשימה. הקפד: שאלות שהפקודה באמת עונה עליהן, בניסוח פעולה,
בלי לחזור על שאלות קיימות."""

SCHEMA = {
    "type": "object",
    "properties": {"questions": {"type": "array", "items": {"type": "string"}}},
    "required": ["questions"],
    "additionalProperties": False,
}


def _stems(text: str) -> set[str]:
    """Content stems for overlap tests — curation's normalizer, lazily bound.

    night.curate imports backend at module level; binding at call time keeps
    the gates importable (and testable) without the app environment.
    """
    from night.curate import _norm
    return _norm(text)


def _flat_questions(doc: dict) -> list[str]:
    """Existing question strings in either storage format (flat or per-role)."""
    sq = doc.get("suggested_questions")
    lists = [qs for qs in sq.values() if isinstance(qs, list)] \
        if isinstance(sq, dict) else [sq or []]
    lists = lists + [doc.get("anchor_questions") or []]
    return [q for qs in lists for q in qs if isinstance(q, str)]


def eval_questions() -> list[str]:
    """Saved benchmark questions — anchors must not be rewrites of these.

    Sources are best-effort: a missing file contributes nothing rather than
    failing, but the caller logs how many were loaded so an empty guard is
    visible instead of silent.
    """
    out: list[str] = []
    for name in ("grades_baseline.jsonl", "probe_base30.jsonl",
                 "probe_remeasure.jsonl"):
        for row in C.read_jsonl(C.OUT / name):
            q = row.get("q")
            if isinstance(q, str) and q.strip():
                out.append(q.strip())
    return sorted(set(out))


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def check(questions: list[str], doc: dict, eval_qs: list[str]
          ) -> tuple[list[str], list[str], list[str]]:
    """Mechanical gates. Returns (kept, problems, warnings).

    Per-question failures drop the question and record a warning; `problems`
    is non-empty only when the surviving set is too small to accept, so one
    bad candidate costs itself and not the whole batch.
    """
    from night.curate import RISK_TOPICS
    raw = str(doc.get("raw_text", ""))
    block = " ".join(
        str(cl.get("number", "")) + " " + str(cl.get("text", ""))
        for s in doc.get("sections") or [] if s.get("id") == "key-facts"
        for cl in s.get("clauses", []))
    existing = [_stems(q) for q in _flat_questions(doc)]
    evals = [_stems(q) for q in eval_qs]

    kept: list[str] = []
    kept_stems: list[set[str]] = []
    warnings: list[str] = []
    for q in questions:
        if not isinstance(q, str) or len(q.strip()) < MIN_CHARS:
            warnings.append(f"dropped (short): {q!r}")
            continue
        q = " ".join(q.split())
        st = _stems(q)
        if any(_jaccard(st, e) >= MIRROR_JACCARD for e in evals):
            warnings.append(f"dropped (mirrors a saved eval question): {q!r}")
            continue
        if any(_jaccard(st, e) >= 0.8 for e in existing + kept_stems):
            warnings.append(f"dropped (duplicate of an existing question): {q!r}")
            continue
        topic = next((label for label, terms in RISK_TOPICS.items()
                      if any(t in q for t in terms)
                      and not any(t in raw or t in block for t in terms)), None)
        if topic:
            warnings.append(f"dropped (raises {topic!r}, order is silent): {q!r}")
            continue
        kept.append(q)
        kept_stems.append(st)

    problems: list[str] = []
    if len(kept) < MIN_KEEP:
        problems.append(f"only {len(kept)} anchors survived the gates "
                        f"(need {MIN_KEEP}); dropped: {len(questions) - len(kept)}")
    return kept[:MAX_KEEP], problems, warnings


def targets(docs: list[dict]) -> list[dict]:
    """Orders that need anchors and can safely take them.

    Sectionless orders are excluded on purpose: an anchor lift hands the model
    the merged key-facts block, so anchoring an order without one recreates
    the liftable-but-empty trap the curation stage exists to close.
    """
    out = []
    for d in docs:
        did = d.get("document_id")
        if not did or did in NEVER:
            continue
        if d.get("anchor_questions"):
            continue
        if not any(s.get("id") == "key-facts" for s in d.get("sections") or []):
            continue
        out.append(d)
    return sorted(out, key=lambda d: d["document_id"])


def estimate_usd(doc: dict, *, batch: bool = False) -> float:
    words = len(str(doc.get("raw_text", "")).split()[:MAX_RAW_WORDS])
    return cost_usd(MODEL,
                    input_tokens=int(words * 1.6) + 1200,
                    output_tokens=EST_OUT_TOKENS, batch=batch)


def generate_one(doc: dict, ledger, eval_qs: list[str],
                 problems: list[str] | None = None
                 ) -> tuple[list[str], float, list[str], list[str]]:
    """One paid call plus gates. Returns (kept, usd, problems, warnings)."""
    import backend
    raw = " ".join(str(doc.get("raw_text", "")).split()[:MAX_RAW_WORDS])
    block = "\n".join(
        f"- {cl.get('number', '')}: {cl.get('text', '')}"
        for s in doc.get("sections") or [] if s.get("id") == "key-facts"
        for cl in s.get("clauses", []))
    existing = "\n".join(f"- {q}" for q in _flat_questions(doc)) or "- (אין)"
    rid = ledger.reserve(f"anchors:{doc['document_id']}",
                         estimate_usd(doc) * 1.3)
    try:
        r = backend.client.messages.create(
            model=MODEL, max_tokens=16000,
            thinking={"type": "adaptive"},
            output_config={"effort": "high",
                           "format": {"type": "json_schema", "schema": SCHEMA}},
            messages=[{"role": "user", "content": PROMPT.format(
                title=doc.get("title", ""), block=block or "(אין)", raw=raw,
                existing=existing)
                + (RETRY_SUFFIX.format(
                    problems="\n".join(f"- {p}" for p in problems))
                   if problems else "")}],
        )
    except Exception as e:
        ledger.settle(rid, 0.0)
        return [], 0.0, [f"api error: {type(e).__name__}: {e}"], []
    usd = cost_usd(MODEL, input_tokens=r.usage.input_tokens,
                   output_tokens=r.usage.output_tokens)
    ledger.settle(rid, usd)
    if r.stop_reason == "max_tokens":
        return [], usd, ["hit max_tokens"], []
    try:
        parsed = json.loads("".join(b.text for b in r.content if b.type == "text"))
    except json.JSONDecodeError as e:
        return [], usd, [f"bad JSON despite schema: {e}"], []
    kept, probs, warns = check(parsed.get("questions") or [], doc, eval_qs)
    return kept, usd, probs, warns


ACTION_HINTS = ("מה עושים", "מותר ל", "מי מאשר", "מי צריך", "כמה מגיע",
                "מה קורה", "איך מ", "האם אני", "מה עכשיו", "חייב ל")


def run(limit: int | None = None, *, apply: bool = False) -> None:
    import backend
    todo = targets(backend.load_documents())
    if limit:
        todo = todo[:limit]
    evals = eval_questions()
    sync = sum(estimate_usd(d) for d in todo)
    batch = sum(estimate_usd(d, batch=True) for d in todo)
    C.log(f"[anchors] {len(todo)} orders lack anchor_questions; "
          f"~${sync:.2f} sync / ~${batch:.2f} batch; "
          f"{len(evals)} saved eval questions loaded as mirror-guard")
    if not apply:
        for d in todo:
            C.log(f"[anchors]   would run {d['document_id']} "
                  f"(~${estimate_usd(d):.3f})")
        C.log("[anchors] dry run — nothing spent. Use --apply to execute.")
        return
    if not evals:
        # an empty mirror-guard silently re-legalises rewriting the benchmark
        raise SystemExit("[anchors] refusing --apply: no saved eval questions "
                         "found to guard against benchmark mirroring")

    from night.ledger import Ledger
    from night.rehearse import doc_path
    from storage.vector_store import index_document
    ledger = Ledger(C.LEDGER)
    done = failed = 0
    for i, doc in enumerate(todo, 1):
        did = doc["document_id"]
        spent = 0.0
        try:
            kept, usd, probs, warns = generate_one(doc, ledger, evals)
            spent += usd
            if probs:
                C.log(f"[anchors] {i}/{len(todo)} {did} retry: "
                      f"{'; '.join(probs)[:140]}")
                kept, usd, probs, warns = generate_one(doc, ledger, evals, probs)
                spent += usd
        except BudgetExceeded as e:
            C.log(f"[anchors] STOPPING — {e}")
            break
        if probs or not kept:
            failed += 1
            C.log(f"[anchors] {i}/{len(todo)} {did} REJECTED (${spent:.3f}): "
                  f"{'; '.join(probs)[:200]}")
            C.append_jsonl(REJECTED, {"doc_id": did, "problems": probs,
                                      "warnings": warns, "usd": spent})
            continue
        path = doc_path(did)
        raw_doc = json.loads(path.read_text(encoding="utf-8"))
        raw_doc["anchor_questions"] = kept
        path.write_text(json.dumps(raw_doc, ensure_ascii=False, indent=2),
                        encoding="utf-8")
        n = index_document(json.loads(path.read_text(encoding="utf-8")))
        action = sum(1 for q in kept if any(h in q for h in ACTION_HINTS))
        done += 1
        C.log(f"[anchors] {i}/{len(todo)} {did} OK  {len(kept)} anchors "
              f"({action} action-phrased), {n} chunks, ${spent:.3f} "
              f"| spent ${ledger.spent:.2f}")
        C.append_jsonl(ACCEPTED, {"doc_id": did, "anchors": kept,
                                  "warnings": warns, "usd": spent})
    C.log(f"[anchors] done: {done} accepted, {failed} rejected")


if __name__ == "__main__":
    import sys
    args = [a for a in sys.argv[1:]]
    apply = "--apply" in args
    nums = [a for a in args if a.isdigit()]
    run(int(nums[0]) if nums else None, apply=apply)
