"""SQLite memory store."""
from __future__ import annotations
import json
import sqlite3
from pathlib import Path

from ..config import get_config
from .types import EpisodicRecord, MemoryStore, SemanticConcept


class SQLiteMemoryStore(MemoryStore):
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS episodes ("
            "task_id TEXT PRIMARY KEY, ts REAL, user_text TEXT, intent TEXT, "
            "tool_calls TEXT, final_summary TEXT, detail TEXT, ok INTEGER, elapsed_s REAL)"
        )
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS concepts ("
            "concept_id TEXT PRIMARY KEY, name TEXT, body TEXT, confidence REAL, "
            "learned_at REAL, last_used REAL, source_task_ids TEXT)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_concepts_name ON concepts(name)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_episodes_ts ON episodes(ts DESC)"
        )
        self._conn.commit()

    def write_episode(self, rec: EpisodicRecord) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO episodes "
            "(task_id, ts, user_text, intent, tool_calls, final_summary, detail, ok, elapsed_s) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (rec.task_id, rec.ts, rec.user_text, rec.intent,
             json.dumps(rec.tool_calls, default=str),
             rec.final_summary,
             json.dumps(rec.detail, default=str),
             int(rec.ok), rec.elapsed_s),
        )
        self._conn.commit()

    def recent_episodes(self, limit: int = 20) -> list[EpisodicRecord]:
        rows = self._conn.execute(
            "SELECT task_id, ts, user_text, intent, tool_calls, final_summary, "
            "detail, ok, elapsed_s FROM episodes ORDER BY ts DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [self._row_to_episode(r) for r in rows]

    def get_episode(self, task_id: str) -> EpisodicRecord | None:
        row = self._conn.execute(
            "SELECT task_id, ts, user_text, intent, tool_calls, final_summary, "
            "detail, ok, elapsed_s FROM episodes WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        return self._row_to_episode(row) if row else None

    @staticmethod
    def _row_to_episode(row) -> EpisodicRecord:
        return EpisodicRecord(
            task_id=row[0], ts=row[1], user_text=row[2], intent=row[3],
            tool_calls=json.loads(row[4] or "[]"),
            final_summary=row[5] or "",
            detail=json.loads(row[6] or "{}"),
            ok=bool(row[7]), elapsed_s=row[8] or 0.0,
        )

    def write_concept(self, c: SemanticConcept) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO concepts "
            "(concept_id, name, body, confidence, learned_at, last_used, source_task_ids) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (c.concept_id, c.name, c.body, c.confidence, c.learned_at, c.last_used,
             json.dumps(c.source_task_ids)),
        )
        self._conn.commit()

    def search_concepts(self, query: str, limit: int = 5) -> list[SemanticConcept]:
        # LIKE-based; production would put this on FTS5 or pgvector.
        like = f"%{query.lower()}%"
        rows = self._conn.execute(
            "SELECT concept_id, name, body, confidence, learned_at, last_used, source_task_ids "
            "FROM concepts WHERE LOWER(name) LIKE ? OR LOWER(body) LIKE ? "
            "ORDER BY confidence DESC, last_used DESC LIMIT ?",
            (like, like, limit),
        ).fetchall()
        return [
            SemanticConcept(
                concept_id=r[0], name=r[1], body=r[2], confidence=r[3],
                learned_at=r[4], last_used=r[5],
                source_task_ids=json.loads(r[6] or "[]"),
            ) for r in rows
        ]

    def close(self) -> None:
        self._conn.close()


def make_memory_store(path: str | Path | None = None) -> MemoryStore:
    cfg = get_config()
    p = Path(path) if path else cfg.workspace / "memory" / "memory.sqlite"
    p.parent.mkdir(parents=True, exist_ok=True)
    return SQLiteMemoryStore(p)
