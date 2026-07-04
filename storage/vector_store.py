import hashlib
import json
import math
from pathlib import Path

import chromadb
import numpy as np
from chromadb import Documents, EmbeddingFunction, Embeddings

_COLLECTION = "idf_orders"
# Small windows so a single clause dominates its chunk's embedding — with
# 600-word chunks, mean-pooling diluted the one clause a question targets
# below the noise floor (the clubs order scored 0.11 cosine against a
# question about clubs). Adjacent chunks are stitched back together after
# retrieval, so answer context doesn't shrink with the window.
_CHUNK_WORDS = 180
_OVERLAP_WORDS = 40

_client: chromadb.ClientAPI | None = None
_collection: chromadb.Collection | None = None
_ef: "MultilingualMiniLM | None" = None
_corpus: list[dict] | None = None  # every chunk with its stored embedding


class MultilingualMiniLM(EmbeddingFunction):
    """Hebrew-capable embeddings via paraphrase-multilingual-MiniLM-L12-v2.

    Runs the quantized (quint8, ~120MB) ONNX export directly with
    onnxruntime + tokenizers — both already pulled in by chromadb — so no
    PyTorch / sentence-transformers dependency is added. The English-only
    all-MiniLM-L6-v2 default scored noticeably worse on Hebrew retrieval.
    """

    _REPO = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    _BATCH = 16

    def __init__(self):
        from huggingface_hub import hf_hub_download
        from tokenizers import Tokenizer
        import onnxruntime as ort

        def _get(filename: str) -> str:
            # cached copy first — never block startup on a network check
            try:
                return hf_hub_download(self._REPO, filename, local_files_only=True)
            except Exception:
                return hf_hub_download(self._REPO, filename)

        model_path = _get("onnx/model_quint8_avx2.onnx")
        tok_path = _get("tokenizer.json")
        self._tokenizer = Tokenizer.from_file(tok_path)
        self._tokenizer.enable_truncation(max_length=512)
        self._tokenizer.enable_padding()
        self._session = ort.InferenceSession(model_path, providers=["CPUExecutionProvider"])
        self._input_names = {i.name for i in self._session.get_inputs()}

    @staticmethod
    def name() -> str:
        return "multilingual-minilm-l12-v2-quint8"

    def get_config(self) -> dict:
        return {}

    @staticmethod
    def build_from_config(config: dict) -> "MultilingualMiniLM":
        return MultilingualMiniLM()

    def _embed_batch(self, texts: list[str]) -> np.ndarray:
        encs = self._tokenizer.encode_batch(texts)
        input_ids = np.array([e.ids for e in encs], dtype=np.int64)
        attention_mask = np.array([e.attention_mask for e in encs], dtype=np.int64)
        feed = {"input_ids": input_ids, "attention_mask": attention_mask}
        if "token_type_ids" in self._input_names:
            feed["token_type_ids"] = np.zeros_like(input_ids)
        hidden = self._session.run(None, feed)[0]  # [batch, seq, dim]
        mask = attention_mask[..., None].astype(np.float32)
        emb = (hidden * mask).sum(axis=1) / np.clip(mask.sum(axis=1), 1e-9, None)
        return emb / np.clip(np.linalg.norm(emb, axis=1, keepdims=True), 1e-9, None)

    def __call__(self, input: Documents) -> Embeddings:
        out: list[list[float]] = []
        for i in range(0, len(input), self._BATCH):
            out.extend(self._embed_batch(list(input[i:i + self._BATCH])).tolist())
        return out


def _get_ef() -> MultilingualMiniLM:
    """Single shared embedding function — the ONNX session is ~120MB, so it
    must never be instantiated twice (collection + query paths share it)."""
    global _ef
    if _ef is None:
        _ef = MultilingualMiniLM()
    return _ef


# ── Precomputed-embedding cache ──────────────────────────────────────────
# Embedding the corpus at every boot took ~2 minutes of ONNX inference —
# long enough to trip platform health checks on Streamlit Cloud. Chunk
# vectors are content-addressed (sha1 of chunk text) and committed to the
# repo, so a deploy boots by loading this file instead of re-embedding;
# only genuinely new/changed chunks (or queries) touch the model. Stale or
# missing entries degrade to on-the-fly embedding, never to wrong vectors.
_EMB_CACHE_PATH = Path(__file__).parent / "embedding_cache.npz"
_emb_cache: dict[str, np.ndarray] | None = None
_emb_cache_dirty = False


