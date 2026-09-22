# -*- coding: utf-8 -*-
"""RETRIEVE_BLOCK_CLAUSE_EMBED — clause selection inside a cut block.

A block over RETRIEVE_V2_BLOCK_WORDS is cut to RETRIEVE_V2_TOP_K clauses, and
the lead block over RETRIEVE_FULL_BLOCK_MAX_WORDS to a word budget, both by the
clause-TITLE index alone: a clause whose title does not sound like the
question is dropped even when its body is the rule. The flag fuses the title
ranking with the clause-body embedding ranking (1) or ranks by the body alone
(2). Ships OFF; these tests pin that OFF is byte-identical, that a body-only
match is picked when on, and that nothing outside the cut moves.

The embedder is the fake from test_clause_embed (hashed character trigrams), so
nothing here needs a model. Criterion and ceiling: night/BLOCK_CLAUSE_CRITERION.md.

    venv\\Scripts\\python.exe tests\\test_block_clause_embed.py
"""
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import backend
from storage import clause_embed as ce
from tests.test_clause_embed import FakeEmbedder
from tests.test_clause_index import DOC_B

# A big order (over the 600-word cut): ten filler clauses with titles that say
# nothing about jewellery, plus the answering clause behind an OPAQUE title —
# the title index cannot see it, its body is the rule.
FILLER = [{"number": f"סעיף מספר {i} על נושא {i}", "text": " ".join(["מילה"] * 100)} for i in range(10)]
ANSWER = {"number": "הוראות כלליות", "text": "עם מדים מותר לענוד טבעת נישואין בלבד; תכשיטים אחרים אסורים לענוד."}
BIG = {"document_id": "E.5", "title": "פקודה ענקית", "roles": ["soldier"],
       "sections": [{"id": "key-facts", "title": "עיקרי הפקודה", "clauses": FILLER + [ANSWER]}]}
KEY = ("key-facts", "הוראות כלליות")
Q = "יש לי טבעת, מותר לענוד תכשיטים עם מדים?"


class Counting(FakeEmbedder):
    def __init__(self):
        super().__init__()
        self.queries = 0

    def embed(self, texts, kind="passage"):
        if kind == "query":
            self.queries += len(texts)
        return super().embed(texts, kind)


@contextmanager
def _with(mode: int, docs: list[dict]):
    old = {k: getattr(backend, k) for k in
           ("RETRIEVE_BLOCK_CLAUSE_EMBED", "RETRIEVE_DOC_BLOCKS", "RETRIEVE_V2_BLOCK_WORDS",
            "RETRIEVE_V2_TOP_K", "RETRIEVE_FULL_BLOCK_MAX_WORDS")}
    old_load, old_v2, old_v3, old_ret = (backend.load_documents, backend._v2_index,
                                         backend._v3_index, backend.retrieve)
    emb = Counting()
    with tempfile.TemporaryDirectory() as tmp:
        backend.RETRIEVE_BLOCK_CLAUSE_EMBED = mode
        backend.load_documents = lambda: docs
        backend._v2_index = None
        backend._v3_index = (None, ce.ClauseEmbedIndex(docs, emb, cache_path=Path(tmp) / "c.npz",
                                                       save=False))
        # pin the key so _clause_embed_index() hands back this index untouched
        backend._v3_index = ((backend._docs_cache[0] if backend._docs_cache else None,
                              id(docs), len(docs), backend.RETRIEVE_V3_MODEL), backend._v3_index[1])
        try:
            yield emb
        finally:
            for k, v in old.items():
                setattr(backend, k, v)
            backend.load_documents, backend._v2_index, backend._v3_index, backend.retrieve = (
                old_load, old_v2, old_v3, old_ret)


def _keys(chunks):
    return [(c["section"], c["clause"]) for c in chunks]


