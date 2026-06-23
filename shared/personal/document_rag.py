"""Document RAG (Retrieval-Augmented Generation) for SHIMS Personal AI.

Allows users to upload PDFs, DOCX, TXT files and ask questions about them.
Uses simple keyword extraction + sentence chunking as a lightweight fallback.
Can be upgraded to embeddings + vector DB later.
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from shared.config import settings

DB_PATH = Path(settings.database_path).parent / "shims_rag.sqlite3"
DOCS_DIR = Path(settings.database_path).parent / "rag_documents"


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(DB_PATH))
    con.row_factory = sqlite3.Row
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS doc_chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            doc_name TEXT NOT NULL,
            chunk_text TEXT NOT NULL,
            chunk_index INTEGER NOT NULL,
            created_at REAL NOT NULL
        )
        """
    )
    con.execute("CREATE INDEX IF NOT EXISTS idx_doc_name ON doc_chunks(doc_name)")
    return con


@dataclass
class Chunk:
    id: int
    doc_name: str
    chunk_text: str
    chunk_index: int

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "doc_name": self.doc_name,
            "chunk_text": self.chunk_text,
            "chunk_index": self.chunk_index,
        }


def _chunk_text(text: str, max_chars: int = 800, overlap: int = 100) -> list[str]:
    """Simple sentence-aware chunking."""
    sentences = re.split(r'(?<=[.!?])\s+', text)
    chunks: list[str] = []
    current = ""
    for s in sentences:
        if len(current) + len(s) < max_chars:
            current += " " + s if current else s
        else:
            if current:
                chunks.append(current.strip())
            current = s
    if current:
        chunks.append(current.strip())
    # Add overlap
    if overlap > 0 and len(chunks) > 1:
        overlapped: list[str] = []
        for i, c in enumerate(chunks):
            if i > 0:
                prev_end = chunks[i - 1][-overlap:]
                c = prev_end + " " + c
            overlapped.append(c.strip())
        chunks = overlapped
    return chunks


def ingest_document(file_name: str, text: str) -> int:
    """Chunk and store a document. Returns number of chunks stored."""
    chunks = _chunk_text(text)
    with _connect() as con:
        # Remove old version of same doc
        con.execute("DELETE FROM doc_chunks WHERE doc_name = ?", (file_name,))
        for idx, chunk in enumerate(chunks):
            con.execute(
                "INSERT INTO doc_chunks(doc_name, chunk_text, chunk_index, created_at) VALUES (?, ?, ?, ?)",
                (file_name, chunk, idx, time.time()),
            )
        con.commit()
    return len(chunks)


def search_chunks(query: str, top_k: int = 5) -> list[Chunk]:
    """Keyword-based retrieval. Upgrade to embeddings for semantic search."""
    words = [w.lower() for w in re.findall(r'\b\w+\b', query) if len(w) > 2]
    if not words:
        return []

    with _connect() as con:
        # Simple scoring: count matching words per chunk
        rows = con.execute(
            "SELECT * FROM doc_chunks ORDER BY created_at DESC LIMIT 500"
        ).fetchall()

    scored: list[tuple[int, Chunk]] = []
    for r in rows:
        text_lower = r["chunk_text"].lower()
        score = sum(1 for w in words if w in text_lower)
        if score > 0:
            scored.append((score, Chunk(
                id=r["id"],
                doc_name=r["doc_name"],
                chunk_text=r["chunk_text"],
                chunk_index=r["chunk_index"],
            )))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [c for _, c in scored[:top_k]]


def rag_query(query: str, llm_generate_fn: Any) -> dict[str, Any]:
    """Full RAG pipeline: retrieve chunks, build prompt, generate answer."""
    chunks = search_chunks(query, top_k=5)
    if not chunks:
        return {
            "answer": "I don't have any documents that match your question. Try uploading a document first.",
            "sources": [],
        }

    context = "\n\n".join(
        f"[From {c.doc_name}]: {c.chunk_text}" for c in chunks
    )
    prompt = (
        "Use the following document excerpts to answer the question. "
        "If the answer is not in the excerpts, say so honestly.\n\n"
        f"{context}\n\n"
        f"Question: {query}\n\nAnswer:"
    )
    answer = llm_generate_fn(prompt)
    return {
        "answer": answer,
        "sources": [c.to_dict() for c in chunks],
    }


def list_documents() -> list[str]:
    with _connect() as con:
        rows = con.execute(
            "SELECT DISTINCT doc_name FROM doc_chunks ORDER BY doc_name"
        ).fetchall()
    return [r["doc_name"] for r in rows]


def delete_document(doc_name: str) -> bool:
    with _connect() as con:
        cur = con.execute("DELETE FROM doc_chunks WHERE doc_name = ?", (doc_name,))
        con.commit()
        return cur.rowcount > 0
