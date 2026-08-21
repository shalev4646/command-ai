"""Scenario anchors for the clauses the digest added.

The digest measured flat (2026-08-21): the enriched blocks did not move any of
the three sets, and the free instrument said why — the answering order enters
the production window in only 3 of 11 typical-set zeros. Content is not the
bottleneck; WINDOW ENTRY is. The one mechanism measured to move window entry is
scenario anchors (v95: four hand-written anchors put PM-35.0402 into the window
of three family-leave questions and the held-out set went 15->29/47).

This generates anchors AT SCALE for exactly the content the digest added: for
each digested order, the model sees the NEW clauses and the order's existing
anchor list, and writes scenario questions a soldier would type whose answers
are those clauses. Gates are night.anchors.check unchanged — mirror guard
against saved eval questions, duplicate guard, risk-topic guard — with the
mirror pool WIDENED to every measured question on disk (probe_*.jsonl), because
the fresh/held-out sets became benchmarks the moment they were measured, and an
anchor that mirrors a benchmark question is rewriting the test.

    python -m night.anchors_digest            # dry run: targets + cost
    python -m night.anchors_digest --apply    # paid, through the ledger
"""
from __future__ import annotations

import json
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from night import config as C
from night import anchors as A
from night.ledger import BudgetExceeded, Ledger, cost_usd

MODEL = "claude-opus-4-8"
DIGEST_ACCEPTED = C.OUT / "digest_accepted.jsonl"
ACCEPTED = C.OUT / "anchors_digest_accepted.jsonl"
EST_OUT_TOKENS = 1100

PROMPT = """לפניך פקודת מטכ"ל שסעיף „עיקרי הפקודה" שלה הורחב זה עתה בסעיפים חדשים,
ורשימת שאלות-העוגן הקיימות שלה. כתוב 6–12 שאלות-עוגן חדשות לאחזור — שאלות
שחייל, מפקד או מילואימניק אמיתי היה מקליד באפליקציה, ושהתשובה להן נמצאת
**בסעיפים החדשים** שלהלן.

כותרת הפקודה: {title}

הסעיפים החדשים (המקור היחיד לשאלות):
{new_clauses}

שאלות קיימות (אל תחזור עליהן ואל תנסח וריאציה שלהן):
{existing}

כללים מחייבים:
1. **רוב השאלות בניסוח תרחיש או פעולה בגוף ראשון** — „מה עושים כש…", „מותר לי…",
   „מי מאשר…", „תוך כמה זמן…", „מישהו עשה X, מה עכשיו". לכל היותר 2 שאלות הגדרה.
2. **רק שאלות שהסעיפים החדשים באמת עונים עליהן** — שאלה שמבטיחה מה שאין תגרום
   לאפליקציה להגיש תוכן לא רלוונטי. אל תבטיח.
3. אסור להעלות נושא שהסעיפים שותקים בו, ואסור מספרי-סעיפים או שם הפקודה בשאלה.
4. כל שאלה 5–15 מילים, בעברית של חייל (סלנג מקובל מותר: קב"ן, ת"ש, שמ"פ, גימלים)."""


def mirror_pool() -> list[str]:
    """Every measured question on disk — the widened mirror guard."""
    out = set(A.eval_questions())
    for p in C.OUT.glob("probe_*.jsonl"):
        for row in C.read_jsonl(p):
            q = row.get("q")
            if isinstance(q, str) and q.strip():
                out.add(q.strip())
    return sorted(out)


def targets() -> list[tuple[dict, list[dict]]]:
    import backend
    docs = {d["document_id"]: d for d in backend.load_documents() if d.get("document_id")}
    added: dict[str, list[dict]] = {}
    for row in C.read_jsonl(DIGEST_ACCEPTED):
        added.setdefault(row["doc_id"], []).extend(row.get("added") or [])
    out = []
    for did, clauses in added.items():
        doc = docs.get(did)
        if doc and clauses:
            out.append((doc, clauses))
    return sorted(out, key=lambda t: -len(t[1]))


