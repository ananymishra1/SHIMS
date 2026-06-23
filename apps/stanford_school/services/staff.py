from ..database import get_db
import time


def create_staff(data: dict) -> dict:
    with get_db() as conn:
        cursor = conn.execute(
            "INSERT INTO staff (employee_id, full_name, role, subject, created_at) VALUES (?, ?, ?, ?, ?)",
            (
                data["employee_id"],
                data["full_name"],
                data["role"],
                data.get("subject"),
                time.time(),
            ),
        )
        row_id = cursor.lastrowid
        row = conn.execute("SELECT * FROM staff WHERE id=?", (row_id,)).fetchone()
        return dict(row)


def list_staff() -> list:
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM staff ORDER BY id DESC").fetchall()
        return [dict(r) for r in rows]


def get_staff(staff_id: int) -> dict | None:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM staff WHERE id=?", (staff_id,)).fetchone()
        return dict(row) if row else None


def update_staff(staff_id: int, data: dict) -> dict | None:
    fields = []
    values = []
    for key in ("employee_id", "full_name", "role", "subject"):
        if key in data:
            fields.append(f"{key}=?")
            values.append(data[key])
    if not fields:
        return get_staff(staff_id)
    values.append(staff_id)
    with get_db() as conn:
        conn.execute(
            f"UPDATE staff SET {', '.join(fields)} WHERE id=?",
            values,
        )
        row = conn.execute("SELECT * FROM staff WHERE id=?", (staff_id,)).fetchone()
        return dict(row) if row else None


def delete_staff(staff_id: int) -> bool:
    with get_db() as conn:
        cursor = conn.execute("DELETE FROM staff WHERE id=?", (staff_id,))
        return cursor.rowcount > 0