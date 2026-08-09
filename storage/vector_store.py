import hashlib
import json
import math
import threading
from pathlib import Path

import chromadb
import numpy as np
from chromadb import Documents, EmbeddingFunction, Embeddings

from common import safe_print

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

# Streamlit serves each session on its own thread in one process; this guards
# the lazy init (two cold sessions must not both build the index) and keeps
# corpus rebuilds from interleaving with a concurrent runtime ingest.
# Reentrant because init itself goes _get_collection → index_all_documents →
# index_document → _get_collection.
_index_lock = threading.RLock()


class MultilingualMiniLM(EmbeddingFunction):
    """Hebrew-capable embeddings via paraphrase-multilingual-MiniLM-L12-v2.

    Runs the quantized (quint8, ~120MB) ONNX export directly with
    onnxruntime + tokenizers — both already pulled in by chromadb — so no
    PyTorch / sentence-transformers dependency is added. The English-only
    all-MiniLM-L6-v2 default scored noticeably worse on Hebrew retrieval.
    """

    _REPO = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    # 8, not 16: the batch is a cache-miss/boot path only, and one 16×512
    # padded batch was the transient allocation peak that (kept alive by the
    # ORT arena, see below) parked the process ~500MB higher for life.
    _BATCH = 8

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
        # The default CPU memory arena never returns freed activation memory
        # to the OS: one full-length embed batch grew RSS by ~500MB and it
        # stayed for the life of the process (measured 2026-07-27, the day a
        # single question OOM-killed the 1024MB Fly machine at 871MB). With
        # the arena off the same batch releases back to ~baseline; per-query
        # embeds are unaffected and batch embeds only run at boot/cache-miss,
        # where the extra seconds don't matter.
        so = ort.SessionOptions()
        so.enable_cpu_mem_arena = False
        so.enable_mem_pattern = False
        self._session = ort.InferenceSession(
            model_path, sess_options=so, providers=["CPUExecutionProvider"]
        )
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
    """Single shared embedding function — tokenizer + ONNX session cost ~430MB
    resident (the 250K-vocab tokenizer alone is ~260MB), so it must never be
    instantiated twice. The lock is load-bearing, not defensive: two cold
    sessions' first questions used to race the None-check and build TWO
    stacks — +800MB on a 1024MB machine, instant OOM."""
    global _ef
    if _ef is None:
        with _index_lock:
            if _ef is None:
                _ef = MultilingualMiniLM()
    return _ef


def warm_ef() -> None:
    """Load the embedding stack and run one dummy query at BOOT, behind the
    platform health check — not inside the first soldier's question. Before
    this, boot sat at ~190MB and the first retrieve() jumped the process by
    ~430MB mid-request; combined with the answer pipeline's own allocations
    that is exactly the 871MB profile the 2026-07-27 OOM kill showed."""
    _get_ef()(["חימום"])


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
                # a cache built by a different embedding model must be
                # discarded wholesale — serving its vectors would silently
                # corrupt every similarity score (or crash retrieve() on a
                # dimension mismatch)
                if "model" in data.files and str(data["model"][0]) == MultilingualMiniLM.name():
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
        np.savez_compressed(
            _EMB_CACHE_PATH,
            keys=keys,
            vectors=vectors,
            model=np.array([MultilingualMiniLM.name()]),
        )
        _emb_cache_dirty = False
    except Exception:
        pass  # cache is an optimization; failing to persist it must not break indexing


def _embed_cached(texts: list[str]) -> list[list[float]]:
    """Embeddings for texts, from the cache where possible.

    Only marks the cache dirty — persisting is the caller's call (indexing
    entry points save once at the end, not once per document).
    """
    global _emb_cache_dirty
    cache = _get_emb_cache()
    keys = [_text_key(t) for t in texts]
    missing = [i for i, k in enumerate(keys) if k not in cache]
    if missing:
        fresh = _get_ef()([texts[i] for i in missing])
        for i, vec in zip(missing, fresh):
            cache[keys[i]] = np.asarray(vec, dtype=np.float32)
        _emb_cache_dirty = True
    return [cache[k].tolist() for k in keys]


def _get_collection() -> chromadb.Collection:
    global _client, _collection
    with _index_lock:
        if _collection is None:
            _client = chromadb.EphemeralClient()
            # No embedding_function on purpose: every upsert passes explicit
            # embeddings and queries never go through col.query (retrieve()
            # scans the corpus itself), while an attached custom EF makes
            # chroma rebuild it — a fresh ~1.6s ONNX session load — on every
            # single upsert call (~50s across 16 documents at boot).
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
    with _index_lock:
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


