"""Inbound message channels (WhatsApp today, other bridges later).

SHIMS has no WhatsApp account of its own. Messages arrive from a bridge that
already holds a session — the OpenClaw WhatsApp plugin subscribes to its
``message_received`` hook and POSTs each message here. This module is only the
durable landing zone for that relay plus the read side the dashboard renders.

Design notes:

* **Append-only, bounded.** ``record_inbound`` never updates history; a retained
  window is trimmed on write so an always-on relay cannot grow the DB forever.
* **Idempotent.** The bridge retries on failure, and WhatsApp itself can deliver
  a message more than once. ``(channel, message_id)`` is unique, so a replay is
  a no-op rather than a duplicate in the feed.
* **Inbound only.** Nothing here sends. Outbound WhatsApp would mean acting as
  the user to third parties, which needs an explicit approval path rather than
  an HTTP endpoint any local process can call.
"""
from __future__ import annotations

import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator

from shared.config import ROOT_DIR

CHANNELS_DB = ROOT_DIR / "data" / "state" / "shims_channels.sqlite3"

# Retained messages per channel. A chatty group can produce thousands a day and
# the dashboard only ever shows the newest handful.
MAX_RETAINED = 2000

# Longest message body kept. WhatsApp allows very long texts; the feed needs a
# preview, not an archive.
MAX_BODY = 4000


def _now() -> float:
    return time.time()


@contextmanager
def _connect() -> Generator[sqlite3.Connection, None, None]:
    path = Path(CHANNELS_DB)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path), timeout=10)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()


def ensure_schema(con: sqlite3.Connection | None = None) -> None:
    def _create(c: sqlite3.Connection) -> None:
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS channel_messages (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                channel      TEXT NOT NULL,
                message_id   TEXT NOT NULL,
                thread_id    TEXT NOT NULL DEFAULT '',
                sender_id    TEXT NOT NULL DEFAULT '',
                sender_name  TEXT NOT NULL DEFAULT '',
                body         TEXT NOT NULL DEFAULT '',
                is_group     INTEGER NOT NULL DEFAULT 0,
                received_at  REAL NOT NULL,
                metadata     TEXT NOT NULL DEFAULT '{}'
            )
            """
        )
        # Idempotency key: a retried relay or a WhatsApp redelivery must not
        # produce a second row.
        c.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_channel_msg_unique "
            "ON channel_messages(channel, message_id)"
        )
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_channel_msg_recent "
            "ON channel_messages(channel, received_at DESC)"
        )

    if con is not None:
        _create(con)
        return
    with _connect() as c:
        _create(c)


def _trim(con: sqlite3.Connection, channel: str) -> None:
    con.execute(
        """
        DELETE FROM channel_messages
         WHERE channel = ?
           AND id NOT IN (
               SELECT id FROM channel_messages
                WHERE channel = ?
                ORDER BY received_at DESC, id DESC
                LIMIT ?
           )
        """,
        (channel, channel, MAX_RETAINED),
    )


def record_inbound(
    channel: str,
    message_id: str,
    *,
    body: str = "",
    sender_id: str = "",
    sender_name: str = "",
    thread_id: str = "",
    is_group: bool = False,
    received_at: float | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Store one inbound message. Returns ``{ok, stored, duplicate}``.

    A duplicate is a success, not an error — the bridge retries, and reporting
    failure would make it retry harder.
    """
    import json as _json

    channel = (channel or "").strip().lower()
    message_id = (message_id or "").strip()
    if not channel:
        return {"ok": False, "error": "channel is required"}
    if not message_id:
        return {"ok": False, "error": "message_id is required"}

    ensure_schema()
    with _connect() as con:
        try:
            con.execute(
                """
                INSERT INTO channel_messages
                    (channel, message_id, thread_id, sender_id, sender_name,
                     body, is_group, received_at, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    channel,
                    message_id,
                    str(thread_id or "")[:200],
                    str(sender_id or "")[:200],
                    str(sender_name or "")[:200],
                    str(body or "")[:MAX_BODY],
                    1 if is_group else 0,
                    float(received_at or _now()),
                    _json.dumps(metadata or {}, default=str)[:4000],
                ),
            )
        except sqlite3.IntegrityError:
            return {"ok": True, "stored": False, "duplicate": True}
        _trim(con, channel)
    return {"ok": True, "stored": True, "duplicate": False}


def recent(channel: str, limit: int = 20) -> dict[str, Any]:
    """Newest messages for a channel, plus whether the bridge has ever
    delivered anything (which is the only signal SHIMS has that it is wired up
    — the session itself lives in the bridge, not here)."""
    import json as _json

    channel = (channel or "").strip().lower()
    limit = max(1, min(int(limit or 20), 100))
    ensure_schema()
    with _connect() as con:
        rows = con.execute(
            "SELECT * FROM channel_messages WHERE channel = ? "
            "ORDER BY received_at DESC, id DESC LIMIT ?",
            (channel, limit),
        ).fetchall()
        total = con.execute(
            "SELECT COUNT(*) AS n FROM channel_messages WHERE channel = ?", (channel,)
        ).fetchone()["n"]
        last = con.execute(
            "SELECT MAX(received_at) AS t FROM channel_messages WHERE channel = ?",
            (channel,),
        ).fetchone()["t"]

    messages = []
    for row in rows:
        try:
            meta = _json.loads(row["metadata"] or "{}")
        except Exception:
            meta = {}
        messages.append({
            "message_id": row["message_id"],
            "thread_id": row["thread_id"],
            "sender_id": row["sender_id"],
            "sender_name": row["sender_name"] or row["sender_id"],
            "text": row["body"],
            "is_group": bool(row["is_group"]),
            "received_at": row["received_at"],
            "metadata": meta,
        })
    return {
        "ok": True,
        "channel": channel,
        "connected": bool(total),
        "count": int(total),
        "last_received_at": last,
        "messages": messages,
    }


def bridge_token() -> str:
    """Shared secret the relay must present.

    Reuses ``SHIMS_BRIDGE_TOKEN`` so there is one bridge credential on the
    machine rather than a second one to rotate.
    """
    return (os.getenv("SHIMS_BRIDGE_TOKEN") or "").strip()


def token_ok(supplied: str) -> bool:
    """Constant-time check of the relay's token.

    Returns False when no token is configured: this endpoint accepts message
    content from another process, so an unset secret must fail closed rather
    than silently accept anything that can reach the port.
    """
    import hmac

    expected = bridge_token()
    if not expected or len(expected) < 16:
        return False
    return hmac.compare_digest(str(supplied or ""), expected)
