# -*- coding: utf-8 -*-
"""RETRIEVE_V3 — a clause-level embedding index with a pluggable embedder.

Why (night/PLAN_ROUND4.md, the 12.09 decision)
--------------------------------------------
After two arms the picture is one number: in 45 of 82 measured questions the
ANSWERING ORDER never reaches the model. Every knob so far — anchors, router
seats, the title index (V2), the second pass — nudges that by 5 to 8 questions
and costs context. Two things are suspect underneath all of them:

  1. the index is 10,324 windows of 180 words cut from OCR'd raw text, plus
     the curated clauses, scored together; the raw windows share vocabulary
     with everything and drown the curated clause that answers;
  2. the embedder is paraphrase-multilingual-MiniLM-L12-v2 (118M params,
     384 dims) — small, and weak on Hebrew.

This module changes exactly those two, behind a flag, as a parallel path:
one vector per CURATED CLAUSE (its question-shaped title + its text) and per
anchor question, nothing from raw text; the embedder is chosen by
RETRIEVE_V3_MODEL — `minilm` reuses the production stack (no new download,
so the first pre-screen isolates "clean index" from "better model"), the
others are stronger multilingual ONNX exports. The question is scored against
units, the best orders are served as whole blocks (backend.v2_block_chunks),
appended after the window — or, with RETRIEVE_V3_ONLY, instead of it.

Free pre-screen (the user's machine, night/sectprobe.py):
    RETRIEVE_FULL_BLOCKS=1 RETRIEVE_V3=2                         python -m night.sectprobe out/sect_v3_minilm.json
    RETRIEVE_FULL_BLOCKS=1 RETRIEVE_V3=2 RETRIEVE_V3_MODEL=e5-base python -m night.sectprobe out/sect_v3_e5.json
The number that decides: the answering order in the window, 29/82 today.

Vectors are cached per model in storage/clause_embed_cache_<model>.npz, keyed
by the sha1 of the unit text, so a corpus edit re-embeds only what changed.
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from storage.clause_index import Hits, _is_curated, _is_titled, _question_lists

_HERE = Path(__file__).resolve().parent

# name -> how to load it. `minilm` is the production stack (vector_store), the
# rest are transformers.js-style ONNX exports on the Hub. Candidate filenames
# are tried in order — the export naming differs between repos.
_FILES = ("onnx/model_quantized.onnx", "onnx/model_q8.onnx", "onnx/model_uint8.onnx", "onnx/model.onnx")
MODELS: dict[str, dict] = {
    "minilm": {},
    "e5-small": {"repo": "Xenova/multilingual-e5-small", "query": "query: ", "passage": "passage: ", "pooling": "mean"},
    "e5-base": {"repo": "Xenova/multilingual-e5-base", "query": "query: ", "passage": "passage: ", "pooling": "mean"},
    "e5-large": {"repo": "Xenova/multilingual-e5-large", "query": "query: ", "passage": "passage: ", "pooling": "mean"},
    "bge-m3": {"repo": "Xenova/bge-m3", "query": "", "passage": "", "pooling": "cls"},
}


class MiniLMEmbedder:
    """The production embedding stack, shared — never a second copy."""
    name = "minilm"

    def embed(self, texts: list[str], kind: str = "passage") -> np.ndarray:
        from storage.vector_store import _get_ef
        return np.asarray(_get_ef()(list(texts)), dtype=np.float32)


class OnnxEmbedder:
    """A Hub ONNX export run with onnxruntime + tokenizers, the way
    vector_store.MultilingualMiniLM does it (arena off, batch of 8).
    RETRIEVE_V3_REPO / RETRIEVE_V3_FILE override the registry for one run."""
    _BATCH = 8

    def __init__(self, name: str, spec: dict):
        from huggingface_hub import hf_hub_download
        from tokenizers import Tokenizer
        import onnxruntime as ort

        self.name = name
        repo = os.environ.get("RETRIEVE_V3_REPO") or spec["repo"]
        self._q, self._p, self._pooling = spec.get("query", ""), spec.get("passage", ""), spec.get("pooling", "mean")

        def _get(filename: str) -> str:
            try:
                return hf_hub_download(repo, filename, local_files_only=True)
            except Exception:
                return hf_hub_download(repo, filename)

        model_path = None
        files = [os.environ["RETRIEVE_V3_FILE"]] if os.environ.get("RETRIEVE_V3_FILE") else list(_FILES)
        errors = []
        for f in files:
            try:
                model_path = _get(f)
                break
            except Exception as e:  # try the next export name
                errors.append(f"{f}: {e!r}"[:200])
        if model_path is None:
            raise RuntimeError(f"no ONNX export found in {repo}: " + " | ".join(errors))
        self._tokenizer = Tokenizer.from_file(_get("tokenizer.json"))
        self._tokenizer.enable_truncation(max_length=512)
        self._tokenizer.enable_padding()
        so = ort.SessionOptions()
        so.enable_cpu_mem_arena = False
        so.enable_mem_pattern = False
        self._session = ort.InferenceSession(model_path, sess_options=so, providers=["CPUExecutionProvider"])
        self._input_names = {i.name for i in self._session.get_inputs()}

    def _batch(self, texts: list[str]) -> np.ndarray:
        encs = self._tokenizer.encode_batch(texts)
        ids = np.array([e.ids for e in encs], dtype=np.int64)
        mask = np.array([e.attention_mask for e in encs], dtype=np.int64)
        feed = {"input_ids": ids, "attention_mask": mask}
        if "token_type_ids" in self._input_names:
            feed["token_type_ids"] = np.zeros_like(ids)
        hidden = self._session.run(None, feed)[0]
        if self._pooling == "cls":
            emb = hidden[:, 0, :]
        else:
            m = mask[..., None].astype(np.float32)
            emb = (hidden * m).sum(axis=1) / np.clip(m.sum(axis=1), 1e-9, None)
        return emb / np.clip(np.linalg.norm(emb, axis=1, keepdims=True), 1e-9, None)

    def embed(self, texts: list[str], kind: str = "passage") -> np.ndarray:
        prefix = self._q if kind == "query" else self._p
        texts = [prefix + t for t in texts]
        out = [self._batch(texts[i:i + self._BATCH]) for i in range(0, len(texts), self._BATCH)]
        return np.concatenate(out, axis=0).astype(np.float32) if out else np.zeros((0, 1), dtype=np.float32)


def make_embedder(name: str):
    if name not in MODELS:
        raise KeyError(f"unknown RETRIEVE_V3_MODEL {name!r}; known: {sorted(MODELS)}")
    return MiniLMEmbedder() if name == "minilm" else OnnxEmbedder(name, MODELS[name])


@dataclass
class Unit:
    doc_id: str
    section: str | None
    clause: str | None
    text: str


def units_of(docs: list[dict]) -> list[Unit]:
    """One unit per curated clause (title + text) and per anchor question.
    Nothing from raw text — that is the point."""
    out: list[Unit] = []
    for d in docs:
        doc_id = d.get("document_id") or ""
        if not doc_id:
            continue
        had = False
        for s in d.get("sections") or []:
            if not _is_curated(s):
                continue
            sec = str(s.get("id") or "")
            for cl in s.get("clauses") or []:
                num = str(cl.get("number") or "")
                text = " ".join((cl.get("text") or "").split())
                if not text:
                    continue
                had = True
                title = f"{num}. " if _is_titled(num) else ""
                out.append(Unit(doc_id, sec, num, f"{title}{text}"))
        if had:
            for q in _question_lists(d):
                out.append(Unit(doc_id, None, None, q))
    return out


def _key(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


class ClauseEmbedIndex:
    """Vectors for every unit, from the per-model cache where possible."""

    def __init__(self, docs: list[dict], embedder, cache_path: Path | None = None,
                 save: bool = True):
        self.embedder = embedder
        self.units = units_of(docs)
        self.cache_path = cache_path if cache_path is not None else \
            _HERE / f"clause_embed_cache_{getattr(embedder, 'name', 'x')}.npz"
        cache = self._load_cache()
        keys = [_key(u.text) for u in self.units]
        missing = [i for i, k in enumerate(keys) if k not in cache]
        if missing:
            fresh = embedder.embed([self.units[i].text for i in missing], kind="passage")
            for i, vec in zip(missing, fresh):
                cache[keys[i]] = np.asarray(vec, dtype=np.float32)
            if save:
                self._save_cache(cache)
        self.embedded_now = len(missing)
        self.matrix = np.stack([cache[k] for k in keys]).astype(np.float32) if keys else np.zeros((0, 1), np.float32)

    def _load_cache(self) -> dict[str, np.ndarray]:
        p = self.cache_path
        if not p or not Path(p).exists():
            return {}
        try:
            data = np.load(p)
            if str(data["model"][0]) != getattr(self.embedder, "name", "x"):
                return {}
            keys = [k.decode() if isinstance(k, bytes) else str(k) for k in data["keys"]]
            return dict(zip(keys, data["vectors"].astype(np.float32)))
        except Exception:
            return {}

    def _save_cache(self, cache: dict[str, np.ndarray]) -> None:
        try:
            np.savez_compressed(self.cache_path, keys=np.array(list(cache)),
                                vectors=np.stack(list(cache.values())),
                                model=np.array([getattr(self.embedder, "name", "x")]))
        except Exception:
            pass  # the cache is an optimisation

    def rank(self, question: str, doc_ids=None) -> Hits:
        if not self.units or not (question or "").strip():
            return Hits()
        q = self.embedder.embed([question], kind="query")[0]
        scores = self.matrix @ q
        allowed = None if doc_ids is None else set(doc_ids)
        best_clause: dict[tuple[str, str, str], float] = {}
        best_doc: dict[str, float] = {}
        for u, s in zip(self.units, scores):
            if allowed is not None and u.doc_id not in allowed:
                continue
            s = float(s)
            if u.section is not None:
                k = (u.doc_id, u.section, u.clause)
                if s > best_clause.get(k, -2.0):
                    best_clause[k] = s
            if s > best_doc.get(u.doc_id, -2.0):
                best_doc[u.doc_id] = s
        clauses = sorted(((s, d, sec, cl) for (d, sec, cl), s in best_clause.items()), key=lambda x: -x[0])
        docs = sorted(((s, d) for d, s in best_doc.items()), key=lambda x: -x[0])
        return Hits(clauses=clauses, docs=docs)

    def stats(self) -> dict:
        return {"units": len(self.units), "clauses": sum(1 for u in self.units if u.section is not None),
                "anchors": sum(1 for u in self.units if u.section is None),
                "docs": len({u.doc_id for u in self.units}), "dim": int(self.matrix.shape[1]) if self.matrix.size else 0,
                "embedded_now": self.embedded_now}
