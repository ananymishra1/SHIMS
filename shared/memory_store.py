from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator, Optional

MEMORY_DB = Path("storage/shims_memory.sqlite3")


@contextmanager
def _connect() -> Generator[sqlite3.Connection, None, None]:
    """Open the memory database and close it on exit."""
    MEMORY_DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(MEMORY_DB)
    con.row_factory = sqlite3.Row
    try:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                tags TEXT DEFAULT '[]',
                pinned INTEGER DEFAULT 0,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )
        con.execute("CREATE INDEX IF NOT EXISTS idx_mem_kind ON memories(kind)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_mem_key ON memories(key)")
        con.commit()
        yield con
    finally:
        con.close()


def save_memory(kind: str, key: str, value: str, tags: Optional[list[str]] = None, pinned: bool = False) -> dict:
    now = time.time()
    tags = tags or []
    with _connect() as con:
        cur = con.execute(
            "INSERT INTO memories(kind, key, value, tags, pinned, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (kind, key, value, json.dumps(tags), int(pinned), now, now),
        )
        memory_id = cur.lastrowid
    return get_memory(memory_id) or {}


def get_memory(memory_id: int) -> dict | None:
    with _connect() as con:
        row = con.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
    return dict(row) if row else None


def list_memories(kind: str | None = None, q: str | None = None) -> list[dict]:
    sql = "SELECT * FROM memories WHERE 1=1"
    args: list[str] = []
    if kind:
        sql += " AND kind = ?"
        args.append(kind)
    if q:
        sql += " AND (key LIKE ? OR value LIKE ? OR tags LIKE ?)"
        like = f"%{q}%"
        args.extend([like, like, like])
    sql += " ORDER BY pinned DESC, updated_at DESC LIMIT 200"
    with _connect() as con:
        rows = con.execute(sql, args).fetchall()
    return [dict(r) for r in rows]


def forget_memory(memory_id: int) -> bool:
    with _connect() as con:
        cur = con.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
    return cur.rowcount > 0
