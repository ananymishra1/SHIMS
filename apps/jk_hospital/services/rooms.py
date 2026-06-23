"""Room / bed allocation."""
from __future__ import annotations

from typing import Any

from ..database import execute, insert, query_all, query_one


def ensure_default_rooms() -> None:
    wards = [
        ("General Ward", "GW", ["101", "102", "103"], "general"),
        ("Semi Private", "SP", ["201", "202"], "semi_private"),
        ("Private", "PV", ["301", "302", "303"], "private"),
        ("ICU", "ICU", ["A", "B", "C"], "icu"),
    ]
    existing = {(r["ward"], r["room_number"], r["bed_number"]) for r in query_all("SELECT ward, room_number, bed_number FROM rooms")}
    for ward, prefix, rooms, bed_type in wards:
        for room in rooms:
            for bed in ["A", "B"]:
                key = (ward, room, bed)
                if key in existing:
                    continue
                insert("INSERT INTO rooms (ward, room_number, bed_number, bed_type, status) VALUES (?, ?, ?, ?, ?)",
                       (ward, room, bed, bed_type, "available"))


def list_rooms(status: str | None = None) -> list[dict[str, Any]]:
    if status:
        return query_all("SELECT * FROM rooms WHERE status=? ORDER BY ward, room_number, bed_number", (status,))
    return query_all("SELECT * FROM rooms ORDER BY ward, room_number, bed_number")


def allocate_bed(visit_id: int, bed_id: int) -> dict[str, Any]:
    bed = query_one("SELECT * FROM rooms WHERE id=? AND status='available'", (bed_id,))
    if not bed:
        raise ValueError("Bed not available")
    execute("UPDATE rooms SET status='occupied' WHERE id=?", (bed_id,))
    aid = insert("INSERT INTO bed_allocations (visit_id, bed_id) VALUES (?, ?)", (visit_id, bed_id))
    execute("UPDATE visits SET bed_id=? WHERE id=?", (bed_id, visit_id))
    return query_one("SELECT * FROM bed_allocations WHERE id=?", (aid,))


def discharge_bed(visit_id: int) -> None:
    alloc = query_one("SELECT * FROM bed_allocations WHERE visit_id=? AND discharged_at IS NULL", (visit_id,))
    if alloc:
        execute("UPDATE rooms SET status='available' WHERE id=?", (alloc["bed_id"],))
        execute("UPDATE bed_allocations SET discharged_at=CURRENT_TIMESTAMP WHERE id=?", (alloc["id"],))
        execute("UPDATE visits SET bed_id=NULL WHERE id=?", (visit_id,))
