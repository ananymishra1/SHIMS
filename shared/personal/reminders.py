"""Personal reminder system for SHIMS AI.

Stores reminders in SQLite and provides CRUD + upcoming query.
Can be wired to Android AlarmManager via the native bridge.
"""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from shared.config import settings

DB_PATH = Path(settings.database_path).parent / "shims_reminders.sqlite3"


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(DB_PATH))
    con.row_factory = sqlite3.Row
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            body TEXT DEFAULT '',
            remind_at REAL NOT NULL,
            repeat_rule TEXT DEFAULT '',
            created_at REAL NOT NULL,
            dismissed INTEGER DEFAULT 0
        )
        """
    )
    con.execute("CREATE INDEX IF NOT EXISTS idx_rem_time ON reminders(remind_at)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_rem_dismissed ON reminders(dismissed)")
    return con


@dataclass
class Reminder:
    id: int
    title: str
    body: str
    remind_at: float
    repeat_rule: str
    created_at: float
    dismissed: bool

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "body": self.body,
            "remind_at": self.remind_at,
            "repeat_rule": self.repeat_rule,
            "created_at": self.created_at,
            "dismissed": self.dismissed,
        }


def create_reminder(
    title: str,
    remind_at: float,
    body: str = "",
    repeat_rule: str = "",
) -> int:
    with _connect() as con:
        cur = con.execute(
            "INSERT INTO reminders(title, body, remind_at, repeat_rule, created_at) VALUES (?, ?, ?, ?, ?)",
            (title, body, remind_at, repeat_rule, time.time()),
        )
        con.commit()
        return cur.lastrowid or 0


def list_reminders(
    upcoming_only: bool = True,
    limit: int = 100,
) -> list[Reminder]:
    sql = "SELECT * FROM reminders WHERE dismissed = 0"
    args: list[Any] = []
    if upcoming_only:
        sql += " AND remind_at > ?"
        args.append(time.time() - 3600)  # include recent ones
    sql += " ORDER BY remind_at ASC LIMIT ?"
    args.append(limit)
    with _connect() as con:
        rows = con.execute(sql, args).fetchall()
    return [
        Reminder(
            id=r["id"],
            title=r["title"],
            body=r["body"],
            remind_at=r["remind_at"],
            repeat_rule=r["repeat_rule"],
            created_at=r["created_at"],
            dismissed=bool(r["dismissed"]),
        )
        for r in rows
    ]


def dismiss_reminder(reminder_id: int) -> bool:
    with _connect() as con:
        cur = con.execute(
            "UPDATE reminders SET dismissed = 1 WHERE id = ?", (reminder_id,)
        )
        con.commit()
        return cur.rowcount > 0


def delete_reminder(reminder_id: int) -> bool:
    with _connect() as con:
        cur = con.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))
        con.commit()
        return cur.rowcount > 0


def get_upcoming_notifications(look_ahead_seconds: float = 300) -> list[Reminder]:
    """Get reminders that are due within the next N seconds."""
    now = time.time()
    with _connect() as con:
        rows = con.execute(
            "SELECT * FROM reminders WHERE dismissed = 0 AND remind_at BETWEEN ? AND ? ORDER BY remind_at",
            (now, now + look_ahead_seconds),
        ).fetchall()
    return [
        Reminder(
            id=r["id"],
            title=r["title"],
            body=r["body"],
            remind_at=r["remind_at"],
            repeat_rule=r["repeat_rule"],
            created_at=r["created_at"],
            dismissed=bool(r["dismissed"]),
        )
        for r in rows
    ]
