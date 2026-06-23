"""Data lineage tracker — records full provenance of every AI response."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from . import DriftReport, ResponseLineage, RoutingDecision, new_lineage_id
from .event_bus import publish

LINEAGE_DB = Path(__file__).resolve().parent.parent.parent / "data" / "state" / "governor_lineage.sqlite3"
LINEAGE_DB.parent.mkdir(parents=True, exist_ok=True)


def _connect() -> sqlite3.Connection:
    con = sqlite3.connect(str(LINEAGE_DB))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=5000")
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS governor_lineage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lineage_uuid TEXT UNIQUE NOT NULL,
            user_id INTEGER NOT NULL,
            session_id TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            intent TEXT,
            provider TEXT,
            model TEXT,
            request_text TEXT,
            draft_output TEXT,
            drift_report_json TEXT,
            arbitrator_used INTEGER DEFAULT 0,
            tools_used_json TEXT,
            final_output TEXT,
            latency_ms INTEGER,
            trust_score REAL,
            action_ledger_hash TEXT,
            feedback_rating INTEGER,
            feedback_notes TEXT
        )
        """
    )
    con.execute("CREATE INDEX IF NOT EXISTS idx_lineage_user ON governor_lineage(user_id, timestamp)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_lineage_session ON governor_lineage(session_id)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_lineage_uuid ON governor_lineage(lineage_uuid)")
    con.commit()
    return con


def _hash_record(data: dict[str, Any]) -> str:
    """Create a deterministic hash for action ledger linkage."""
    canonical = json.dumps(data, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


def record_lineage(lineage: ResponseLineage) -> str:
    """Persist a lineage record and return its ID."""
    with _connect() as con:
        con.execute(
            """
            INSERT INTO governor_lineage (
                lineage_uuid, user_id, session_id, timestamp, intent,
                provider, model, request_text, draft_output, drift_report_json,
                arbitrator_used, tools_used_json, final_output, latency_ms,
                trust_score, action_ledger_hash, feedback_rating, feedback_notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                lineage.lineage_id,
                lineage.user_id,
                lineage.session_id,
                lineage.timestamp.isoformat(),
                lineage.intent.value,
                lineage.routing_decision.provider,
                lineage.routing_decision.model,
                lineage.request_text[:4000],
                lineage.draft_output[:8000],
                json.dumps(lineage.drift_report.to_dict(), ensure_ascii=False) if lineage.drift_report else None,
                int(lineage.arbitrator_used),
                json.dumps(lineage.tools_used, ensure_ascii=False),
                lineage.final_output[:8000],
                lineage.latency_ms,
                lineage.trust_score,
                lineage.action_ledger_hash,
                lineage.feedback_rating,
                lineage.feedback_notes,
            ),
        )
        con.commit()

    # Publish event
    publish("ai.request_completed", {
        "lineage_id": lineage.lineage_id,
        "user_id": lineage.user_id,
        "intent": lineage.intent.value,
        "provider": lineage.routing_decision.provider,
        "model": lineage.routing_decision.model,
        "drift_triggered": lineage.drift_report.triggered if lineage.drift_report else False,
        "trust_score": lineage.trust_score,
        "latency_ms": lineage.latency_ms,
    })

    return lineage.lineage_id


def get_lineage(lineage_id: str) -> Optional[dict[str, Any]]:
    with _connect() as con:
        row = con.execute("SELECT * FROM governor_lineage WHERE lineage_uuid = ?", (lineage_id,)).fetchone()
    if not row:
        return None
    return _row_to_dict(row)


def list_lineage(user_id: int, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
    with _connect() as con:
        rows = con.execute(
            "SELECT * FROM governor_lineage WHERE user_id = ? ORDER BY timestamp DESC LIMIT ? OFFSET ?",
            (user_id, limit, offset),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def add_feedback(lineage_id: str, rating: int, notes: str = "") -> bool:
    """Add user feedback to a lineage record. rating: 1 = thumbs up, -1 = thumbs down."""
    with _connect() as con:
        cur = con.execute(
            "UPDATE governor_lineage SET feedback_rating = ?, feedback_notes = ? WHERE lineage_uuid = ?",
            (rating, notes, lineage_id),
        )
        con.commit()
    if cur.rowcount > 0:
        publish("ai.feedback_received", {"lineage_id": lineage_id, "rating": rating, "notes": notes})
        return True
    return False


def compute_trust_score(lineage_id: str) -> float:
    """Compute a trust score for a lineage record based on drift, latency, and feedback."""
    row = get_lineage(lineage_id)
    if not row:
        return 0.0

    score = 1.0

    # Drift penalty
    drift = row.get("drift_report", {})
    if drift.get("triggered"):
        score -= 0.3
    score -= (drift.get("composite", 0.0) * 0.3)

    # Latency penalty
    latency = row.get("latency_ms", 0)
    if latency > 30000:
        score -= 0.2
    elif latency > 10000:
        score -= 0.1

    # Feedback bonus/penalty
    feedback = row.get("feedback_rating")
    if feedback == 1:
        score += 0.2
    elif feedback == -1:
        score -= 0.4

    # Arbitrator penalty (corrections suggest initial draft was weak)
    if row.get("arbitrator_used"):
        score -= 0.05

    return max(0.0, min(1.0, round(score, 3)))


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "lineage_id": row["lineage_uuid"],
        "user_id": row["user_id"],
        "session_id": row["session_id"],
        "timestamp": row["timestamp"],
        "intent": row["intent"],
        "provider": row["provider"],
        "model": row["model"],
        "request_text": row["request_text"],
        "draft_output": row["draft_output"],
        "drift_report": json.loads(row["drift_report_json"]) if row["drift_report_json"] else None,
        "arbitrator_used": bool(row["arbitrator_used"]),
        "tools_used": json.loads(row["tools_used_json"]) if row["tools_used_json"] else [],
        "final_output": row["final_output"],
        "latency_ms": row["latency_ms"],
        "trust_score": row["trust_score"],
        "action_ledger_hash": row["action_ledger_hash"],
        "feedback_rating": row["feedback_rating"],
        "feedback_notes": row["feedback_notes"],
    }


def get_drift_summary(user_id: int, days: int = 7) -> dict[str, Any]:
    """Aggregate drift statistics for dashboard."""
    with _connect() as con:
        row = con.execute(
            """
            SELECT
                COUNT(*) as total,
                AVG(CASE WHEN drift_report_json IS NOT NULL THEN json_extract(drift_report_json, '$.composite') END) as avg_drift,
                SUM(CASE WHEN arbitrator_used = 1 THEN 1 ELSE 0 END) as arbitrator_count,
                SUM(CASE WHEN feedback_rating = 1 THEN 1 ELSE 0 END) as thumbs_up,
                SUM(CASE WHEN feedback_rating = -1 THEN 1 ELSE 0 END) as thumbs_down
            FROM governor_lineage
            WHERE user_id = ? AND timestamp > datetime('now', ?)
            """,
            (user_id, f"-{days} days"),
        ).fetchone()
    return {
        "total_requests": row["total"] or 0,
        "avg_drift": round(row["avg_drift"] or 0.0, 4),
        "arbitrator_invocations": row["arbitrator_count"] or 0,
        "thumbs_up": row["thumbs_up"] or 0,
        "thumbs_down": row["thumbs_down"] or 0,
    }
