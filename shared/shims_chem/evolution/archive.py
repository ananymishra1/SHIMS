"""
Darwin-Gödel-Machine-style archive.

A SQLite table of every candidate improvement we've ever evaluated. Each
candidate records:

  - kind ('prompt_edit' | 'tool_default' | 'lora_adapter' | 'rule_add')
  - parent_id (lineage; None for genesis)
  - payload (the actual edit; small JSON or a path to a LoRA dir)
  - eval scores
  - status ('proposed' | 'eval_passed' | 'eval_failed' | 'promoted' | 'rolled_back')
  - promoted_at / rolled_back_at timestamps

This is the only durable record of system evolution. To roll back: promote
an older candidate. To compare: query by lineage and score axis.
"""
from __future__ import annotations
import json
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import get_config


@dataclass
class Candidate:
    candidate_id: str
    kind: str
    parent_id: str | None
    payload: dict[str, Any]
    scores: dict[str, float] = field(default_factory=dict)
    status: str = "proposed"
    created_at: float = field(default_factory=time.time)
    promoted_at: float | None = None
    rolled_back_at: float | None = None
    note: str = ""

    @classmethod
    def new(cls, kind: str, payload: dict[str, Any], *, parent_id: str | None = None,
            note: str = "") -> "Candidate":
        return cls(
            candidate_id=f"cand-{uuid.uuid4().hex[:10]}",
            kind=kind, parent_id=parent_id, payload=payload, note=note,
        )


class Archive:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS candidates ("
            "candidate_id TEXT PRIMARY KEY, kind TEXT, parent_id TEXT, "
            "payload TEXT, scores TEXT, status TEXT, created_at REAL, "
            "promoted_at REAL, rolled_back_at REAL, note TEXT)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_cand_status ON candidates(status, created_at DESC)"
        )
        self._conn.commit()

    def write(self, c: Candidate) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO candidates "
            "(candidate_id, kind, parent_id, payload, scores, status, "
            "created_at, promoted_at, rolled_back_at, note) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (c.candidate_id, c.kind, c.parent_id,
             json.dumps(c.payload, default=str),
             json.dumps(c.scores),
             c.status, c.created_at, c.promoted_at, c.rolled_back_at, c.note),
        )
        self._conn.commit()

    def get(self, candidate_id: str) -> Candidate | None:
        row = self._conn.execute(
            "SELECT candidate_id, kind, parent_id, payload, scores, status, "
            "created_at, promoted_at, rolled_back_at, note "
            "FROM candidates WHERE candidate_id = ?",
            (candidate_id,),
        ).fetchone()
        return self._row(row) if row else None

    def list_recent(self, limit: int = 50, status: str | None = None) -> list[Candidate]:
        if status:
            rows = self._conn.execute(
                "SELECT candidate_id, kind, parent_id, payload, scores, status, "
                "created_at, promoted_at, rolled_back_at, note "
                "FROM candidates WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT candidate_id, kind, parent_id, payload, scores, status, "
                "created_at, promoted_at, rolled_back_at, note "
                "FROM candidates ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._row(r) for r in rows]

    def current_promoted(self, kind: str) -> Candidate | None:
        row = self._conn.execute(
            "SELECT candidate_id, kind, parent_id, payload, scores, status, "
            "created_at, promoted_at, rolled_back_at, note "
            "FROM candidates WHERE kind = ? AND status = 'promoted' "
            "ORDER BY promoted_at DESC LIMIT 1",
            (kind,),
        ).fetchone()
        return self._row(row) if row else None

    @staticmethod
    def _row(r) -> Candidate:
        return Candidate(
            candidate_id=r[0], kind=r[1], parent_id=r[2],
            payload=json.loads(r[3] or "{}"),
            scores=json.loads(r[4] or "{}"),
            status=r[5], created_at=r[6],
            promoted_at=r[7], rolled_back_at=r[8],
            note=r[9] or "",
        )

    def close(self) -> None:
        self._conn.close()


def make_archive(path: str | Path | None = None) -> Archive:
    cfg = get_config()
    p = Path(path) if path else cfg.workspace / "evolution" / "archive.sqlite"
    p.parent.mkdir(parents=True, exist_ok=True)
    return Archive(p)
