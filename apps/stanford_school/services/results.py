from ..database import get_db
import time


def create_result(data: dict) -> dict:
    with get_db() as conn:
        cursor = conn.execute(
            "INSERT INTO results (student_id, exam_id, marks, created_at) VALUES (?, ?, ?, ?)",
            (data["student_id"], data["exam_id"], data["marks"], time.time()),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM results WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()
    return dict(row)


def list_results(student_id: int = None, exam_id: int = None) -> list:
    with get_db() as conn:
        if student_id and exam_id:
            rows = conn.execute(
                "SELECT * FROM results WHERE student_id = ? AND exam_id = ?",
                (student_id, exam_id),
            ).fetchall()
        elif student_id:
            rows = conn.execute(
                "SELECT * FROM results WHERE student_id = ?", (student_id,)
            ).fetchall()
        elif exam_id:
            rows = conn.execute(
                "SELECT * FROM results WHERE exam_id = ?", (exam_id,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM results").fetchall()
    return [dict(r) for r in rows]


def get_result(result_id: int) -> dict | None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM results WHERE id = ?", (result_id,)
        ).fetchone()
    return dict(row) if row else None


def update_result(result_id: int, data: dict) -> dict | None:
    with get_db() as conn:
        existing = conn.execute(
            "SELECT * FROM results WHERE id = ?", (result_id,)
        ).fetchone()
        if not existing:
            return None
        conn.execute(
            "UPDATE results SET student_id = ?, exam_id = ?, marks = ? WHERE id = ?",
            (
                data.get("student_id", existing["student_id"]),
                data.get("exam_id", existing["exam_id"]),
                data.get("marks", existing["marks"]),
                result_id,
            ),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM results WHERE id = ?", (result_id,)
        ).fetchone()
    return dict(row)


def delete_result(result_id: int) -> bool:
    with get_db() as conn:
        existing = conn.execute(
            "SELECT * FROM results WHERE id = ?", (result_id,)
        ).fetchone()
        if not existing:
            return False
        conn.execute("DELETE FROM results WHERE id = ?", (result_id,))
        conn.commit()
    return True


def get_student_results_with_details(student_id: int) -> list:
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT r.id, r.student_id, r.exam_id, r.marks,
                   e.name as exam_name, e.subject, e.grade, e.max_marks,
                   s.full_name as student_name, s.admission_number
            FROM results r
            JOIN exams e ON r.exam_id = e.id
            JOIN students s ON r.student_id = s.id
            WHERE r.student_id = ?
            """,
            (student_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_exam_results_with_details(exam_id: int) -> list:
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT r.id, r.student_id, r.exam_id, r.marks,
                   e.name as exam_name, e.subject, e.grade, e.max_marks,
                   s.full_name as student_name, s.admission_number
            FROM results r
            JOIN exams e ON r.exam_id = e.id
            JOIN students s ON r.student_id = s.id
            WHERE r.exam_id = ?
            ORDER BY r.marks DESC
            """,
            (exam_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_results_summary() -> dict:
    with get_db() as conn:
        total = conn.execute("SELECT COUNT(*) as cnt FROM results").fetchone()["cnt"]
        avg_marks = conn.execute(
            "SELECT AVG(marks) as avg FROM results"
        ).fetchone()["avg"]
        top_scorers = conn.execute(
            """
            SELECT s.full_name, s.admission_number, AVG(r.marks) as avg_marks
            FROM results r
            JOIN students s ON r.student_id = s.id
            GROUP BY r.student_id
            ORDER BY avg_marks DESC
            LIMIT 5
            """
        ).fetchall()
        at_risk = conn.execute(
            """
            SELECT s.full_name, s.admission_number, AVG(r.marks * 100.0 / e.max_marks) as pct
            FROM results r
            JOIN students s ON r.student_id = s.id
            JOIN exams e ON r.exam_id = e.id
            GROUP BY r.student_id
            HAVING pct < 40
            ORDER BY pct ASC
            LIMIT 10
            """
        ).fetchall()
    return {
        "total_results": total,
        "average_marks": round(avg_marks, 2) if avg_marks else 0,
        "top_scorers": [dict(r) for r in top_scorers],
        "at_risk_students": [dict(r) for r in at_risk],
    }