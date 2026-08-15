"""Drop key-facts clauses that were truncated on their way out of the model.

Hebrew abbreviations carry a gershayim — צה"ל, יו"ר, אמל"ח, רמחב"ט — and a
clause that ends at one leaves behind "צה", "יו", "אמל". Structured output was
meant to make that impossible and mostly does; it still happened in 5 of 115
Haiku blocks and 1 of 98 Opus ones.

The faithfulness gates could not see it. Vocabulary grounding compares a
clause's words against the order's, and a two-word clause has almost nothing to
be ungrounded about, so it passes cleanly. The citation gate has nothing to
check. A fragment therefore reaches a soldier as a real answer to a real
question — "מי יושב בוועדה לסיווג אמל\"ח" answered with the single word "יו".

curate.py now rejects these at the gate. This removes the ones already written.
The clause goes rather than the block: the rest of the block is sound, and a
missing entry is a gap while a fragment is a wrong answer.

    python -m night.prune            # report
    python -m night.prune --apply    # rewrite storage/json_store
"""
from __future__ import annotations

import json
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import backend
from night.curate import MIN_CLAUSE_WORDS
from night.rehearse import doc_path


def truncated(doc: dict) -> list[tuple[int, str, str]]:
    out = []
    for s in doc.get("sections") or []:
        for i, cl in enumerate(s.get("clauses", [])):
            txt = str(cl.get("text", "")).strip()
            if len(txt.split()) < MIN_CLAUSE_WORDS:
                out.append((i, str(cl.get("number", "")), txt))
    return out


def main(apply: bool = False) -> None:
    docs = backend.load_documents()
    dropped = touched = emptied = 0
    for doc in docs:
        hits = truncated(doc)
        if not hits:
            continue
        touched += 1
        dropped += len(hits)
        for _i, label, txt in hits:
            print(f"  {doc['document_id']:<13} {label[:40]:<42} -> {txt[:30]!r}")
        if not apply:
            continue
        # every section, not just the first: PM-33.0302 carries a second
        # key-facts-attendance block, and pruning only sections[0] left its
        # fragment in place and the run reporting the same document forever
        for sec in doc["sections"]:
            sec["clauses"] = [cl for cl in sec.get("clauses", [])
                              if len(str(cl.get("text", "")).strip().split())
                              >= MIN_CLAUSE_WORDS]
        doc["sections"] = [s for s in doc["sections"] if s.get("clauses")]
        if not doc["sections"]:
            # nothing left to say: an empty sections list is exactly what curate
            # treats as "needs curating", so the order returns to the queue
            emptied += 1
        json.dump(doc, open(doc_path(doc["document_id"]), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)

    verb = "dropped from" if apply else "would drop from"
    print(f"\n{dropped} truncated clauses {verb} {touched} orders")
    if emptied:
        print(f"{emptied} orders lost their whole block and go back in the curate queue")
    if not apply:
        print("dry run — nothing written. Use --apply.")


if __name__ == "__main__":
    main("--apply" in sys.argv)
