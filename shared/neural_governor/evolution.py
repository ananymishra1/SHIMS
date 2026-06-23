"""Safe self-evolution loop — proposes, tests, benchmarks, and queues patches."""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from .event_bus import publish

EVOLUTION_DB = Path(__file__).resolve().parent.parent.parent / "data" / "state" / "governor_evolution.sqlite3"
EVOLUTION_DB.parent.mkdir(parents=True, exist_ok=True)


def _connect() -> sqlite3.Connection:
    con = sqlite3.connect(str(EVOLUTION_DB))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS governor_proposals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            proposal_uuid TEXT UNIQUE NOT NULL,
            title TEXT NOT NULL,
            description TEXT,
            patch_type TEXT,
            patch_content TEXT,
            affected_files_json TEXT,
            baseline_score REAL,
            sandbox_score REAL,
            improvement_delta REAL,
            test_results_json TEXT,
            status TEXT DEFAULT 'pending',
            proposed_by TEXT DEFAULT 'system',
            reviewed_by INTEGER,
            review_notes TEXT,
            created_at REAL NOT NULL,
            reviewed_at REAL,
            deployed_at REAL
        )
        """
    )
    con.commit()
    return con


def propose_patch(
    title: str,
    description: str,
    patch_type: str,
    patch_content: str,
    affected_files: list[str],
    baseline_score: float = 0.0,
    sandbox_score: float = 0.0,
    test_results: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Create a new evolution proposal."""
    uid = str(uuid.uuid4())
    delta = sandbox_score - baseline_score
    with _connect() as con:
        con.execute(
            """
            INSERT INTO governor_proposals (
                proposal_uuid, title, description, patch_type, patch_content,
                affected_files_json, baseline_score, sandbox_score, improvement_delta,
                test_results_json, status, proposed_by, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', 'system', ?)
            """,
            (
                uid, title, description, patch_type, patch_content,
                json.dumps(affected_files), baseline_score, sandbox_score, delta,
                json.dumps(test_results or {}), time.time(),
            ),
        )
        con.commit()

    publish("evolution.proposal_created", {
        "proposal_uuid": uid,
        "title": title,
        "improvement_delta": delta,
    })

    return {
        "proposal_uuid": uid,
        "title": title,
        "status": "pending",
        "improvement_delta": delta,
    }


def list_proposals(status: Optional[str] = None, limit: int = 50) -> list[dict[str, Any]]:
    with _connect() as con:
        if status:
            rows = con.execute(
                "SELECT * FROM governor_proposals WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT * FROM governor_proposals ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_proposal(proposal_uuid: str) -> Optional[dict[str, Any]]:
    with _connect() as con:
        row = con.execute("SELECT * FROM governor_proposals WHERE proposal_uuid = ?", (proposal_uuid,)).fetchone()
    return _row_to_dict(row) if row else None


def review_proposal(proposal_uuid: str, reviewer_id: int, approved: bool, notes: str = "") -> dict[str, Any]:
    status = "approved" if approved else "rejected"
    with _connect() as con:
        con.execute(
            "UPDATE governor_proposals SET status = ?, reviewed_by = ?, review_notes = ?, reviewed_at = ? WHERE proposal_uuid = ?",
            (status, reviewer_id, notes, time.time(), proposal_uuid),
        )
        con.commit()

    publish(f"evolution.proposal_{status}", {
        "proposal_uuid": proposal_uuid,
        "reviewer_id": reviewer_id,
        "notes": notes,
    })

    return {"proposal_uuid": proposal_uuid, "status": status, "reviewer_id": reviewer_id}


def deploy_proposal(proposal_uuid: str) -> dict[str, Any]:
    """Mark proposal as deployed. Actual file writing is handled by the caller."""
    with _connect() as con:
        con.execute(
            "UPDATE governor_proposals SET status = 'deployed', deployed_at = ? WHERE proposal_uuid = ?",
            (time.time(), proposal_uuid),
        )
        con.commit()

    publish("evolution.proposal_deployed", {"proposal_uuid": proposal_uuid})
    return {"proposal_uuid": proposal_uuid, "status": "deployed"}


def rollback_proposal(proposal_uuid: str) -> dict[str, Any]:
    with _connect() as con:
        con.execute(
            "UPDATE governor_proposals SET status = 'rolled_back', deployed_at = NULL WHERE proposal_uuid = ?",
            (proposal_uuid,),
        )
        con.commit()

    publish("evolution.proposal_rolled_back", {"proposal_uuid": proposal_uuid})
    return {"proposal_uuid": proposal_uuid, "status": "rolled_back"}


def run_sandbox_test(patch_content: str, affected_files: list[str]) -> dict[str, Any]:
    """Run a patch through the existing self-evolver sandbox."""
    try:
        from shared.self_evolver import validate_proposal
        # Build a fake proposal structure
        proposal = {
            "patch": patch_content,
            "files": affected_files,
            "description": "Governor auto-proposal",
        }
        result = validate_proposal(proposal)
        return {
            "ok": result.get("ok", False),
            "tests_passed": result.get("tests_passed", False),
            "details": result.get("details", ""),
        }
    except Exception as exc:
        return {"ok": False, "tests_passed": False, "details": str(exc)}


def detect_patterns_for_evolution(user_id: int, days: int = 7) -> list[dict[str, Any]]:
    """Analyze lineage to detect recurring patterns worth automating."""
    try:
        from .lineage import _connect as lineage_connect
        with lineage_connect() as con:
            rows = con.execute(
                """
                SELECT intent, COUNT(*) as c, AVG(drift_report_json IS NOT NULL AND json_extract(drift_report_json, '$.composite') > 0.3) as avg_drift
                FROM governor_lineage
                WHERE user_id = ? AND timestamp > datetime('now', ?)
                GROUP BY intent
                HAVING c >= 3
                ORDER BY c DESC
                """,
                (user_id, f"-{days} days"),
            ).fetchall()
    except Exception:
        return []

    patterns = []
    for r in rows:
        if r["c"] >= 3:
            patterns.append({
                "intent": r["intent"],
                "frequency": r["c"],
                "avg_drift": round(r["avg_drift"] or 0.0, 4),
                "suggestion": f"Consider creating a skill/template for '{r['intent']}' tasks.",
            })
    return patterns


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "proposal_uuid": row["proposal_uuid"],
        "title": row["title"],
        "description": row["description"],
        "patch_type": row["patch_type"],
        "patch_content": row["patch_content"],
        "affected_files": json.loads(row["affected_files_json"]) if row["affected_files_json"] else [],
        "baseline_score": row["baseline_score"],
        "sandbox_score": row["sandbox_score"],
        "improvement_delta": row["improvement_delta"],
        "test_results": json.loads(row["test_results_json"]) if row["test_results_json"] else {},
        "status": row["status"],
        "proposed_by": row["proposed_by"],
        "reviewed_by": row["reviewed_by"],
        "review_notes": row["review_notes"],
        "created_at": row["created_at"],
        "reviewed_at": row["reviewed_at"],
        "deployed_at": row["deployed_at"],
    }
