"""OT scheduling."""
from __future__ import annotations

from typing import Any

from ..database import execute, insert, query_all, query_one


OT_ROOMS = ["OT-1", "OT-2", "OT-3"]


def create_schedule(visit_id: int, data: dict[str, Any]) -> dict[str, Any]:
    oid = insert(
        "INSERT INTO ot_schedules (visit_id, procedure, scheduled_at, ot_room, surgeon_id, anaesthetist_id, notes, consent_signed) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (visit_id, data["procedure"], data["scheduled_at"], data.get("ot_room"), data.get("surgeon_id"), data.get("anaesthetist_id"), data.get("notes"), int(bool(data.get("consent_signed")))),
    )
    return query_one("SELECT * FROM ot_schedules WHERE id=?", (oid,))


def list_schedules(status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    if status:
        return query_all("SELECT * FROM ot_schedules WHERE status=? ORDER BY scheduled_at LIMIT ?", (status, limit))
    return query_all("SELECT * FROM ot_schedules ORDER BY scheduled_at LIMIT ?", (limit,))


def update_schedule(ot_id: int, data: dict[str, Any]) -> dict[str, Any] | None:
    allowed = {"procedure", "scheduled_at", "ot_room", "surgeon_id", "anaesthetist_id", "notes", "consent_signed", "status"}
    fields = []
    params = []
    for k, v in data.items():
        if k in allowed:
            fields.append(f"{k} = ?")
            params.append(v)
    if not fields:
        return query_one("SELECT * FROM ot_schedules WHERE id=?", (ot_id,))
    params.append(ot_id)
    execute(f"UPDATE ot_schedules SET {', '.join(fields)} WHERE id=?", tuple(params))
    return query_one("SELECT * FROM ot_schedules WHERE id=?", (ot_id,))
