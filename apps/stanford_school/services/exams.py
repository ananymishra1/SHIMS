from ..database import get_db
import time


def create_exam(data: dict) -> dict:
    with get_db() as conn:
        cursor = conn.execute(
            "INSERT INTO exams (name, subject, grade, max_marks, created_at) VALUES (?, ?, ?, ?, ?)",
            (data["name"], data["subject"], data["grade"], data["max_marks"], time.time()),
        )
        exam_id = cursor.lastrowid
        row = conn.execute("SELECT * FROM exams WHERE id = ?", (exam_id,)).fetchone()
        return dict(row)


def list_exams(grade: str = None) -> list:
    with get_db() as conn:
        if grade:
            rows = conn.execute(
                "SELECT * FROM exams WHERE grade = ? ORDER BY created_at DESC",
                (grade,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM exams ORDER BY created_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]


def get_exam(exam_id: int) -> dict:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM exams WHERE id = ?", (exam_id,)).fetchone()
        if row is None:
            return None
        return dict(row)


def delete_exam(exam_id: int) -> bool:
    with get_db() as conn:
        cursor = conn.execute("DELETE FROM exams WHERE id = ?", (exam_id,))
        return cursor.rowcount > 0