def test_it_ships_off_and_off_returns_the_title_scores_themselves():
    assert backend.RETRIEVE_BLOCK_CLAUSE_EMBED == 0
    t = {("s", "a"): 0.5}
    assert backend.fuse_clause_scores(t, {("s", "a"): 0.9, ("s", "b"): 0.8}, 0) is t


# the lead-block budget: exactly two filler clauses (112 words each, prefix
# included), so the off path fills it in block order and stops — a smaller
# budget leaves room the budget-filler would give the 19-word answer anyway
BUDGET = 224


def test_off_is_byte_identical_and_never_embeds():
    with _with(0, [BIG, DOC_B]) as emb:
        cut = backend._capped_block(BIG, Q, BUDGET)
        assert KEY not in _keys(cut), "today the title index cannot see the answering clause"
        assert emb.queries == 0, "off must not touch the embedding index"
        with_flag_off = backend.v2_block_chunks(BIG, backend._clause_index().rank(Q, doc_ids=["E.5"]).clause_scores("E.5"))
        assert KEY not in _keys(with_flag_off)


def test_on_a_body_only_match_is_picked_by_the_lead_block_cut():
    with _with(1, [BIG, DOC_B]) as emb:
        cut = backend._capped_block(BIG, Q, BUDGET)
        assert KEY in _keys(cut), _keys(cut)
        assert sum(len(c["text"].split()) for c in cut) <= BUDGET, "the word budget still holds"
        assert emb.queries == 1
        assert _keys(cut) == [k for k in _keys(backend._full_block(BIG)) if k in set(_keys(cut))], \
            "emitted in block order, as before"


def test_on_the_appended_block_cut_picks_it_too():
    """doc_blocks_window: the same fusion for the N appended blocks, one
    question embedding for all of them."""
    ranked = [{"doc_id": "E.5", "title": "פקודה ענקית", "section": "chunk1", "clause": "1",
               "text": "טקסט גולמי.", "score": 0.6}]
    with _with(1, [BIG, DOC_B]) as emb:
        backend.RETRIEVE_DOC_BLOCKS = 6
        backend.retrieve = lambda *a, **kw: list(ranked)
        served = backend.doc_blocks_window(Q, "soldier", None, ["E.5", "B.2"])
        assert KEY in _keys(served), _keys(served)
        assert len([c for c in served if c["doc_id"] == "E.5"]) <= backend.RETRIEVE_V2_TOP_K
        assert emb.queries == 1
    with _with(0, [BIG, DOC_B]) as emb:
        backend.RETRIEVE_DOC_BLOCKS = 6
        backend.retrieve = lambda *a, **kw: list(ranked)
        served = backend.doc_blocks_window(Q, "soldier", None, ["E.5", "B.2"])
        assert KEY not in _keys(served) and emb.queries == 0


def test_an_uncut_block_is_untouched_when_on():
    with _with(1, [BIG, DOC_B]) as emb:
        whole = backend._capped_block(DOC_B, Q, BUDGET)
        assert whole == backend._full_block(DOC_B)
        assert emb.queries == 0, "a block that fits is served whole without asking the embedder"


def test_mode_two_ranks_by_the_body_alone_and_fusion_keeps_both_lists():
    t = {("s", "a"): 0.5}
    e = {("s", "a"): 0.1, ("s", "b"): 0.9}
    only = backend.fuse_clause_scores(t, e, 2)
    assert only[("s", "b")] > only[("s", "a")] > 0
    fused = backend.fuse_clause_scores(t, e, 1)
    assert set(fused) == {("s", "a"), ("s", "b")} and all(v > 0 for v in fused.values())
    # a clause first on BOTH lists beats one first on one list only
    both = backend.fuse_clause_scores({("s", "a"): 0.5, ("s", "b"): 0.2}, {("s", "a"): 0.9, ("s", "b"): 0.1}, 1)
    assert both[("s", "a")] > both[("s", "b")]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
    print("all block-clause-embed tests passed")
