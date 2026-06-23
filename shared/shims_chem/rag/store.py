"""In-memory and SQLite-backed RAG stores."""
from __future__ import annotations
import json
import sqlite3
from pathlib import Path
from typing import Any

from ..config import get_config
from .types import Chunk, Document, Store


class InMemoryStore(Store):
    def __init__(self) -> None:
        self._docs: dict[str, Document] = {}
        self._chunks: dict[str, Chunk] = {}

    def upsert_document(self, doc: Document) -> None:
        self._docs[doc.doc_id] = doc

    def upsert_chunks(self, chunks: list[Chunk]) -> None:
        for c in chunks:
            self._chunks[c.chunk_id] = c

    def all_chunks(self) -> list[Chunk]:
        return list(self._chunks.values())

    def get_chunk(self, chunk_id: str) -> Chunk | None:
        return self._chunks.get(chunk_id)

    def close(self) -> None:
        self._docs.clear()
        self._chunks.clear()


class SQLiteStore(Store):
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS docs ("
            "doc_id TEXT PRIMARY KEY, source_uri TEXT, title TEXT, text TEXT, meta TEXT)"
        )
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS chunks ("
            "chunk_id TEXT PRIMARY KEY, doc_id TEXT, text TEXT, "
            "span_start INTEGER, span_end INTEGER, source_uri TEXT, meta TEXT)"
        )
        self._conn.commit()

    def upsert_document(self, doc: Document) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO docs (doc_id, source_uri, title, text, meta) VALUES (?, ?, ?, ?, ?)",
            (doc.doc_id, doc.source_uri, doc.title, doc.text, json.dumps(doc.meta or {})),
        )
        self._conn.commit()

    def upsert_chunks(self, chunks: list[Chunk]) -> None:
        self._conn.executemany(
            "INSERT OR REPLACE INTO chunks (chunk_id, doc_id, text, span_start, span_end, source_uri, meta) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(c.chunk_id, c.doc_id, c.text, c.span_start, c.span_end,
              c.source_uri, json.dumps(c.meta or {})) for c in chunks],
        )
        self._conn.commit()

    def all_chunks(self) -> list[Chunk]:
        rows = self._conn.execute(
            "SELECT chunk_id, doc_id, text, span_start, span_end, source_uri, meta FROM chunks"
        ).fetchall()
        return [
            Chunk(chunk_id=r[0], doc_id=r[1], text=r[2],
                  span_start=r[3], span_end=r[4], source_uri=r[5],
                  meta=json.loads(r[6] or "{}"))
            for r in rows
        ]

    def get_chunk(self, chunk_id: str) -> Chunk | None:
        row = self._conn.execute(
            "SELECT chunk_id, doc_id, text, span_start, span_end, source_uri, meta FROM chunks WHERE chunk_id = ?",
            (chunk_id,),
        ).fetchone()
        if row is None:
            return None
        return Chunk(chunk_id=row[0], doc_id=row[1], text=row[2],
                     span_start=row[3], span_end=row[4],
                     source_uri=row[5], meta=json.loads(row[6] or "{}"))

    def close(self) -> None:
        self._conn.close()


def make_store(kind: str = "auto", path: str | Path | None = None) -> Store:
    cfg = get_config()
    if kind == "memory":
        return InMemoryStore()
    if kind in ("sqlite", "auto"):
        p = Path(path) if path else cfg.workspace / "rag" / "store.sqlite"
        p.parent.mkdir(parents=True, exist_ok=True)
        return SQLiteStore(p)
    raise ValueError(f"Unknown store kind: {kind}")
