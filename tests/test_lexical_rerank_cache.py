# -*- coding: utf-8 -*-
"""The lexical rerank's per-term hit masks: same scores, one scan per term.

The rerank used to fold every chunk in the corpus and rebuild a full
match matrix on EVERY query — 88% of a question's local CPU when it was
measured on 2026-08-23, and because Streamlit serves all sessions from one
process holding one GIL, that cost was the app's concurrency ceiling: the
machine flat-lined at 0.63 questions/second whether 2 or 16 people asked.

The cache is only worth having if it is invisible in the output, so the test
that matters is the first one: the live implementation must produce the
same scores, to the last digit, as the scan it replaced. It is checked here
against a verbatim copy of the old code rather than against recorded numbers,
so it keeps holding when the corpus changes.

    venv\\Scripts\\python.exe tests\\test_lexical_rerank_cache.py
"""
import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import storage.vector_store as vs

# building the index can embed newly-added chunks; persisting that is not this
# test's business (and races anything else running against the same file)
vs._save_emb_cache = lambda: None

QUERIES = [
    "כמה ימי חופשה מגיעים לי בשנה",
    "איחרתי לבסיס בגלל פקק בכביש",
    'תוך כמה זמן אני אמור לקבל תור לקב"ן',
    "לא קיבלתי את התשלום על ימי המילואים",
    "המפקד לקח לי את הטלפון",
    "אבא שלי חולה ואני צריך לצאת הביתה דחוף",
]


def _old_rerank(query, candidates, positions):
    """The pre-cache implementation, verbatim. positions did not exist then."""
    terms = [v for v in (vs._term_variants(w) for w in query.split()) if v]
    if not terms or not candidates:
        return
    weights = [vs._term_weight(v) for v in terms]
    texts = [c["text"].translate(vs._FINALS) for c in candidates]
    matches = [
        [any(v in text for v in variants) for text in texts]
        for variants in terms
    ]
    for i, c in enumerate(candidates):
        overlap = sum(w for w, m in zip(weights, matches) if m[i])
        c["score"] = round(c["score"] + vs._LEXICAL_WEIGHT * overlap / len(terms), 3)


def _candidates(limit: int = 400):
    """Real chunks with their real corpus positions — the shape retrieve() builds."""
    corpus = vs._get_corpus()[:limit]
    positions = list(range(len(corpus)))
    cands = [
        {"text": c["text"], "doc_id": c["doc_id"], "title": c["title"],
         "section": c["section"], "clause": c["clause"], "score": round(0.4 + i / 1000, 3)}
        for i, c in enumerate(corpus)
    ]
    return cands, positions


def test_scores_are_identical_to_the_scan_it_replaced():
    for q in QUERIES:
        a, pos = _candidates()
        b, _ = _candidates()
        _old_rerank(q, a, pos)
        vs._lexical_rerank(q, b, pos)
        assert [c["score"] for c in a] == [c["score"] for c in b], q


def test_a_term_is_scanned_once_and_reused():
    vs._term_hits.clear()
    variants = vs._term_variants("חופשה")
    first = vs._term_hit_mask(variants)
    assert len(vs._term_hits) == 1
    # the same term, asked again, must hand back the SAME array, not a rebuild
    assert vs._term_hit_mask(variants) is first
    assert len(vs._term_hits) == 1


def test_mask_matches_a_direct_scan_and_df_is_its_sum():
    variants = vs._term_variants("מילואים")
    mask = vs._term_hit_mask(variants)
    folded = vs._folded_corpus()
    assert len(mask) == len(folded)
    direct = [any(v in t for v in variants) for t in folded]
    assert list(mask) == direct
    assert int(mask.sum()) == sum(direct)


def test_a_rare_term_outweighs_a_common_one():
    # the whole point of df: rarity is what buys the bonus
    rare = vs._term_weight(vs._term_variants("מחבוש"))
    common = vs._term_weight(vs._term_variants("חייל"))
    assert rare > common, (rare, common)


def test_upsert_clears_the_masks():
    # a stale mask after an upsert would score against a corpus that no longer
    # exists — the invalidation has to sit with the other corpus caches
    src = inspect.getsource(vs._index_document_locked)
    assert "_term_hits.clear()" in src
    assert not hasattr(vs, "_df_counts"), "the df cache it replaced is still around"


def test_cache_is_bounded():
    # query vocabulary is unbounded and every mask is one byte per chunk
    assert vs._MAX_TERM_MASKS <= 5000
    assert "_term_hits.clear()" in inspect.getsource(vs._term_hit_mask)


if __name__ == "__main__":
    failed = []
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("ok", name)
            except AssertionError as e:
                failed.append(name)
                print("FAIL", name, e)
    print("all rerank-cache tests passed" if not failed else f"FAILED: {failed}")
    raise SystemExit(1 if failed else 0)
