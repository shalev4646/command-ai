import json
from pathlib import Path

import chromadb
from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2

_COLLECTION = "idf_orders"

_client: chromadb.ClientAPI | None = None
_collection: chromadb.Collection | None = None


def _get_collection() -> chromadb.Collection:
    global _client, _collection
    if _collection is None:
        _client = chromadb.EphemeralClient()
        _collection = _client.get_or_create_collection(
            name=_COLLECTION,
            embedding_function=ONNXMiniLM_L6_V2(),
            metadata={"hnsw:space": "cosine"},
        )
        index_all_documents()
    return _collection


def index_document(doc: dict) -> int:
    """Index all clauses from a document. Returns number of chunks added."""
    col = _get_collection()
    doc_id = doc.get("document_id", "unknown")
    title = doc.get("title", "")

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


def _unique_doc_ids() -> list[str]:
    col = _get_collection()
    if col.count() == 0:
        return []
    all_meta = col.get(limit=1000, include=["metadatas"])["metadatas"]
    return list({m.get("doc_id") for m in all_meta if m.get("doc_id") and m.get("doc_id") != "UNKNOWN"})


def retrieve(query: str, n_results: int = 6) -> list[dict]:
    """Return top chunks per document, ensuring every document is represented."""
    col = _get_collection()
    if col.count() == 0:
        return []

    doc_ids = _unique_doc_ids()
    per_doc = max(2, n_results // max(len(doc_ids), 1))

    chunks = []
    seen_ids = set()

    for doc_id in doc_ids:
        try:
            results = col.query(
                query_texts=[query],
                n_results=min(per_doc, col.count()),
                where={"doc_id": doc_id},
                include=["documents", "metadatas", "distances"],
            )
            for doc_text, meta, dist in zip(
                results["documents"][0],
                results["metadatas"][0],
                results["distances"][0],
            ):
                chunk_key = doc_text[:80]
                if chunk_key not in seen_ids:
                    seen_ids.add(chunk_key)
                    chunks.append({
                        "text": doc_text,
                        "doc_id": meta.get("doc_id"),
                        "title": meta.get("title"),
                        "section": meta.get("section"),
                        "clause": meta.get("clause"),
                        "score": round(1 - dist, 3),
                    })
        except Exception:
            pass

    chunks.sort(key=lambda x: x["score"], reverse=True)
    return chunks[:n_results]


def get_index_stats() -> dict:
    col = _get_collection()
    return {"total_chunks": col.count()}