def index_document(doc: dict, save_cache: bool = True) -> int:
    """Index all clauses from a document. Returns number of chunks added.

    `save_cache=False` defers persisting the embedding cache to the caller —
    used by index_all_documents so a cold re-embed writes the compressed npz
    once instead of once per document.
    """
    with _index_lock:
        return _index_document_locked(doc, save_cache)


def _index_document_locked(doc: dict, save_cache: bool) -> int:
    global _corpus, _vocab, _folded_corpus_cache
    _corpus = None  # upserts invalidate the in-memory corpus cache
    _vocab = None   # ...and the typo-gate vocabulary derived from it
    _folded_corpus_cache = None  # ...and the folded copy df counting reads
    _df_counts.clear()           # ...and the corpus term frequencies
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

    # Suggested questions are indexed as retrieval *anchors* only: a user
    # question phrased like one of them gives a sharp question-to-question
    # match even when the document's extracted text is RTL-mangled (the
    # 31.0703 travel order) or tiny (the 77-word Passover amendment). They
    # carry no answer content, so retrieve() only uses them to boost the
    # document's real chunks and never returns them as context.
    # Two storage formats: legacy flat list, or {role: [questions]} (all
    # roles' questions are anchors — role scoping happens via doc_ids).
    # `anchor_questions` are extra retrieval-only anchors that never appear
    # in the UI — phrasing bridges kept when the display questions were
    # rewritten per-role (the golden gate caught their loss).
    sq = doc.get("suggested_questions")
    flat, seen = [], set()
    if isinstance(sq, dict):
        role_lists = [qs for qs in sq.values() if isinstance(qs, list)]
    else:
        role_lists = [sq or []]
    for qs in role_lists + [doc.get("anchor_questions") or []]:
        for q in qs:
            if isinstance(q, str) and q not in seen:
                seen.add(q)
                flat.append(q)
    for i, q in enumerate(flat):
        if not isinstance(q, str) or len(q.strip()) < 12:
            continue
        ids.append(f"{doc_id}__sq{i}")
        texts.append(f"{title}\n{q.strip()}")
        metas.append({
            "doc_id": doc_id,
            "title": title,
            "section": "sq",
            "clause": str(i),
            "tags": "",
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
    if save_cache:
        _save_emb_cache()
    return len(ids)


def index_all_documents(json_dir: Path | None = None) -> int:
    if json_dir is None:
        json_dir = Path(__file__).parent / "json_store"
    total = 0
    for f in sorted(json_dir.glob("*.json")):
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
            total += index_document(doc, save_cache=False)
        except Exception as e:
            # print() with Hebrew raises UnicodeEncodeError on a cp1252 console
            # (the _reingest.py path), which would abort the whole index build
            # instead of skipping one bad file — safe_print survives it
            safe_print(f"שגיאה באינדוקס {f.name}: {e}")
    _save_emb_cache()
    return total


# How much a routed document's real chunks gain.
#
# Deliberately a BONUS, not a filter: scoping retrieval to the router's picks
# measured slightly better on paper (86.1% vs 85.9% on the anchor-stripped
# hold-out) but bought it by creating 22 new failures — when the three picks
# are wrong the right order was deleted before scoring and nothing downstream
# can recover it. A bonus can only reorder, never exclude.
#
# 0.05 is the largest value that breaks none of the 382 questions that pass
# today. The two gates pull against each other, measured:
#
#   boost   anchored gate    anchor-stripped hold-out
#   0.00      382/382              273 (71.5%)
#   0.05      382/382              304 (79.6%)   ← here
#   0.08      381/382              312 (81.7%)
#   0.10      381/382              318 (83.2%)
#   0.15      378/382              328 (85.9%)
#
# Raising it trades questions that work today for questions nobody has
# phrased yet — a real gain on paper, but the regressions are the ones users
# would notice. The four that break at 0.15 are all order-boundary cases
# (traffic offence → discipline vs licence suspension, alcohol → intoxicants
# vs discipline); anchor them first, then this can go up.
_ROUTE_BOOST = 0.05


def retrieve(
    query: str,
    n_results: int = 10,
    max_per_doc: int = 4,
    doc_ids: list[str] | None = None,
    top_doc_depth: int = 4,
    boost_docs: set[str] | None = None,
) -> list[dict]:
    """Return the globally most relevant chunks, capped per document.

    `top_doc_depth` guarantees the LEADING document that many of its own
    best chunks within the n_results budget. Without it, a long order that
    clearly wins the ranking often lands a single chunk while other
    documents' anchor-lifted best chunks fill the rest — and the one chunk
    is frequently the wrong part of the right order (a punishment table
    when the question is about attendance). Repeated "right doc, wrong
    chunk" failures (33.0111 reporting, 36.0413 sick leave, 33.0302
    no-show) all trace to this.

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

    `boost_docs` is the document router's shortlist (see backend._route_docs):
    those documents' chunks get _ROUTE_BOOST added to their score. It is a
    hint, not a scope — an empty or wrong shortlist leaves retrieval exactly
    as it would have been.
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
    except Exception as e:
        # an embed failure returns no chunks, which reads downstream as "no
        # documents" and the model answers "המידע לא קיים" — log it so an
        # infrastructure fault isn't silently disguised as missing content
        safe_print(f"[vector_store] query embedding failed: {e!r}")
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

    # Router bonus — real chunks only, never anchors. Boosting anchors too
    # cost 12 of the 382 gated questions: an anchor's score becomes its
    # document's score in the fold below, so a boosted anchor on a
    # mis-routed order outranked the correctly-anchored one. Anchors and the
    # router answer different halves of the problem — a human wrote an anchor
    # for the phrasings someone anticipated, the router covers everything
    # else — so the router must not be able to outbid a live anchor match.
    # On the anchor-stripped hold-out the two forms are identical by
    # construction, so this costs nothing there (85.9% either way).
    if boost_docs:
        for c in candidates:
            if c["doc_id"] in boost_docs and c["section"] != "sq":
                c["score"] = round(c["score"] + _ROUTE_BOOST, 3)

    # Fold suggested-question anchors into their document's real chunks: a
    # strong question-to-question match lifts one of the doc's real chunks to
    # the anchor's rank, and the anchor itself is dropped — it carries no
    # answer content, so it must never be handed to the LLM as context.
    # The lift lands on the doc's best KEY-FACTS chunk when one exists, not
    # on its best raw chunk: an anchor win means the raw chunks all scored
    # poorly (a long anecdotal question dilutes cosine), and the best raw
    # chunk at that point is arbitrary noise — the live "לאיים במשפט" miss
    # handed the model a colophon/dates chunk while the curated offense
    # summary sat at rank 404. Curated key-facts are the answers the anchor
    # questions were written against, so they are the doc's representative.
    #
    # WHICH key-facts clause gets the lift is a coin flip, though. An anchor win
    # means every clause of that doc scored poorly against the query — the
    # dilution is why the anchor had to carry it at all — so at clause
    # granularity they all sit inside the model's noise: 33.0336's five spread
    # over 0.25–0.33 cosine, and ranking them by the winning anchor instead of
    # the query is the same coin (0.69–0.77 across the same five, measured).
    # Both 2026-08-05 pilot misses landed the right order and the wrong
    # paragraph that way: "יש חוק התיישנות?" lifted the reserve-eligibility
    # clause over the filing deadline, "המפקד ביטל לי תור" lifted
    # sick-on-call-up-day over the appointment rule. So the winning document
    # stops choosing and hands over its key-facts section as one merged block
    # (below). Merged, not promoted clause by clause: individually they took
    # four of the eight slots, which cost 12 probes and the source-page link.
    sq_best: dict[str, float] = {}
    best_real: dict[str, dict] = {}
    kf_by_doc: dict[str, list[dict]] = {}
    real_candidates = []
    for c in candidates:
        if c["section"] == "sq":
            sq_best[c["doc_id"]] = max(sq_best.get(c["doc_id"], -1.0), c["score"])
        else:
            real_candidates.append(c)
            cur = best_real.get(c["doc_id"])
            if cur is None or c["score"] > cur["score"]:
                best_real[c["doc_id"]] = c
            if c["section"].startswith("key-facts"):
                kf_by_doc.setdefault(c["doc_id"], []).append(c)
    candidates = real_candidates
    lifted: dict[str, dict] = {}
    for doc_id, anchor_score in sq_best.items():
        best = best_real.get(doc_id)
        if best is None or anchor_score <= best["score"]:
            continue
        kfs = kf_by_doc.get(doc_id)
        # the doc's highest-scoring clause carries the lift and keeps its own
        # identity — it is what the source card cites and the only one with a
        # chance of a page mapping
        target = max(kfs, key=lambda x: x["score"]) if kfs else best
        target["score"] = anchor_score
        lifted[doc_id] = target

    # ...and the WINNING document's remaining clauses fold into that block. Only
    # the winner: it is the order the answer is built on, so a wrong clause
    # there is the refusal, while a supporting order contributing its
    # second-best clause costs nothing. Merging every lifted document instead
    # grew retrieved context 38% (837→1157 words over the eval sets) for no
    # additional probe passing — real money at ~$0.04 a question.
    winner = max(candidates, key=lambda c: c["score"], default=None)
    lead = lifted.get(winner["doc_id"]) if winner is not None else None
    if lead is not None:
        rest = sorted(
            (c for c in kf_by_doc.get(winner["doc_id"], []) if c is not lead),
            key=lambda x: x["score"], reverse=True,
        )
        folded, words = set(), len(lead["text"].split())
        for extra in rest:
            body = extra["text"].split("\n", 1)[-1]
            if words + len(body.split()) > _KEY_FACTS_MERGE_WORDS:
                break
            lead["text"] += "\n" + body
            words += len(body.split())
            folded.add(id(extra))
        if folded:
            candidates = [c for c in candidates if id(c) not in folded]

    chunks = []
    per_doc_count: dict[str, int] = {}
    for c in sorted(candidates, key=lambda x: x["score"], reverse=True):
        doc_id = c["doc_id"]
        if per_doc_count.get(doc_id, 0) >= max_per_doc:
            continue
        per_doc_count[doc_id] = per_doc_count.get(doc_id, 0) + 1
        chunks.append(c)

    selected = chunks[:n_results]
    # winner depth: swap the lowest-ranked chunks of OTHER docs for the
    # leading doc's next-best chunks until it holds top_doc_depth slots
    if selected and top_doc_depth > 1:
        lead = selected[0]["doc_id"]
        lead_extras = [c for c in chunks[n_results:] if c["doc_id"] == lead]
        need = min(top_doc_depth, max_per_doc) - sum(1 for c in selected if c["doc_id"] == lead)
        for extra in lead_extras[:max(0, need)]:
            for i in range(len(selected) - 1, -1, -1):
                if selected[i]["doc_id"] != lead:
                    selected.pop(i)
                    selected.append(extra)
                    break
            else:
                break
        selected.sort(key=lambda x: x["score"], reverse=True)

    return _stitch_adjacent_chunks(_expand_neighbors(selected, corpus))


# Hebrew single-letter prefixes (ה,ו,ב,ל,מ,כ,ש) that glue onto content words —
# stripped from query terms so "בריתוק" still matches a chunk containing "ריתוק".
_HEB_PREFIXES = "הובלמכש"
_LEXICAL_WEIGHT = 0.25
# Word budget for the winning document's merged key-facts block (see
# retrieve()). One raw window, so the block costs the model about what the
# single clause it replaced plus its neighbours would have: at two windows'
# worth, retrieved context ran 1157 words against a 837-word baseline over the
# eval sets, and that is real money at ~$0.04 a question. The median key-facts
# section is 109 words and fits whole; the long ones lose their least relevant
# clauses, which is what the ranking is for.
_KEY_FACTS_MERGE_WORDS = _CHUNK_WORDS
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


# Lazy vocabulary of every match-form in the indexed corpus (finals-folded,
# punctuation-stripped words). Built once (~0.2s over ~400K corpus words) and
# invalidated together with the corpus cache on upsert.
_vocab: set[str] | None = None

# Corpus-level term statistics for the lexical bonus. Counted over the WHOLE
# index, once, instead of over each query's candidate set. Candidate-set df
# made every score a function of who else happened to be scored: the same
# question scored differently for a soldier and a commander, and every
# ingested chunk nudged every score in the corpus. Measured on the 11-order
# round: 382 of 382 eval questions changed score, and PM-33.0302 slid from
# rank 2 to rank 4 on "כמה מחבוש אפשר לחטוף על משפט בצבא?" — a golden case
# lost to nothing but arithmetic.
#
# Anchor chunks (section == "sq") are counted like any other chunk. Leaving
# them out is defensible — they are questions, not order text, and they are
# 43% of the index — but it measures worse: 273/282 on the adversarial set
# against 278/282 with them in.
_folded_corpus_cache: list[str] | None = None
_df_counts: dict[tuple[str, ...], int] = {}

# A term appearing in 5% of the index weighs exactly 1.0. Both the term's idf
# and this reference are functions of df/N, so neither carries an N that grows:
# log(1 + N/(1+df)) ≈ log(1 + 1/(df/N)).
_IDF_REFERENCE = math.log(21.0)   # == log(1 + 1/0.05)
_IDF_CAP = 1.5
_IDF_STEP = 0.25


def _get_vocab() -> set[str]:
    global _vocab
    if _vocab is None:
        vocab = set()
        for c in _get_corpus():
            for w in c["text"].split():
                w = w.strip("?.,:;!\"'()[]").translate(_FINALS)
                if len(w) >= 2:
                    vocab.add(w)
        _vocab = vocab
    return _vocab


def has_unknown_terms(query: str) -> bool:
    """True when some content word of the query matches nothing in the corpus
    under the same variant rules retrieval itself uses (prefix stripping,
    light stemming, finals folding).

    This is the typo-normalization gate: a word no chunk contains in any form
    is either a typo or off-corpus vocabulary — both are exactly the cases
    where a Haiku spelling pass can help and the ~1.5s it costs is worth it.
    When every word is known, raw retrieval already sees everything the
    normalizer could show it, so the caller skips the LLM round-trip."""
    vocab = _get_vocab()
    if not vocab:
        return False
    for word in query.split():
        variants = _term_variants(word)
        if variants and not any(v in vocab for v in variants):
            return True
    return False


def _folded_corpus() -> list[str]:
    """Every indexed chunk's text, finals-folded once, for df counting."""
    global _folded_corpus_cache
    if _folded_corpus_cache is None:
        _folded_corpus_cache = [c["text"].translate(_FINALS) for c in _get_corpus()]
    return _folded_corpus_cache


def _term_weight(variants: set[str]) -> float:
    """Rarity weight for one query term. Three properties, each load-bearing
    for keeping the ranking still while the corpus grows:

    * df over the whole index, not over this query's candidate set — the
      weight of "מחבוש" is a property of the corpus, not of the asker's role
      or of what else happened to be scored.
    * measured against a FIXED reference rarity instead of against the other
      query terms. The old `/ sum(idf)` made every term's weight a function of
      every other term's df, so one new document moved the entire bonus for
      every query sharing any word with it.
    * snapped to a 0.25 grid. df/N moves continuously as documents arrive; the
      grid means it has to move a long way before any score changes at all.
      This is the part that actually stops the drift — ungridded, 381 of 382
      eval questions still shifted on the 87→98 step; gridded, 116 do.

    Capped at 1.5 so a term nothing else contains cannot outbid the vector
    score outright. The cap is a plateau: 1.4, 1.5 and 1.6 measure identically.
    """
    key = tuple(sorted(variants))
    df = _df_counts.get(key)
    if df is None:
        df = sum(1 for t in _folded_corpus() if any(v in t for v in variants))
        if len(_df_counts) > 20000:
            _df_counts.clear()   # query vocabulary is unbounded; this is a cache
        _df_counts[key] = df
    w = math.log(1.0 + len(_folded_corpus()) / (1.0 + df)) / _IDF_REFERENCE
    return round(min(w, _IDF_CAP) / _IDF_STEP) * _IDF_STEP


def _lexical_rerank(query: str, candidates: list[dict]) -> None:
    """Blend a lexical-overlap bonus into each candidate's vector score.

    Pure vector retrieval dilutes rare, decisive terms (e.g. "ריתוק משקי")
    inside long mean-pooled chunks, so the one document that actually answers
    the question can rank below generically-similar chunks. Each query term is
    weighted by how rare it is IN THE CORPUS (see _term_weight), and candidates
    containing the rare terms get a bonus of up to 1.5 * _LEXICAL_WEIGHT.
    Mutates scores in place.
    """
    terms = [v for v in (_term_variants(w) for w in query.split()) if v]
    if not terms or not candidates:
        return

    weights = [_term_weight(v) for v in terms]
    texts = [c["text"].translate(_FINALS) for c in candidates]
    matches = [
        [any(v in text for v in variants) for text in texts]
        for variants in terms
    ]
    for i, c in enumerate(candidates):
        overlap = sum(w for w, m in zip(weights, matches) if m[i])
        c["score"] = round(c["score"] + _LEXICAL_WEIGHT * overlap / len(terms), 3)


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
