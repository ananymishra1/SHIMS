"""
Persistent memory.

Two tables, two purposes:

  episodic — a per-turn audit log. Every dispatched task gets a row with
    its user_text, intent, all tool calls + results, top routes, final
    summary, timestamps. Used to (a) show chemists what they did last week,
    (b) train the self-evolution loop on real interactions, (c) prove to
    QA that every claim was tool-verified.

  semantic — long-term concept store. When the smart brain learns something
    durable ('THF forms peroxides — always warn'), it goes here keyed by
    a concept name. The fast brain reads from this on every turn.

SQLite by default; same interface for any future Postgres/pgvector swap.
"""
from .types import EpisodicRecord, SemanticConcept, MemoryStore
from .sqlite_store import SQLiteMemoryStore, make_memory_store

__all__ = ["EpisodicRecord", "SemanticConcept", "MemoryStore",
           "SQLiteMemoryStore", "make_memory_store"]
