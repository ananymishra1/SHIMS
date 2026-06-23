from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .autonomy import check_autonomy
from .config import ROOT_DIR


ACTION_DB = Path(os.getenv("SHIMS_ACTION_LEDGER_DB", ROOT_DIR / "data" / "state" / "shims_action_ledger.sqlite3")).resolve()

EXTERNAL_OR_IRREVERSIBLE = {
    "gmail_send",
    "email_send",
    "calendar_sync",
    "social_post",
    "publish_campaign",
    "payment_release",
    "account_change",
    "vendor_order",
    "delete_external",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean(value: Any, limit: int = 4000) -> str:
    return " ".join(str(value or "").replace("\x00", " ").split())[:limit]


def _json(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, sort_keys=True, default=str)


def _load_json(value: str | None) -> Any:
    if not value:
        return {}
    try:
        return json.loads(value)
    except Exception:
        return {}


def _connect() -> sqlite3.Connection:
    ACTION_DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(ACTION_DB))
    con.row_factory = sqlite3.Row
    ensure_action_schema(con)
    return con


def ensure_action_schema(con: sqlite3.Connection | None = None) -> None:
    own = con is None
    if con is None:
        ACTION_DB.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(str(ACTION_DB))
        con.row_factory = sqlite3.Row
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS action_records (
            id TEXT PRIMARY KEY,
            action_type TEXT NOT NULL,
            title TEXT NOT NULL,
            status TEXT NOT NULL,
            requested_level TEXT DEFAULT 'L2',
            effective_level TEXT DEFAULT 'L2',
            requires_confirmation INTEGER DEFAULT 0,
            autonomy_json TEXT DEFAULT '{}',
            payload_json TEXT DEFAULT '{}',
            result_json TEXT DEFAULT '{}',
            evidence_json TEXT DEFAULT '[]',
            summary TEXT DEFAULT '',
            record_hash TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_action_records_created ON action_records(created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_action_records_status ON action_records(status);
        CREATE INDEX IF NOT EXISTS idx_action_records_type ON action_records(action_type);
        """
    )
    if own:
        con.commit()
        con.close()


def _hash_record(record: dict[str, Any]) -> str:
    stable = {
        "id": record.get("id"),
        "action_type": record.get("action_type"),
        "title": record.get("title"),
        "status": record.get("status"),
        "requested_level": record.get("requested_level"),
        "effective_level": record.get("effective_level"),
        "requires_confirmation": bool(record.get("requires_confirmation")),
        "autonomy": record.get("autonomy") or {},
        "payload": record.get("payload") or {},
        "result": record.get("result") or {},
        "evidence": record.get("evidence") or [],
        "summary": record.get("summary") or "",
        "created_at": record.get("created_at"),
    }
    blob = json.dumps(stable, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _row_to_action(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    data = dict(row)
    data["requires_confirmation"] = bool(data.get("requires_confirmation"))
    data["autonomy"] = _load_json(data.pop("autonomy_json", None))
    data["payload"] = _load_json(data.pop("payload_json", None))
    data["result"] = _load_json(data.pop("result_json", None))
    data["evidence"] = _load_json(data.pop("evidence_json", None)) or []
    data["ledger_hash"] = data.get("record_hash")
    data["verified"] = _hash_record(data) == data.get("record_hash")
    return data


def action_requires_confirmation(action_type: str) -> bool:
    normalized = _clean(action_type, 120).lower().replace(" ", "_")
    if normalized in EXTERNAL_OR_IRREVERSIBLE:
        return True
    risky_words = ("send", "publish", "post", "pay", "payment", "approve", "release", "delete", "submit", "sync")
    return any(word in normalized for word in risky_words) and normalized not in {
        "web_search",
        "operator_digest",
        "campaign_draft",
        "calendar_ics_create",
    }


def record_action(
    action_type: str,
    title: str,
    *,
    payload: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
    evidence: list[dict[str, Any]] | None = None,
    status: str | None = None,
    requested_level: str = "L3",
    summary: str = "",
) -> dict[str, Any]:
    action_type = _clean(action_type or "unknown_action", 120).lower().replace(" ", "_") or "unknown_action"
    title = _clean(title or action_type.replace("_", " ").title(), 240)
    requires_confirmation = action_requires_confirmation(action_type)
    autonomy = check_autonomy(action_type, requested_level)
    if requires_confirmation:
        autonomy = {**autonomy, "allowed": False, "requires_human_approval": True, "reason": "External, irreversible, or account-changing action requires explicit confirmation."}
    final_status = status or ("requires_confirmation" if requires_confirmation or not autonomy.get("allowed", True) else "completed")
    now = _now()
    record = {
        "id": "act_" + uuid.uuid4().hex[:22],
        "action_type": action_type,
        "title": title,
        "status": _clean(final_status, 80),
        "requested_level": requested_level,
        "effective_level": autonomy.get("effective_level") or requested_level,
        "requires_confirmation": bool(requires_confirmation or autonomy.get("requires_human_approval")),
        "autonomy": autonomy,
        "payload": payload or {},
        "result": result or {},
        "evidence": evidence or [],
        "summary": _clean(summary or title, 800),
        "created_at": now,
        "updated_at": now,
    }
    record["record_hash"] = _hash_record(record)
    with _connect() as con:
        con.execute(
            """
            INSERT INTO action_records(
                id, action_type, title, status, requested_level, effective_level,
                requires_confirmation, autonomy_json, payload_json, result_json,
                evidence_json, summary, record_hash, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["id"],
                record["action_type"],
                record["title"],
                record["status"],
                record["requested_level"],
                record["effective_level"],
                1 if record["requires_confirmation"] else 0,
                _json(record["autonomy"]),
                _json(record["payload"]),
                _json(record["result"]),
                _json(record["evidence"]),
                record["summary"],
                record["record_hash"],
                record["created_at"],
                record["updated_at"],
            ),
        )
        con.commit()
    action = get_action(record["id"]) or record
    return {"ok": True, "action": action, "action_id": record["id"], "ledger_hash": record["record_hash"], "requires_confirmation": action["requires_confirmation"]}


def get_action(action_id: str) -> dict[str, Any] | None:
    with _connect() as con:
        row = con.execute("SELECT * FROM action_records WHERE id=?", (_clean(action_id, 120),)).fetchone()
    return _row_to_action(row)


def list_actions(limit: int = 50, status: str | None = None, action_type: str | None = None) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit or 50), 200))
    with _connect() as con:
        if status and action_type:
            rows = con.execute(
                "SELECT * FROM action_records WHERE status=? AND action_type=? ORDER BY created_at DESC LIMIT ?",
                (_clean(status, 80), _clean(action_type, 120), limit),
            ).fetchall()
        elif status:
            rows = con.execute("SELECT * FROM action_records WHERE status=? ORDER BY created_at DESC LIMIT ?", (_clean(status, 80), limit)).fetchall()
        elif action_type:
            rows = con.execute("SELECT * FROM action_records WHERE action_type=? ORDER BY created_at DESC LIMIT ?", (_clean(action_type, 120), limit)).fetchall()
        else:
            rows = con.execute("SELECT * FROM action_records ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    return [a for a in (_row_to_action(r) for r in rows) if a]


def verify_action(action_id: str) -> dict[str, Any]:
    action = get_action(action_id)
    if not action:
        return {"ok": False, "reason": "not_found", "action_id": action_id}
    current = _hash_record(action)
    expected = action.get("record_hash")
    return {
        "ok": current == expected,
        "action_id": action.get("id"),
        "ledger_hash": expected,
        "current_hash": current,
        "status": action.get("status"),
        "action_type": action.get("action_type"),
        "requires_confirmation": action.get("requires_confirmation"),
    }


def action_status() -> dict[str, Any]:
    ensure_action_schema()
    with _connect() as con:
        counts = {
            "total": con.execute("SELECT COUNT(*) FROM action_records").fetchone()[0],
            "completed": con.execute("SELECT COUNT(*) FROM action_records WHERE status='completed'").fetchone()[0],
            "requires_confirmation": con.execute("SELECT COUNT(*) FROM action_records WHERE status='requires_confirmation'").fetchone()[0],
            "failed": con.execute("SELECT COUNT(*) FROM action_records WHERE status='failed'").fetchone()[0],
        }
    return {"ok": True, "version": "action-ledger-v16", "db_path": str(ACTION_DB), "counts": counts}
