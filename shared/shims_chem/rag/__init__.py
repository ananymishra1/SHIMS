"""
Chemistry-aware retrieval.

What makes this RAG different from a generic LangChain pipeline:

  1. Chunker preserves chemical entities. SMILES strings, reaction SMILES,
     reaction schemes, table rows, structural formulae, and CAS numbers are
     never split mid-token. Standard recursive splitters cut paracetamol's
     SMILES in half and the whole retrieval falls apart.

  2. Index is hybrid. BM25 (rank_bm25 when installed; pure-Python TF-IDF
     fallback otherwise) plus dense vectors (sentence-transformers when
     installed; hashed-bag fallback otherwise). Reciprocal-rank fusion (RRF)
     to merge — RRF is robust to score-scale mismatch between sparse/dense.

  3. Backends are pluggable. In-memory (default), SQLite (persistent, single
     file), LanceDB (when installed) — all behind the same `Store` interface.

  4. Citation-grounded. Every retrieved chunk carries (doc_id, span_start,
     span_end, source_uri), so when the brain answers from a chunk it can
     hand back a verifiable citation. We force the smart brain's summarizer
     to cite by chunk id; the verifier rejects answers that introduce facts
     without a chunk backing.
"""
from .types import Document, Chunk, Hit, Store
from .chunker import chemistry_chunk
from .index import HybridIndex
from .store import InMemoryStore, SQLiteStore, make_store

__all__ = [
    "Document", "Chunk", "Hit", "Store",
    "chemistry_chunk", "HybridIndex",
    "InMemoryStore", "SQLiteStore", "make_store",
]
