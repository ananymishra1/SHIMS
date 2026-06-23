"""Hospital reminders & follow-ups."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from shared.desktop_scheduler import schedule_task

from ..database import execute, insert, query_all, query_one


def create_reminder(patient_id: int, reminder_type: str, scheduled_at: str, message: str, visit_id: int | None = None) -> dict[str, Any]:
    rid = insert(
        "INSERT INTO reminders (patient_id, visit_id, reminder_type, scheduled_at, message) VALUES (?, ?, ?, ?, ?)",
        (patient_id, visit_id, reminder_type, scheduled_at, message),
    )
    # Also schedule via SHIMS desktop scheduler for actual execution if possible
    try:
        schedule_task(
            title=f"Hospital reminder: {reminder_type} for patient {patient_id}",
            schedule_type="once",
            when=scheduled_at,
            action_type="message",
            payload={"text": message, "channel": "hospital", "patient_id": patient_id, "visit_id": visit_id},
        )
    except Exception:
        pass
    return query_one("SELECT * FROM reminders WHERE id=?", (rid,))


def list_reminders(status: str | None = None, patient_id: int | None = None, limit: int = 100) -> list[dict[str, Any]]:
    where = ["1=1"]
    params: list[Any] = []
    if status:
        where.append("status = ?")
        params.append(status)
    if patient_id:
        where.append("patient_id = ?")
        params.append(patient_id)
    sql = f"SELECT * FROM reminders WHERE {' AND '.join(where)} ORDER BY scheduled_at ASC LIMIT ?"
    params.append(limit)
    return query_all(sql, tuple(params))


def mark_reminder(reminder_id: int, status: str) -> None:
    execute("UPDATE reminders SET status=? WHERE id=?", (status, reminder_id))


def upcoming_for_role(role: str, minutes_ahead: int = 120) -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc)
    later = (now + timedelta(minutes=minutes_ahead)).isoformat()
    now_s = now.isoformat()
    # role-specific reminder types
    type_map = {
        "receptionist": ("appointment", "follow_up_call"),
        "nurse": ("medication", "ot_prep"),
        "doctor": ("follow_up_call",),
        "lab_technician": ("lab",),
        "ot_coordinator": ("ot_prep",),
    }
    types = type_map.get(role, ())
    if not types:
        return []
    placeholders = ",".join("?" * len(types))
    return query_all(
        f"SELECT * FROM reminders WHERE reminder_type IN ({placeholders}) AND scheduled_at BETWEEN ? AND ? AND status='pending' ORDER BY scheduled_at",
        (*types, now_s, later),
    )