def estimate(doc: dict, clauses: list[dict]) -> float:
    words = sum(len(str(c.get("text", "")).split()) + 8 for c in clauses) + 400
    return cost_usd(MODEL, input_tokens=int(words * 1.6) + 1200, output_tokens=EST_OUT_TOKENS)


def run(apply: bool) -> None:
    import backend
    from night.rehearse import doc_path
    from storage.vector_store import index_document

    todo = targets()
    evals = mirror_pool()
    total = sum(estimate(d, cl) for d, cl in todo)
    C.log(f"[anchors2] {len(todo)} digested orders, ~${total:.2f}; "
          f"{len(evals)} measured questions in the mirror guard")
    if not apply:
        for d, cl in todo:
            C.log(f"[anchors2]   {d['document_id']}: {len(cl)} new clauses, ~${estimate(d, cl):.3f}")
        C.log("[anchors2] dry run — nothing spent. Use --apply to execute.")
        return
    if not evals:
        raise SystemExit("[anchors2] refusing --apply: empty mirror guard")

    ledger = Ledger(C.LEDGER)
    done = kept_total = 0
    for doc, clauses in todo:
        did = doc["document_id"]
        new_block = "\n".join(f"- {c.get('number', '')}: {c.get('text', '')}" for c in clauses)
        existing = "\n".join(f"- {q}" for q in A._flat_questions(doc)) or "- (אין)"
        est = estimate(doc, clauses) * 1.3
        try:
            rid = ledger.reserve(f"anchors2:{did}", est)
        except BudgetExceeded as e:
            C.log(f"[anchors2] STOPPING — {e}")
            break
        try:
            r = backend.client.messages.create(
                model=MODEL, max_tokens=16000,
                thinking={"type": "adaptive"},
                output_config={"effort": "high",
                               "format": {"type": "json_schema", "schema": A.SCHEMA}},
                messages=[{"role": "user", "content": PROMPT.format(
                    title=doc.get("title", ""), new_clauses=new_block, existing=existing)}])
        except Exception as e:
            ledger.settle(rid, 0.0)
            C.log(f"[anchors2] {did} api error: {type(e).__name__}: {str(e)[:100]}")
            continue
        usd = cost_usd(MODEL, input_tokens=r.usage.input_tokens, output_tokens=r.usage.output_tokens)
        ledger.settle(rid, usd)
        try:
            parsed = json.loads("".join(b.text for b in r.content if b.type == "text"))
        except json.JSONDecodeError:
            C.log(f"[anchors2] {did} bad JSON ${usd:.3f}")
            continue
        kept, problems, warnings = A.check(parsed.get("questions") or [], doc, evals)
        # MIN_KEEP was tuned for whole-order anchor generation; here a small
        # clean set is fine — an order whose digest added 3 clauses cannot
        # honestly yield 6 anchors. Keep whatever survives the gates.
        if not kept:
            C.log(f"[anchors2] {did}: nothing survived ({len(warnings)} dropped) ${usd:.3f}")
            continue
        path = doc_path(did)
        on_disk = json.loads(path.read_text(encoding="utf-8"))
        on_disk["anchor_questions"] = list(on_disk.get("anchor_questions") or []) + kept
        path.write_text(json.dumps(on_disk, ensure_ascii=False, indent=2), encoding="utf-8")
        n = index_document(json.loads(path.read_text(encoding="utf-8")))
        done += 1
        kept_total += len(kept)
        C.append_jsonl(ACCEPTED, {"doc_id": did, "kept": kept, "dropped": warnings, "usd": usd})
        C.log(f"[anchors2] {did}: +{len(kept)} anchors ({len(warnings)} dropped) "
              f"{n} chunks ${usd:.3f} | spent ${ledger.spent:.2f}")
    C.log(f"[anchors2] done: {kept_total} anchors over {done} orders")


if __name__ == "__main__":
    run(apply="--apply" in sys.argv)
