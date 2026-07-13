"""Event bus — async pub/sub for cross-system communication."""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable, Optional

EVENT_DB = Path(__file__).resolve().parent.parent.parent / "data" / "state" / "governor_events.sqlite3"
EVENT_DB.parent.mkdir(parents=True, exist_ok=True)

_subscribers: dict[str, list[Callable[[dict[str, Any]], None]]] = {}


def _connect() -> sqlite3.Connection:
    con = sqlite3.connect(str(EVENT_DB))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS event_bus_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            topic TEXT NOT NULL,
            payload_json TEXT,
            timestamp REAL NOT NULL
        )
        """
    )
    con.execute("CREATE INDEX IF NOT EXISTS idx_event_topic ON event_bus_log(topic, timestamp)")
    con.commit()
    return con


def publish(topic: str, payload: dict[str, Any]) -> None:
    """Publish an event to the bus."""
    ts = time.time()
    with _connect() as con:
        con.execute(
            "INSERT INTO event_bus_log (topic, payload_json, timestamp) VALUES (?, ?, ?)",
            (topic, json.dumps(payload, ensure_ascii=False, default=str), ts),
        )
        con.commit()

    # Notify in-process subscribers
    for cb in _subscribers.get(topic, []):
        try:
            cb(payload)
        except Exception:
            pass
    # Notify wildcard subscribers
    for cb in _subscribers.get("*", []):
        try:
            cb({"topic": topic, **payload})
        except Exception:
            pass


def subscribe(topic: str, callback: Callable[[dict[str, Any]], None]) -> None:
    """Subscribe to a topic. Use '*' for all topics."""
    _subscribers.setdefault(topic, []).append(callback)


def unsubscribe(topic: str, callback: Callable[[dict[str, Any]], None]) -> None:
    """Remove a subscriber."""
    if topic in _subscribers:
        _subscribers[topic] = [cb for cb in _subscribers[topic] if cb != callback]


def recent_events(topic: Optional[str] = None, limit: int = 50) -> list[dict[str, Any]]:
    with _connect() as con:
        if topic:
            rows = con.execute(
                "SELECT * FROM event_bus_log WHERE topic = ? ORDER BY timestamp DESC LIMIT ?",
                (topic, limit),
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT * FROM event_bus_log ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            ).fetchall()
    return [
        {
            "id": r["id"],
            "topic": r["topic"],
            "payload": json.loads(r["payload_json"]) if r["payload_json"] else {},
            "timestamp": r["timestamp"],
        }
        for r in rows
    ]


def get_event_stream(last_id: int = 0):
    """Generator for SSE event streaming."""
    while True:
        with _connect() as con:
            rows = con.execute(
                "SELECT * FROM event_bus_log WHERE id > ? ORDER BY id ASC",
                (last_id,),
            ).fetchall()
        for r in rows:
            last_id = r["id"]
            payload = json.loads(r["payload_json"]) if r["payload_json"] else {}
            yield {
                "id": last_id,
                "topic": r["topic"],
                "payload": payload,
                "timestamp": r["timestamp"],
            }
        time.sleep(0.5)
