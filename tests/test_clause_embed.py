# -*- coding: utf-8 -*-
"""RETRIEVE_V3 — storage/clause_embed.py and backend.extend_with_clause_embed,
with a fake embedder (character trigrams hashed into 64 dims) so nothing here
needs a model. Ships OFF; OFF is byte-identical.

    venv\\Scripts\\python.exe tests\\test_clause_embed.py
"""
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import backend
from storage import clause_embed as ce
from tests.test_clause_index import DOC_A, DOC_B, DOC_C, UNCURATED, BIG, Q, _window


class FakeEmbedder:
    """Deterministic: hashed character trigrams, L2-normalised. Similar text,
    similar vector — enough to rank a matching clause first."""
    name = "fake"

    def __init__(self):
        self.calls = 0

    def embed(self, texts, kind="passage"):
        self.calls += len(texts)
        out = np.zeros((len(texts), 64), dtype=np.float32)
        for i, t in enumerate(texts):
            t = " ".join(t.split())
            for j in range(len(t) - 2):
                out[i, hash(t[j:j + 3]) % 64] += 1.0
        return out / np.clip(np.linalg.norm(out, axis=1, keepdims=True), 1e-9, None)


@contextmanager
def _with(flag, docs, only=False, **knobs):
    old = {k: getattr(backend, k) for k in ("RETRIEVE_V3", "RETRIEVE_V3_ONLY", "RETRIEVE_V3_MODEL",
                                             "RETRIEVE_V2_BLOCK_WORDS", "RETRIEVE_V2_TOP_K", "RETRIEVE_V2_ROUTE_BONUS")}
    old_load, old_idx, old_make = backend.load_documents, backend._v3_index, ce.make_embedder
    backend.RETRIEVE_V3, backend.RETRIEVE_V3_ONLY = flag, only
    for k, v in knobs.items():
        setattr(backend, k, v)
    backend.load_documents = lambda: docs
    backend._v3_index = None
    tmp = tempfile.TemporaryDirectory()
    fake = FakeEmbedder()
    ce.make_embedder = lambda name: fake
    real_cls = ce.ClauseEmbedIndex

    def build(d, embedder, cache_path=None, save=True):
        return real_cls(d, embedder, cache_path=Path(tmp.name) / "cache.npz", save=save)
    backend._ce = type("M", (), {"ClauseEmbedIndex": staticmethod(build), "make_embedder": staticmethod(lambda n: fake)})
    try:
        yield fake
    finally:
        for k, v in old.items():
            setattr(backend, k, v)
        backend.load_documents, backend._v3_index, ce.make_embedder = old_load, old_idx, old_make
        backend._ce = ce
        tmp.cleanup()


def test_units_are_curated_clauses_and_anchors_only():
    us = ce.units_of([DOC_A, DOC_B, UNCURATED, {"document_id": "N.9", "title": "עם עוגן",
                                                  "suggested_questions": ["שאלה בלי בלוק?"], "sections": []}])
    assert {u.doc_id for u in us} == {"A.1", "B.2"}, "no block, nothing to serve, nothing to index"
    titled = [u for u in us if u.section is not None]
    assert len(titled) == 4 and all(u.text.startswith(u.clause + ". ") for u in titled)


def test_the_question_finds_the_clause_and_the_order():
    with tempfile.TemporaryDirectory() as d:
        idx = ce.ClauseEmbedIndex([DOC_A, DOC_B, DOC_C], FakeEmbedder(), cache_path=Path(d) / "c.npz")
        hits = idx.rank(Q, doc_ids={"A.1", "B.2"})
        assert hits.clauses[0][1:] == ("B.2", "key-facts", "אילו תכשיטים מותר לענוד עם מדים"), hits.clauses[:2]
        assert hits.docs[0][1] == "B.2" and all(dd != "C.3" for _, dd in hits.docs)
        assert idx.stats()["clauses"] == 5 and idx.stats()["dim"] == 64


def test_the_cache_makes_the_second_build_free():
    with tempfile.TemporaryDirectory() as d:
        emb = FakeEmbedder()
        ce.ClauseEmbedIndex([DOC_A, DOC_B], emb, cache_path=Path(d) / "c.npz")
        n = emb.calls
        idx2 = ce.ClauseEmbedIndex([DOC_A, DOC_B], emb, cache_path=Path(d) / "c.npz")
        assert emb.calls == n and idx2.embedded_now == 0
        other = FakeEmbedder(); other.name = "other-model"
        idx3 = ce.ClauseEmbedIndex([DOC_A, DOC_B], other, cache_path=Path(d) / "c.npz")
        assert idx3.embedded_now == len(idx3.units), "a cache of another model is never reused"


def test_off_is_a_no_op_and_on_appends_the_best_orders_block():
    win = _window()
    with _with(0, [DOC_A, DOC_B]):
        assert backend.extend_with_clause_embed(win, Q, "soldier") is win
    with _with(1, [DOC_A, DOC_B]):
        out = backend.extend_with_clause_embed(win, Q, "soldier")
    assert out[:1] == win and [c["doc_id"] for c in out[1:]] == ["B.2"] * 3, out


def test_the_registry_and_the_scope():
    assert set(ce.MODELS) >= {"minilm", "e5-base", "bge-m3"}
    try:
        ce.make_embedder("no-such-model")
        assert False, "unknown model must fail loudly"
    except KeyError:
        pass
    with _with(2, [DOC_A, DOC_B, DOC_C, UNCURATED]):
        out = backend.extend_with_clause_embed([], Q, "soldier")
    assert {c["doc_id"] for c in out} <= {"A.1", "B.2"}
    with _with(2, [DOC_A, DOC_B, DOC_C]):
        out = backend.extend_with_clause_embed([], "תכשיטים בטיפול רפואי", "reserve")
    assert {c["doc_id"] for c in out} == {"C.3"}


def test_an_oversized_block_is_cut_to_evidenced_clauses_here_too():
    with _with(1, [BIG], RETRIEVE_V2_BLOCK_WORDS=600, RETRIEVE_V2_TOP_K=2):
        out = backend.extend_with_clause_embed([], "כמה ימי מחבוש מפקד יכול לתת לי, מה העונש המרבי?", "soldier")
    assert 0 < len(out) <= 2 and "מה עונש המחבוש המרבי לחייל" in [c["clause"] for c in out], [c["clause"] for c in out]


def test_v3_only_drops_the_raw_window():
    seen = []
    real = backend.retrieve
    backend.retrieve = lambda *a, **kw: seen.append(1) or []
    try:
        with _with(1, [DOC_A, DOC_B], only=True):
            out = backend.retrieve_for_role(Q, "soldier", route=set(), widen=True)
        assert not seen, "V3_ONLY must not rank raw chunks at all"
        assert out and {c["doc_id"] for c in out} == {"B.2"}
        with _with(1, [DOC_A, DOC_B], only=False):
            backend.retrieve_for_role(Q, "soldier", route=set(), widen=False)
        assert seen, "without V3_ONLY the raw window is still ranked"
    finally:
        backend.retrieve = real


def test_the_index_is_rebuilt_when_the_model_changes():
    with _with(1, [DOC_A]) as fake:
        first = backend._clause_embed_index()
        assert backend._clause_embed_index() is first
        backend.RETRIEVE_V3_MODEL = "e5-base"
        assert backend._clause_embed_index() is not first


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
    print("all clause-embed tests passed")
