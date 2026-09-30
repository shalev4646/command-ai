# -*- coding: utf-8 -*-
"""Stage-6 helper: print one row (or several) of review_sheet_<tag>.json with the base
beside the arm, and check every quoted string of the arm's answer against the exact
window the model received (sent_user_content) and against the corpus.

    python review_view.py opus5v2 rs055 rs058      # rows
    python review_view.py opus5v2 --quotes          # quote check for all 72, only rows with a miss
"""
import json
import re
import sys
from pathlib import Path

RUN = Path(__file__).resolve().parents[2]
OUT = RUN / "night" / "out"
sys.path.insert(0, str(RUN))

_ABBR = re.compile(r'(?<=[\u05d0-\u05ea])["\u05f4](?=[\u05d0-\u05ea])')


def norm(t: str) -> str:
    """Letters and digits only: the window is raw PDF text with broken spacing
    ("\u05e9\u05e8\u05d5\u05d5\u05dc \u05d9\u05dd") and displaced punctuation (".\u05d7\u05d9\u05d9\u05dc ,\u05e9\u05de\u05d9\u05e0\u05d5"), and the model quotes
    it cleaned up \u2014 a quote is a miss only when its WORDS are not there."""
    return re.sub(r"[^0-9A-Za-z\u05d0-\u05ea]", "", t or "")


def quotes(answer: str) -> list[str]:
    a = _ABBR.sub("\u05f4", answer or "")
    got = []
    for line in a.splitlines():
        got += re.findall(r'(?:^|(?<=[\s(:\-\u2013\u2014]))["\u201e\u201c]([^"\u201d\u201c\u201e]{12,}?)["\u201d\u201c](?![\u05d0-\u05ea])', line)
    out = []
    for q in got:
        for frag in re.split(r"\.\.\.|…", q):
            frag = frag.strip(" .,;:—-")
            if len(frag.split()) >= 4:
                out.append(frag)
    return out


def _strings(x) -> list[str]:
    if isinstance(x, str):
        return [x]
    if isinstance(x, dict):
        return [s for v in x.values() for s in _strings(v)]
    if isinstance(x, list):
        return [s for v in x for s in _strings(v)]
    return []


def corpus_text() -> dict[str, str]:
    docs = {}
    for f in (RUN / "storage" / "json_store").glob("*.json"):
        d = json.loads(f.read_text(encoding="utf-8"))
        blocks = "\n".join(_strings(d.get("sections") or []))
        docs[d.get("document_id")] = norm((d.get("raw_text") or "") + "\n" + blocks)
    return docs


def check(answer: str, window: str, corpus: dict[str, str]) -> list[tuple[str, bool, list[str]]]:
    w = norm(window)
    res = []
    for q in quotes(answer):
        nq = norm(q)
        in_w = nq in w
        where = [] if in_w else [k for k, v in corpus.items() if nq in v][:3]
        res.append((q, in_w, where))
    return res


def main():
    tag = sys.argv[1]
    sheet = {x["id"]: x for x in json.loads((OUT / f"review_sheet_{tag}.json").read_text(encoding="utf-8"))}
    final = {json.loads(l)["id"]: json.loads(l) for l in open(OUT / f"probe_{tag}.jsonl", encoding="utf-8") if l.strip()}
    corpus = corpus_text()
    if "--quotes" in sys.argv:
        for i, x in sheet.items():
            r = final[i]
            miss = [(q, where) for q, in_w, where in check(r.get("answer") or "", r.get("sent_user_content") or "", corpus) if not in_w]
            if miss:
                print(f"== {i} ({x['verdict']}) kept={r.get('kept')}: {len(miss)} quote(s) not in the window")
                for q, where in miss:
                    print(f"   - {q[:160]}  | corpus: {where or 'NOT FOUND'}")
        return
    for i in sys.argv[2:]:
        x, r = sheet[i], final[i]
        b, a = x["base"], x["arm"]
        print("=" * 100)
        print(f"{i} [{x['role']}] {x['q']}")
        print(f"adjudication: {x['verdict']} doc={x.get('doc')} parts={x.get('parts')}")
        for q in x.get("quotes") or []:
            print(f"   adj-quote: {q[:300]}")
        print(f"--- BASE (4.8) review: {b.get('review')} | grader: {b.get('grade')} | second_pass={b.get('second_pass')}")
        print(b["answer"])
        if b.get("production_answer"):
            print("--- BASE production answer (KEEP_RULING):")
            print(b["production_answer"])
        print(f"--- ARM kept={a.get('kept')} stop={a.get('stop_reason')} p1_stop={a.get('p1_stop_reason')} "
              f"trunc={a.get('truncated')} grader={a.get('grade')} ({a.get('answered_parts')}) out_tok={a.get('output_tokens')} "
              f"words={a.get('context_words')}")
        print(f"    grader reason: {a.get('grade_reason')}")
        print(f"    sources: {a.get('sources')}")
        print(a.get("answer"))
        if a.get("other_answer"):
            print("--- ARM other pass:")
            print(a["other_answer"])
        for q, in_w, where in check(r.get("answer") or "", r.get("sent_user_content") or "", corpus):
            if not in_w:
                print(f"   !! quote not in window: {q[:200]} | corpus: {where or 'NOT FOUND'}")


if __name__ == "__main__":
    main()
