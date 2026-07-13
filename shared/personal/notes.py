"""Personal notes with vector search for SHIMS AI.

Uses sqlite-vec (if available) or simple FTS fallback for semantic search.
"""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from shared.config import settings

DB_PATH = Path(settings.database_path).parent / "shims_notes.sqlite3"


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(DB_PATH))
    con.row_factory = sqlite3.Row
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            body TEXT NOT NULL,
            tags TEXT DEFAULT '[]',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        )
        """
    )
    con.execute("CREATE INDEX IF NOT EXISTS idx_notes_title ON notes(title)")
    # Simple FTS table for keyword search (sqlite-vec can be added later)
    con.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
            title, body, content='notes', content_rowid='id'
        )
        """
    )
    # Triggers to keep FTS in sync
    con.execute(
        """
        CREATE TRIGGER IF NOT EXISTS notes_ai AFTER INSERT ON notes BEGIN
          INSERT INTO notes_fts(rowid, title, body) VALUES (new.id, new.title, new.body);
        END
        """
    )
    con.execute(
        """
        CREATE TRIGGER IF NOT EXISTS notes_ad AFTER DELETE ON notes BEGIN
          INSERT INTO notes_fts(notes_fts, rowid, title, body) VALUES ('delete', old.id, old.title, old.body);
        END
        """
    )
    con.execute(
        """
        CREATE TRIGGER IF NOT EXISTS notes_au AFTER UPDATE ON notes BEGIN
          INSERT INTO notes_fts(notes_fts, rowid, title, body) VALUES ('delete', old.id, old.title, old.body);
          INSERT INTO notes_fts(rowid, title, body) VALUES (new.id, new.title, new.body);
        END
        """
    )
    return con


@dataclass
class Note:
    id: int
    title: str
    body: str
    tags: list[str]
    created_at: float
    updated_at: float

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "body": self.body,
            "tags": self.tags,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def create_note(title: str, body: str, tags: Optional[list[str]] = None) -> int:
    now = time.time()
    tags = tags or []
    with _connect() as con:
        cur = con.execute(
            "INSERT INTO notes(title, body, tags, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (title, body, json.dumps(tags), now, now),
        )
        con.commit()
        return cur.lastrowid or 0


def update_note(note_id: int, title: str | None = None, body: str | None = None, tags: list[str] | None = None) -> bool:
    with _connect() as con:
        row = con.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
        if not row:
            return False
        title = title if title is not None else row["title"]
        body = body if body is not None else row["body"]
        tags = json.dumps(tags) if tags is not None else row["tags"]
        con.execute(
            "UPDATE notes SET title = ?, body = ?, tags = ?, updated_at = ? WHERE id = ?",
            (title, body, tags, time.time(), note_id),
        )
        con.commit()
        return True


def list_notes(q: str | None = None, limit: int = 100) -> list[Note]:
    if q and q.strip():
        with _connect() as con:
            rows = con.execute(
                "SELECT n.* FROM notes n JOIN notes_fts f ON n.id = f.rowid WHERE notes_fts MATCH ? ORDER BY rank LIMIT ?",
                (q.strip(), limit),
            ).fetchall()
    else:
        with _connect() as con:
            rows = con.execute(
                "SELECT * FROM notes ORDER BY updated_at DESC LIMIT ?", (limit,)
            ).fetchall()
    return [
        Note(
            id=r["id"],
            title=r["title"],
            body=r["body"],
            tags=json.loads(r["tags"]),
            created_at=r["created_at"],
            updated_at=r["updated_at"],
        )
        for r in rows
    ]


def get_note(note_id: int) -> Note | None:
    with _connect() as con:
        row = con.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
    if not row:
        return None
    return Note(
        id=row["id"],
        title=row["title"],
        body=row["body"],
        tags=json.loads(row["tags"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def delete_note(note_id: int) -> bool:
    with _connect() as con:
        cur = con.execute("DELETE FROM notes WHERE id = ?", (note_id,))
        con.commit()
        return cur.rowcount > 0
