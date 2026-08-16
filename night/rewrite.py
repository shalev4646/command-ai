"""Does a model rewriting the question BEFORE retrieval rescue the failing cases?

This is the direction `night.expand` left open. Pseudo-relevance feedback was
measured and rejected there: it assumes the first pass is roughly right and only
needs sharpening, and in exactly the cases that fail the first pass is wholly
wrong, so expanding with what came back drives the query further from the order
it needed (top-3 387 -> 349-357).

A rewrite does not depend on a first pass. The measured failure is vocabulary: a
soldier types "שעות מוקדשות" where the order says "קיצור שעות פעילות", and the
two do not meet in embedding space. Of the gate's 28 genuine failures, 23 sit at
rank 4-5 with a median score gap of 0.008 — the target is present and just
below, which is the shape a better query can move and a better ranker cannot.

Two settings, because the interesting question is whether grounding is what
matters:

    bare      Haiku is asked to restate the question in the register of IDF
              general staff orders, with nothing but the question.
    titles    the same, with the 289 order titles in the prompt (cached), so
              the rewrite can only reach for vocabulary the corpus actually has.

`bare` is the one to distrust. Nothing stops it from inventing an official-
sounding phrase that appears nowhere, which is the same failure as expansion
with extra steps. `titles` cannot fix a word the titles do not cover either, but
it can only be wrong within the corpus's own vocabulary.

Measured against the same 415 gate cases and the same baseline as expand, with
the router bypassed, so the numbers are comparable line for line:

    baseline                top-3 387/415   delivered 410/415

Nothing here is wired into production. `backend._standalone_question` already
does typo repair on the same call site; if a setting wins, that is where it
goes, behind the same `has_unknown_terms` gate.

    python -m night.rewrite            # measure the `titles` setting
    python -m night.rewrite --tune     # both settings
    python -m night.rewrite --failures # only the cases the gate fails today
"""
from __future__ import annotations

import json
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import backend
from night import config as C
from night.gate import load_cases
from night.ledger import Ledger, cost_usd

MODEL = "claude-haiku-4-5"
CACHE = C.OUT / "rewrites.json"

_TOOL = {
    "name": "save_query",
    "description": "שמור את השאילתה המנוסחת מחדש",
    "input_schema": {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    },
}

BARE = """נסח מחדש את שאלת החייל הבאה כשאילתת חיפוש בלשון פקודות מטכ"ל.

השאלה: {q}

פקודות מטכ"ל כתובות בעברית משפטית-צבאית: „היעדרות מן השירות שלא ברשות", „קיצור
שעות פעילות", „תשלום דמי כלכלה". חייל שואל בלשון יומיומית. תפקידך לגשר.

⚠ אל תענה על השאלה ואל תוסיף מידע. החזר ניסוח קצר של אותה שאלה בלבד.
⚠ אל תמציא מספרי פקודות ואל תנחש שמות של פקודות."""

TITLED = """נסח מחדש את שאלת החייל הבאה כשאילתת חיפוש בלשון פקודות מטכ"ל.

השאלה: {q}

⚠ אל תענה על השאלה ואל תוסיף מידע. החזר ניסוח קצר של אותה שאלה בלבד.
⚠ השתמש רק במונחים שמופיעים בשמות הפקודות שלהלן או בעברית כללית — אל תמציא
מונח רשמי שאינו שם. אל תמציא מספרי פקודות."""

_TITLES_HEADER = "שמות הפקודות הקיימות במאגר:\n"


def _titles() -> str:
    seen, out = set(), []
    for d in backend.load_documents():
        t = (d.get("title") or "").strip()
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return _TITLES_HEADER + "\n".join(out)


