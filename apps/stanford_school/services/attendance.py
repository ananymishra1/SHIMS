from ..database import get_db
import time


def mark_attendance(data: dict) -> dict:
    with get_db() as conn:
        cursor = conn.execute(
            "INSERT INTO attendance (student_id, date, status) VALUES (?, ?, ?)",
            (data["student_id"], data["date"], data["status"]),
        )
        row = conn.execute(
            "SELECT * FROM attendance WHERE id=?", (cursor.lastrowid,)
        ).fetchone()
        return dict(row)


def get_attendance(student_id: int = None, date: str = None) -> list:
    with get_db() as conn:
        if student_id and date:
            rows = conn.execute(
                "SELECT * FROM attendance WHERE student_id=? AND date=?",
                (student_id, date),
            ).fetchall()
        elif student_id:
            rows = conn.execute(
                "SELECT * FROM attendance WHERE student_id=?", (student_id,)
            ).fetchall()
        elif date:
            rows = conn.execute(
                "SELECT * FROM attendance WHERE date=?", (date,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM attendance").fetchall()
        return [dict(r) for r in rows]


def get_attendance_summary(student_id: int) -> dict:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) as count FROM attendance WHERE student_id=? GROUP BY status",
            (student_id,),
        ).fetchall()
        summary = {r["status"]: r["count"] for r in rows}
        total = sum(summary.values())
        present = summary.get("present", 0)
        percentage = round((present / total * 100), 2) if total > 0 else 0.0
        return {
            "student_id": student_id,
            "total": total,
            "present": present,
            "absent": summary.get("absent", 0),
            "late": summary.get("late", 0),
            "percentage": percentage,
        }