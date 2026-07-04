import json
import math
from pathlib import Path

import chromadb
import numpy as np
from chromadb import Documents, EmbeddingFunction, Embeddings

_COLLECTION = "idf_orders"
_CHUNK_WORDS = 600
_OVERLAP_WORDS = 100

_client: chromadb.ClientAPI | None = None
_collection: chromadb.Collection | None = None


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


def _get_collection() -> chromadb.Collection:
    global _client, _collection
    if _collection is None:
        _client = chromadb.EphemeralClient()
        _collection = _client.get_or_create_collection(
            name=_COLLECTION,
            embedding_function=MultilingualMiniLM(),
            metadata={"hnsw:space": "cosine"},
        )
        index_all_documents()
    return _collection


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
    col = _get_collection()
    doc_id = doc.get("document_id", "unknown")
    title = doc.get("title", "")

    # Fallback: if no structured sections but raw_text exists, chunk it directly
    if not doc.get("sections") and not doc.get("annex_exceptions") and doc.get("raw_text"):
        chunks = _split_raw_text(doc["raw_text"], doc_id, title)
        if not chunks:
            return 0
        col.upsert(
            ids=[c["id"] for c in chunks],
            documents=[c["text"] for c in chunks],
            metadatas=[{k: v for k, v in c.items() if k not in ("id", "text")} for c in chunks],
        )
        return len(chunks)

    ids, texts, metas = [], [], []

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
    col.upsert(ids=ids, documents=texts, metadatas=metas)
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

    Previously this queried each document separately with a *minimum*
    chunks-per-document floor, so an irrelevant document would still force
    its way into the results and crowd out a highly relevant one that just
    happened to have more chunks (e.g. a query squarely about disciplinary
    punishments would only get 2 of that document's 22 chunks, with the
    rest of the budget spent on unrelated leave/sleep-hours chunks). Doing
    one global query and capping the *maximum* per document instead lets a
    genuinely relevant document dominate, while still giving other
    documents a chance if they score well on a broader question.

    `doc_ids`, if given, restricts the search to that set of documents —
    used to scope retrieval to whatever's relevant for the active role
    (soldier/commander/reserve).
    """
    col = _get_collection()
    count = col.count()
    if count == 0:
        return []
    if doc_ids is not None and not doc_ids:
        return []

    where = {"doc_id": {"$in": doc_ids}} if doc_ids is not None else None

    try:
        results = col.query(
            query_texts=[query],
            n_results=min(count, n_results * 4),
            where=where,
            include=["documents", "metadatas", "distances"],
        )
    except Exception:
        return []

    candidates = [
        {
            "text": doc_text,
            "doc_id": meta.get("doc_id"),
            "title": meta.get("title"),
            "section": meta.get("section"),
            "clause": meta.get("clause"),
            "score": round(1 - dist, 3),
        }
        for doc_text, meta, dist in zip(
            results["documents"][0],
            results["metadatas"][0],
            results["distances"][0],
        )
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

    return _stitch_adjacent_chunks(chunks[:n_results])


# Hebrew single-letter prefixes (ה,ו,ב,ל,מ,כ,ש) that glue onto content words —
# stripped from query terms so "בריתוק" still matches a chunk containing "ריתוק".
_HEB_PREFIXES = "הובלמכש"
_LEXICAL_WEIGHT = 0.25


def _lexical_rerank(query: str, candidates: list[dict]) -> None:
    """Blend a lexical-overlap bonus into each candidate's vector score.

    Pure vector retrieval dilutes rare, decisive terms (e.g. "ריתוק משקי")
    inside 600-word chunks, so the one document that actually answers the
    question can rank below generically-similar chunks. Each query term is
    weighted by its rarity *within the candidate pool* (a poor man's IDF —
    a term found in only one candidate is near-decisive, one found in all
    of them says nothing), and candidates containing the rare terms get a
    proportional boost of up to _LEXICAL_WEIGHT. Mutates scores in place.
    """
    terms = []
    for w in query.split():
        w = w.strip("?.,:;!\"'()[]")
        while len(w) > 3 and w[0] in _HEB_PREFIXES:
            w = w[1:]
        if len(w) >= 3:
            terms.append(w)
    if not terms or not candidates:
        return

    n = len(candidates)
    matches = {t: [t in c["text"] for c in candidates] for t in terms}
    idf = {t: math.log(1 + n / (1 + sum(matches[t]))) for t in terms}
    total = sum(idf.values())
    if total <= 0:
        return
    for i, c in enumerate(candidates):
        overlap = sum(idf[t] for t in terms if matches[t][i])
        c["score"] = round(c["score"] + _LEXICAL_WEIGHT * overlap / total, 3)


def _stitch_adjacent_chunks(chunks: list[dict]) -> list[dict]:
    """Merge consecutive raw-text chunks of the same document into one block.

    Raw-text docs are split into overlapping word windows (600 words, 100
    shared with the next window). When retrieval picks neighbouring windows
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
