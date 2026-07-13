"""Shared types for the RAG layer."""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any
import hashlib
import uuid


@dataclass
class Document:
    doc_id: str
    source_uri: str           # file path or URL
    title: str = ""
    text: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_text(cls, text: str, *, source_uri: str = "inline",
                  title: str = "", **meta) -> "Document":
        h = hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]
        return cls(doc_id=f"doc-{h}", source_uri=source_uri, title=title,
                   text=text, meta=meta)


@dataclass
class Chunk:
    chunk_id: str
    doc_id: str
    text: str
    span_start: int            # byte offset in the source doc
    span_end: int
    source_uri: str
    meta: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def new(cls, doc: Document, text: str, span_start: int, span_end: int,
            **extra_meta) -> "Chunk":
        return cls(
            chunk_id=f"ch-{uuid.uuid4().hex[:10]}",
            doc_id=doc.doc_id,
            text=text,
            span_start=span_start,
            span_end=span_end,
            source_uri=doc.source_uri,
            meta={**doc.meta, **extra_meta},
        )


@dataclass
class Hit:
    chunk: Chunk
    score: float
    source: str                # "bm25" | "dense" | "fused"


class Store(ABC):
    @abstractmethod
    def upsert_document(self, doc: Document) -> None: ...

    @abstractmethod
    def upsert_chunks(self, chunks: list[Chunk]) -> None: ...

    @abstractmethod
    def all_chunks(self) -> list[Chunk]: ...

    @abstractmethod
    def get_chunk(self, chunk_id: str) -> Chunk | None: ...

    @abstractmethod
    def close(self) -> None: ...
