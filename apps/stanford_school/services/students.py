import time
from ..database import get_db


def create_student(data: dict) -> dict:
    with get_db() as conn:
        cursor = conn.execute(
            "INSERT INTO students (admission_number, full_name, grade, section, parent_phone, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                data["admission_number"],
                data["full_name"],
                data["grade"],
                data["section"],
                data.get("parent_phone"),
                time.time(),
            ),
        )
        row = conn.execute(
            "SELECT * FROM students WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()
        return dict(row)


def list_students(grade: str = None, section: str = None) -> list:
    with get_db() as conn:
        if grade and section:
            rows = conn.execute(
                "SELECT * FROM students WHERE grade = ? AND section = ? ORDER BY full_name",
                (grade, section),
            ).fetchall()
        elif grade:
            rows = conn.execute(
                "SELECT * FROM students WHERE grade = ? ORDER BY full_name", (grade,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM students ORDER BY full_name"
            ).fetchall()
        return [dict(r) for r in rows]


def get_student(student_id: int) -> dict:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM students WHERE id = ?", (student_id,)
        ).fetchone()
        return dict(row) if row else None