def _text_key(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _get_emb_cache() -> dict[str, np.ndarray]:
    global _emb_cache
    if _emb_cache is None:
        _emb_cache = {}
        if _EMB_CACHE_PATH.exists():
            try:
                data = np.load(_EMB_CACHE_PATH)
                keys = [k.decode() if isinstance(k, bytes) else str(k) for k in data["keys"]]
                _emb_cache = dict(zip(keys, data["vectors"].astype(np.float32)))
            except Exception:
                _emb_cache = {}
    return _emb_cache


def _save_emb_cache() -> None:
    global _emb_cache_dirty
    if not _emb_cache_dirty or not _emb_cache:
        return
    try:
        keys = np.array(list(_emb_cache.keys()))
        vectors = np.stack(list(_emb_cache.values()))
        np.savez_compressed(_EMB_CACHE_PATH, keys=keys, vectors=vectors)
        _emb_cache_dirty = False
    except Exception:
        pass  # cache is an optimization; failing to persist it must not break indexing


def _embed_cached(texts: list[str]) -> list[list[float]]:
    """Embeddings for texts, from the cache where possible."""
    global _emb_cache_dirty
    cache = _get_emb_cache()
    missing = [i for i, t in enumerate(texts) if _text_key(t) not in cache]
    if missing:
        fresh = _get_ef()([texts[i] for i in missing])
        for i, vec in zip(missing, fresh):
            cache[_text_key(texts[i])] = np.asarray(vec, dtype=np.float32)
        _emb_cache_dirty = True
        _save_emb_cache()
    return [cache[_text_key(t)].tolist() for t in texts]


def _get_collection() -> chromadb.Collection:
    global _client, _collection
    if _collection is None:
        _client = chromadb.EphemeralClient()
        # No embedding_function on purpose: every upsert passes explicit
        # embeddings and queries never go through col.query (retrieve() scans
        # the corpus itself), while an attached custom EF makes chroma rebuild
        # it — a fresh ~1.6s ONNX session load — on every single upsert call
        # (~50s across 16 documents at boot).
        _collection = _client.get_or_create_collection(
            name=_COLLECTION,
            embedding_function=None,
            metadata={"hnsw:space": "cosine"},
        )
        index_all_documents()
    return _collection


def _get_corpus() -> list[dict]:
    """All indexed chunks with their stored embeddings, cached in memory.

    The corpus is small (~a hundred chunks), so retrieval scores every chunk
    directly instead of going through an ANN candidate pool — see retrieve().
    Invalidated by index_document() on upsert.
    """
    global _corpus
    if _corpus is None:
        col = _get_collection()
        got = col.get(include=["documents", "metadatas", "embeddings"])
        _corpus = [
            {
                "text": doc,
                "doc_id": meta.get("doc_id"),
                "title": meta.get("title"),
                "section": meta.get("section"),
                "clause": meta.get("clause"),
                "embedding": np.asarray(emb, dtype=np.float32),
            }
            for doc, meta, emb in zip(got["documents"], got["metadatas"], got["embeddings"])
        ]
    return _corpus


def _split_raw_text(text: str, doc_id: str, title: str) -> list[dict]:
    """Split raw text into overlapping word-based chunks."""
    words = text.split()
    chunks = []
    i = 0
    n = 0
    while i < len(words):
        chunk_text = " ".join(words[i:i + _CHUNK_WORDS])
        chunks.append({
            "id": f"{doc_id}__chunk{n}",
            "text": f"{title}\n{chunk_text}",
            "doc_id": doc_id,
            "title": title,
            "section": f"chunk{n}",
            "clause": str(n),
            "tags": "",
        })
        n += 1
        i += _CHUNK_WORDS - _OVERLAP_WORDS
    return chunks


def index_document(doc: dict) -> int:
    """Index all clauses from a document. Returns number of chunks added."""
    global _corpus
    _corpus = None  # upserts invalidate the in-memory corpus cache
    col = _get_collection()
    doc_id = doc.get("document_id", "unknown")
    title = doc.get("title", "")

    ids, texts, metas = [], [], []

    # raw_text and structured sections are indexed side by side: a mostly-raw
    # document can still carry hand-structured clauses for content that raw
    # extraction mangles (e.g. the PM-33.0302 punishment-authority tables,
    # whose PDF table text survives only as scrambled RTL fragments).
    if doc.get("raw_text"):
        for c in _split_raw_text(doc["raw_text"], doc_id, title):
            ids.append(c["id"])
            texts.append(c["text"])
            metas.append({k: v for k, v in c.items() if k not in ("id", "text")})

    for section in doc.get("sections", []):
        section_title = section.get("title", section.get("id", ""))
        for clause in section.get("clauses", []):
            clause_num = clause.get("number", "")
            text = clause.get("text", "").strip()
            if not text:
                continue
            chunk_id = f"{doc_id}__s{section.get('id', '')}__c{clause_num}"
            ids.append(chunk_id)
            texts.append(f"{title} — {section_title}\nסעיף {clause_num}: {text}")
            metas.append({
                "doc_id": doc_id,
                "title": title,
                "section": str(section.get("id", "")),
                "clause": str(clause_num),
                "tags": ",".join(clause.get("tags", [])),
            })

    for annex in doc.get("annex_exceptions", []):
        category = annex.get("category", annex.get("case", "")).strip()
        annex_id = str(annex.get("id", ""))

        sub_cases = annex.get("sub_cases", [])
        if sub_cases:
            for sub in sub_cases:
                sub_id = sub.get("sub_id", "")
                reason = sub.get("reason", "")
                approver = sub.get("approver_min_rank", sub.get("approver", ""))
                compensation = sub.get("compensation", "")
                min_sleep = sub.get("min_sleep_hours", "")
                max_wake = sub.get("max_wake_hours", "")

                parts = [f"קטגוריה: {category}", f"סיבה: {reason}"]
                if min_sleep:
                    parts.append(f"מינימום שינה: {min_sleep} שעות")
                if max_wake:
                    parts.append(f"מקסימום ערות: {max_wake} שעות")
                if compensation:
                    parts.append(f"השלמה: {compensation}")
                parts.append(f"מאשר: {approver}")

                chunk_id = f"{doc_id}__annex_{annex_id}_{sub_id}"
                ids.append(chunk_id)
                texts.append(f"{title} — נספח חריגים\n" + " | ".join(parts))
                metas.append({
                    "doc_id": doc_id,
                    "title": title,
                    "section": f"annex_{annex_id}",
                    "clause": sub_id,
                    "tags": ",".join(sub.get("tags", [])),
                })
        else:
            # flat annex entry (original schema)
            case = category
            if not case:
                continue
            conditions = "; ".join(annex.get("conditions", []))
            text = f"חריג: {case}. תנאים: {conditions}. מאשר: {annex.get('approver', '')}"
            chunk_id = f"{doc_id}__annex__{case[:30]}"
            ids.append(chunk_id)
            texts.append(f"{title} — נספח חריגים\n{text}")
            metas.append({
                "doc_id": doc_id,
                "title": title,
                "section": "annex",
                "clause": case[:40],
                "tags": "",
            })

    if not ids:
        return 0

    # upsert so re-indexing is idempotent
    col.upsert(ids=ids, documents=texts, metadatas=metas, embeddings=_embed_cached(texts))
    return len(ids)


def index_all_documents(json_dir: Path | None = None) -> int:
    if json_dir is None:
        json_dir = Path(__file__).parent / "json_store"
    total = 0
    for f in sorted(json_dir.glob("*.json")):
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
            total += index_document(doc)
        except Exception as e:
            print(f"שגיאה באינדוקס {f.name}: {e}")
    return total


def retrieve(
    query: str,
    n_results: int = 10,
    max_per_doc: int = 4,
    doc_ids: list[str] | None = None,
) -> list[dict]:
    """Return the globally most relevant chunks, capped per document.

    Scores *every* chunk in the corpus (dense cosine + lexical bonus) rather
    than reranking an ANN candidate pool. With ~a hundred chunks a full scan
    is microseconds, and it removes a whole failure class: a document whose
    embedding ranks below the pool cutoff was unrescuable no matter how
    strong its lexical match (e.g. the clubs order ranked >40th on vector
    similarity for a question literally about clubs — mean-pooling a
    332-word chunk dilutes the one clause the question targets).

    `doc_ids`, if given, restricts the search to that set of documents —
    used to scope retrieval to whatever's relevant for the active role
    (soldier/commander/reserve).
    """
    if doc_ids is not None and not doc_ids:
        return []

    corpus = _get_corpus()
    if doc_ids is not None:
        allowed = set(doc_ids)
        corpus = [c for c in corpus if c["doc_id"] in allowed]
    if not corpus:
        return []

    try:
        query_emb = np.asarray(_get_ef()([query])[0], dtype=np.float32)
    except Exception:
        return []

    # embeddings are L2-normalized by the model, so dot product == cosine
    candidates = [
        {
            "text": c["text"],
            "doc_id": c["doc_id"],
            "title": c["title"],
            "section": c["section"],
            "clause": c["clause"],
            "score": round(float(c["embedding"] @ query_emb), 3),
        }
        for c in corpus
    ]

    _lexical_rerank(query, candidates)

    chunks = []
    per_doc_count: dict[str, int] = {}
    for c in sorted(candidates, key=lambda x: x["score"], reverse=True):
        doc_id = c["doc_id"]
        if per_doc_count.get(doc_id, 0) >= max_per_doc:
            continue
        per_doc_count[doc_id] = per_doc_count.get(doc_id, 0) + 1
        chunks.append(c)

    return _stitch_adjacent_chunks(_expand_neighbors(chunks[:n_results], corpus))


# Hebrew single-letter prefixes (ה,ו,ב,ל,מ,כ,ש) that glue onto content words —
# stripped from query terms so "בריתוק" still matches a chunk containing "ריתוק".
_HEB_PREFIXES = "הובלמכש"
_LEXICAL_WEIGHT = 0.25
# final-form letters fold to their medial form so "זמן" matches "זמני"
_FINALS = str.maketrans("םןץףך", "מנצפכ")


def _term_variants(word: str) -> set[str]:
    """Match-forms for one query word: progressively prefix-stripped, plus
    light suffix stemming (ה/ת and ים/ות) so construct-state and plural
    forms still hit — "הפתיחה" must match "פתיחת המועדון". Prefix stripping
    keeps every intermediate form (not just the shortest), so over-stripping
    into the root ("המועדון" → "עדון") can't *lose* the real form."""
    word = word.strip("?.,:;!\"'()[]").translate(_FINALS)
    if len(word) < 3:
        return set()
    variants = {word}
    p = word
    while len(p) > 3 and p[0] in _HEB_PREFIXES:
        p = p[1:]
        variants.add(p)
    for v in list(variants):
        if len(v) > 4 and v[-1] in "הת":
            variants.add(v[:-1])
        if len(v) > 5 and v[-2:] in ("ים", "ות"):
            variants.add(v[:-2])
    return variants


def _lexical_rerank(query: str, candidates: list[dict]) -> None:
    """Blend a lexical-overlap bonus into each candidate's vector score.

    Pure vector retrieval dilutes rare, decisive terms (e.g. "ריתוק משקי")
    inside 600-word chunks, so the one document that actually answers the
    question can rank below generically-similar chunks. Each query term is
    weighted by its rarity across the scored chunks (a poor man's IDF —
    a term found in only one chunk is near-decisive, one found in all
    of them says nothing), and candidates containing the rare terms get a
    proportional boost of up to _LEXICAL_WEIGHT. Mutates scores in place.
    """
    terms = [v for v in (_term_variants(w) for w in query.split()) if v]
    if not terms or not candidates:
        return

    n = len(candidates)
    texts = [c["text"].translate(_FINALS) for c in candidates]
    matches = [
        [any(v in text for v in variants) for text in texts]
        for variants in terms
    ]
    idf = [math.log(1 + n / (1 + sum(m))) for m in matches]
    total = sum(idf)
    if total <= 0:
        return
    for i, c in enumerate(candidates):
        overlap = sum(w for w, m in zip(idf, matches) if m[i])
        c["score"] = round(c["score"] + _LEXICAL_WEIGHT * overlap / total, 3)


def _expand_neighbors(chunks: list[dict], corpus: list[dict], top_k: int = 2) -> list[dict]:
    """Pull in the immediate neighbours (pos±1) of the top_k ranked chunks.

    Small retrieval windows make embeddings sharp but mean the clause that
    answers the question can sit one window over from the one that matched
    (the clubs order: its short tail chunk ranked #1 while the opening-hours
    clause lived in the adjacent, noisier chunk). Since stitching merges
    consecutive chunks anyway, adding a hit's direct neighbours restores the
    surrounding context at a known, small token cost (≤2 windows per hit).
    Neighbours inherit a score just under their anchor so stitching keeps
    the block's rank.
    """
    present = {
        (c["doc_id"], c["clause"])
        for c in chunks
        if (c.get("section") or "").startswith("chunk")
    }
    # only raw-text windows are valid neighbours: a structured clause of the
    # same document can share the same (doc_id, clause) numbering (e.g. the
    # PM-33.0302 annex rows are numbered 1..14, colliding with window
    # positions 1..14) and must not be injected as "adjacent" context
    by_key = {
        (c["doc_id"], c["clause"]): c
        for c in corpus
        if (c.get("section") or "").startswith("chunk")
    }
    out = list(chunks)
    for anchor in chunks[:top_k]:
        sec = anchor.get("section") or ""
        if not sec.startswith("chunk"):
            continue
        try:
            pos = int(anchor["clause"])
        except (ValueError, TypeError):
            continue
        for npos in (pos - 1, pos + 1):
            key = (anchor["doc_id"], str(npos))
            if npos < 0 or key in present:
                continue
            n = by_key.get(key)
            if n is None:
                continue
            present.add(key)
            out.append({
                "text": n["text"],
                "doc_id": n["doc_id"],
                "title": n["title"],
                "section": n["section"],
                "clause": n["clause"],
                "score": round(anchor["score"] - 0.001, 3),
            })
    return out


def _stitch_adjacent_chunks(chunks: list[dict]) -> list[dict]:
    """Merge consecutive raw-text chunks of the same document into one block.

    Raw-text docs are split into overlapping word windows (_CHUNK_WORDS long,
    _OVERLAP_WORDS shared with the next). When retrieval picks neighbouring windows
    — common when one document squarely answers the question — sending them
    separately both duplicates the 100-word overlap and hands the model a
    clause split mid-sentence across two context blocks. Stitching restores
    the continuous passage and drops the duplicated words.

    Window k starts at word k*(CHUNK-OVERLAP), so chunk k+1 only ever adds
    its words beyond the first OVERLAP (empty when the doc ended inside
    chunk k) — merging is exact, not heuristic.
    """
    out: list[dict] = []
    by_pos: dict[tuple, dict] = {}
    for c in chunks:
        sec = c.get("section") or ""
        if not sec.startswith("chunk"):
            out.append(c)  # structured clause/annex chunk — leave as is
            continue
        try:
            by_pos[(c["doc_id"], int(c["clause"]))] = c
        except (ValueError, TypeError):
            out.append(c)
    for (doc_id, pos), c in sorted(by_pos.items()):
        prev = by_pos.get((doc_id, pos - 1))
        if prev and prev.get("_merged_into") is not None:
            target = prev["_merged_into"]
            # chunk text is "{title}\n{body}" — append body minus the overlap
            body_words = c["text"].split("\n", 1)[-1].split()
            extra = body_words[_OVERLAP_WORDS:]
            if extra:
                target["text"] += " " + " ".join(extra)
            target["clause"] = f"{target['clause'].split('–')[0]}–{pos}"
            target["score"] = max(target["score"], c["score"])
            c["_merged_into"] = target
        else:
            c["_merged_into"] = c
            out.append(c)
    for c in out:
        c.pop("_merged_into", None)
    out.sort(key=lambda x: x["score"], reverse=True)
    return out


def get_index_stats() -> dict:
    col = _get_collection()
    return {"total_chunks": col.count()}