def rewrite(q: str, setting: str, ledger: Ledger | None = None) -> tuple[str, float]:
    """Rewritten query and what it cost. Falls back to the original on any error.

    Cached to disk keyed by (setting, question): a sweep gets re-run while the
    knobs move, and paying twice for the same rewrite of the same question buys
    nothing. The cache is also what makes a failure post-mortem free.
    """
    cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
    key = f"{setting}::{q}"
    if key in cache:
        return cache[key], 0.0

    kw = dict(model=MODEL, max_tokens=200, temperature=0, tools=[_TOOL],
              tool_choice={"type": "tool", "name": "save_query"})
    if setting == "titles":
        # the titles block is identical for every question in the sweep, so it
        # is written to the cache once and read at a tenth of the price 414 times
        kw["system"] = [{"type": "text", "text": _titles(),
                         "cache_control": {"type": "ephemeral"}}]
        prompt = TITLED.format(q=q)
    else:
        prompt = BARE.format(q=q)

    try:
        r = backend.client.with_options(timeout=15.0, max_retries=1).messages.create(
            messages=[{"role": "user", "content": prompt}], **kw)
    except Exception as e:
        # In production this falls back to the raw question — degraded, never
        # broken. In a MEASUREMENT that same fallback is a lie: the first sweep
        # here lost all 387 live calls to APIConnectionError, scored the raw
        # question 387 times, and reported "top-3 409/415, lost 0" — a perfect
        # result produced by never running the thing under test. The caller has
        # to be able to tell "rewritten and no worse" from "never rewritten",
        # so failure is signalled rather than swallowed.
        C.log(f"[rewrite] {type(e).__name__} on {q[:40]!r}")
        return None, 0.0

    u = r.usage
    usd = cost_usd(MODEL, input_tokens=u.input_tokens, output_tokens=u.output_tokens,
                   cache_write_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0,
                   cache_read_tokens=getattr(u, "cache_read_input_tokens", 0) or 0)
    out = q
    for block in r.content:
        if block.type == "tool_use":
            got = str(block.input.get("query", "")).strip()
            if got:
                out = got
    cache[key] = out
    CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
    return out, usd


def top_docs(chunks: list[dict]) -> list[str]:
    out: list[str] = []
    for c in chunks:
        if c["doc_id"] not in out:
            out.append(c["doc_id"])
    return out


def main(tune: bool = False, only_failures: bool = False) -> None:
    ledger = Ledger(C.LEDGER)
    cases = []
    base_t3 = base_deliv = 0
    for role, q, exp, tag in load_cases():
        acc = tuple(exp) if isinstance(exp, (list, tuple)) else (exp,)
        docs = top_docs(backend.retrieve_for_role(q, role, route=set()))
        hit3 = any(e in docs[:3] for e in acc)
        base_t3 += hit3
        base_deliv += any(e in docs for e in acc)
        cases.append((role, q, acc, tag, hit3, docs))

    if only_failures:
        # The cheap read on whether this is worth a full sweep: it can only gain
        # here, so a setting that cannot move these does not need 415 calls to
        # be rejected. It says nothing about what it BREAKS — that needs the
        # full set, and breakage is how expansion lost.
        cases = [c for c in cases if not c[4]]
        C.log(f"[rewrite] failures-only: {len(cases)} cases the gate fails today")

    n = len(cases)
    print(f"baseline: top-3 {base_t3}/{n if only_failures else len(cases)}  "
          f"delivered {base_deliv}/415")

    for setting in (("bare", "titles") if tune else ("titles",)):
        t3 = deliv = gained = lost = failed = 0
        spent = 0.0
        for i, (role, q, acc, _tag, base3, _docs) in enumerate(cases):
            rq, usd = rewrite(q, setting, ledger)
            spent += usd
            if rq is None:
                failed += 1
                # Bail early rather than burn the sweep: a run that cannot reach
                # the API has no number to report, and continuing only buys a
                # convincing-looking one.
                if failed >= 5 and failed > i // 4:
                    print(f"  {setting:<8} -> ABORTED after {failed} failed "
                          f"rewrites in {i + 1} cases. No result. (${spent:.2f})")
                    break
                continue
            docs = top_docs(backend.retrieve_for_role(rq, role, route=set()))
            hit3 = any(e in docs[:3] for e in acc)
            t3 += hit3
            deliv += any(e in docs for e in acc)
            gained += hit3 and not base3
            lost += base3 and not hit3
        else:
            scored = n - failed
            print(f"  {setting:<8} -> top-3 {t3}/{scored}  delivered {deliv}/{scored}"
                  f"  gained {gained}, lost {lost}   (${spent:.2f})"
                  + (f"   ⚠ {failed} rewrites failed and are excluded"
                     if failed else ""))


if __name__ == "__main__":
    main("--tune" in sys.argv, "--failures" in sys.argv)
