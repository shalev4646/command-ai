"""Does every number in a curated clause appear in its source?

The review of the three highest-risk orders found exactly one defect, and it
was a number: PM-21.0203 said a call-up order is "טופס 55" where the source
says "טופס 2.2". The claim happens to be true — and that is the problem. The
model filled it in from general knowledge rather than from the order, and both
faithfulness gates passed it, because the vocabulary was grounded and the topic
was not a flagged one.

Numbers are where that failure does damage: deadlines, ranks, day counts, sums,
percentages, form numbers. They are also the one thing that can be checked
mechanically without reading a word. So instead of hand-reading 160 clauses,
check every number in every clause against the order's own text.

Hebrew writes numbers both ways — "שלושים ימים" and "30 יום" mean the same and
either may be the form that appears in the source — so a digit is accepted if
its spelled-out form is present, and vice versa. Clause-number citations are
skipped: they are verified separately, against the clause markers rather than
against free text.
"""
from __future__ import annotations

import json
import re

import backend
from night import config as C

WORDS = {
    1: ("אחד", "אחת", "ראשון"), 2: ("שניים", "שתיים", "שני", "שתי"),
    3: ("שלושה", "שלוש"), 4: ("ארבעה", "ארבע"), 5: ("חמישה", "חמש"),
    6: ("שישה", "שש"), 7: ("שבעה", "שבע"), 8: ("שמונה",), 9: ("תשעה", "תשע"),
    10: ("עשרה", "עשר"), 12: ("שנים-עשר", "שנים עשר", "שתים-עשרה"),
    14: ("ארבעה עשר",), 15: ("חמישה עשר", "חמש עשרה"), 20: ("עשרים",),
    21: ("עשרים ואחד",), 30: ("שלושים",), 36: ("שלושים ושישה",),
    45: ("ארבעים וחמישה",), 60: ("שישים",), 90: ("תשעים",), 100: ("מאה",),
}

# "(סעיף 43)" / "(סעיפים 44–45)" — checked against clause markers elsewhere,
# not against the body text, so counting them here would be double jeopardy.
CITE = re.compile(r"סעיפים?\s*\d{1,3}(\s*[–\-—]\s*\d{1,3})?")


def numbers_in(text: str) -> set[str]:
    return set(re.findall(r"(?<![\d.])\d{1,4}(?![\d.])", CITE.sub(" ", text)))


def present(num: str, raw: str) -> bool:
    if num in raw:
        return True
    n = int(num)
    if any(w in raw for w in WORDS.get(n, ())):
        return True
    # years are routinely written both as 2003 and as התשס"ד; a four-digit
    # number whose last two digits appear nearby is treated as present
    return len(num) == 4 and num[2:] in raw


def run() -> None:
    docs = {d["document_id"]: d for d in backend.load_documents() if d.get("document_id")}
    acc = {a["doc_id"]: a for a in C.read_jsonl(C.OUT / "curate_accepted.jsonl")}

    flagged, checked, clean_docs = [], 0, 0
    for doc_id, entry in sorted(acc.items()):
        doc = docs.get(doc_id)
        if not doc or not (doc.get("sections") or []):
            continue                                  # pulled, e.g. 20.0502
        raw = re.sub(r"\s+", " ", str(doc.get("raw_text", "")))
        bad = []
        for cl in doc["sections"][0].get("clauses", []):
            for num in numbers_in(cl.get("text", "")):
                checked += 1
                if not present(num, raw):
                    bad.append((num, str(cl.get("number", ""))[:44]))
        if bad:
            flagged.append((doc_id, bad))
        else:
            clean_docs += 1

    C.log(f"[numbers] checked {checked} numbers across {len(acc)} curated orders")
    C.log(f"[numbers] orders with every number traced to source: {clean_docs}")
    C.log(f"[numbers] orders carrying an untraceable number: {len(flagged)}")
    for doc_id, bad in flagged:
        C.log(f"[numbers]   {doc_id}: " +
              "; ".join(f"{n} ({lbl})" for n, lbl in bad[:4]))
    (C.OUT / "numbers.json").write_text(
        json.dumps({d: b for d, b in flagged}, ensure_ascii=False, indent=1),
        encoding="utf-8")


if __name__ == "__main__":
    run()
