"""Vector memory — semantic search using lightweight embeddings."""
from __future__ import annotations

import concurrent.futures
import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Optional

import numpy as np

VECTOR_DB = Path(__file__).resolve().parent.parent.parent / "data" / "state" / "governor_vectors.sqlite3"
VECTOR_DB.parent.mkdir(parents=True, exist_ok=True)

# Lazy-loaded sentence-transformer model. Loading is kicked off in the background
# so chat turns never block on the ~30-60 s first load.
_embedding_model: Any = None
_embedding_loading = False
_embedding_lock = threading.Lock()
_EMBEDDING_TIMEOUT_SECONDS = float(__import__("os").getenv("SHIMS_EMBED_TIMEOUT_SECONDS", "3"))


def _connect() -> sqlite3.Connection:
    con = sqlite3.connect(str(VECTOR_DB))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS vector_embeddings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_type TEXT NOT NULL,
            source_id TEXT NOT NULL,
            embedding BLOB NOT NULL,
            text_content TEXT,
            metadata_json TEXT,
            created_at REAL NOT NULL
        )
        """
    )
    con.execute("CREATE INDEX IF NOT EXISTS idx_vec_source ON vector_embeddings(source_type, source_id)")
    con.commit()
    return con


def _load_model_sync() -> Any:
    try:
        from sentence_transformers import SentenceTransformer
        return SentenceTransformer("all-MiniLM-L6-v2")
    except Exception:
        return "unavailable"


def _get_embedding_model() -> Any:
    """Return the loaded model, or None if it is not ready yet.

    The first call starts a background load. Subsequent calls return immediately.
    """
    global _embedding_model, _embedding_loading
    if _embedding_model is not None:
        return _embedding_model if _embedding_model != "unavailable" else None
    with _embedding_lock:
        if _embedding_model is not None:
            return _embedding_model if _embedding_model != "unavailable" else None
        if not _embedding_loading:
            _embedding_loading = True

            def _load():
                global _embedding_model, _embedding_loading
                try:
                    _embedding_model = _load_model_sync()
                finally:
                    _embedding_loading = False

            threading.Thread(target=_load, daemon=True).start()
    return None


def embed_text(text: str) -> Optional[np.ndarray]:
    """Generate embedding vector for text. Returns None if the model is not ready or encoding times out."""
    model = _get_embedding_model()
    if not model:
        return None
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(
                lambda: model.encode(str(text)[:2000], show_progress_bar=False, convert_to_numpy=True).astype(np.float32)
            )
            return future.result(timeout=_EMBEDDING_TIMEOUT_SECONDS)
    except Exception:
        return None


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Compute cosine similarity between two vectors."""
    norm = np.linalg.norm(a) * np.linalg.norm(b)
    if norm == 0:
        return 0.0
    return float(np.dot(a, b) / norm)


def store_embedding(
    source_type: str,
    source_id: str,
    text: str,
    metadata: Optional[dict[str, Any]] = None,
) -> bool:
    """Store a vector embedding for a piece of text."""
    vec = embed_text(text)
    if vec is None:
        return False

    import time
    with _connect() as con:
        con.execute(
            """
            INSERT INTO vector_embeddings (source_type, source_id, embedding, text_content, metadata_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                source_type,
                str(source_id),
                vec.tobytes(),
                text[:2000],
                json.dumps(metadata or {}, ensure_ascii=False, default=str),
                time.time(),
            ),
        )
        con.commit()
    return True


def search_vectors(query: str, limit: int = 8) -> list[dict[str, Any]]:
    """Semantic search across stored embeddings (vectorized for speed)."""
    query_vec = embed_text(query)
    if query_vec is None:
        return []

    with _connect() as con:
        rows = con.execute("SELECT id, source_type, source_id, embedding, text_content, metadata_json FROM vector_embeddings").fetchall()

    if not rows:
        return []

    embeddings = np.vstack([np.frombuffer(row["embedding"], dtype=np.float32) for row in rows])
    if embeddings.shape[1] != query_vec.shape[0]:
        return []

    query_norm = np.linalg.norm(query_vec)
    if query_norm == 0:
        return []

    norms = np.linalg.norm(embeddings, axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        similarities = np.dot(embeddings, query_vec) / (norms * query_norm)
    similarities = np.nan_to_num(similarities, nan=0.0)

    threshold = 0.5
    candidates = np.where(similarities > threshold)[0]
    if candidates.size == 0:
        return []

    top_k = min(limit, len(candidates))
    top_indices = candidates[np.argpartition(similarities[candidates], -top_k)[-top_k:]]
    top_indices = top_indices[np.argsort(similarities[top_indices])[::-1]]

    results: list[dict[str, Any]] = []
    for idx in top_indices:
        row = rows[int(idx)]
        results.append({
            "id": row["id"],
            "source_type": row["source_type"],
            "source_id": row["source_id"],
            "text_content": row["text_content"],
            "similarity": round(float(similarities[idx]), 4),
            "metadata": json.loads(row["metadata_json"]) if row["metadata_json"] else {},
        })
    return results


def delete_embeddings(source_type: str, source_id: str) -> bool:
    with _connect() as con:
        cur = con.execute("DELETE FROM vector_embeddings WHERE source_type = ? AND source_id = ?", (source_type, source_id))
        con.commit()
    return cur.rowcount > 0


def index_document_chunks(doc_id: str, chunks: list[str], metadata: Optional[dict[str, Any]] = None) -> int:
    """Index multiple chunks from a document."""
    count = 0
    for idx, chunk in enumerate(chunks):
        if store_embedding("document_chunk", f"{doc_id}:{idx}", chunk, {**(metadata or {}), "chunk_index": idx}):
            count += 1
    return count
