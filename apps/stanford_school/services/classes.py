from ..database import get_db
import time


def create_class(data: dict) -> dict:
    with get_db() as conn:
        cursor = conn.execute(
            "INSERT INTO classes (grade, section, class_teacher_id) VALUES (?, ?, ?)",
            (data["grade"], data["section"], data.get("class_teacher_id")),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM classes WHERE id=?", (cursor.lastrowid,)
        ).fetchone()
        return dict(row)


def list_classes(grade: str = None, section: str = None) -> list:
    with get_db() as conn:
        query = "SELECT * FROM classes WHERE 1=1"
        params = []
        if grade:
            query += " AND grade=?"
            params.append(grade)
        if section:
            query += " AND section=?"
            params.append(section)
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


def get_class(class_id: int) -> dict:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM classes WHERE id=?", (class_id,)
        ).fetchone()
        if row is None:
            return None
        return dict(row)


def update_class(class_id: int, data: dict) -> dict:
    with get_db() as conn:
        existing = conn.execute(
            "SELECT * FROM classes WHERE id=?", (class_id,)
        ).fetchone()
        if existing is None:
            return None
        grade = data.get("grade", existing["grade"])
        section = data.get("section", existing["section"])
        class_teacher_id = data.get("class_teacher_id", existing["class_teacher_id"])
        conn.execute(
            "UPDATE classes SET grade=?, section=?, class_teacher_id=? WHERE id=?",
            (grade, section, class_teacher_id, class_id),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM classes WHERE id=?", (class_id,)
        ).fetchone()
        return dict(row)


def delete_class(class_id: int) -> bool:
    with get_db() as conn:
        existing = conn.execute(
            "SELECT * FROM classes WHERE id=?", (class_id,)
        ).fetchone()
        if existing is None:
            return False
        conn.execute("DELETE FROM classes WHERE id=?", (class_id,))
        conn.commit()
        return True


def get_class_with_teacher(class_id: int) -> dict:
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT c.*, s.full_name as teacher_name, s.employee_id as teacher_employee_id
            FROM classes c
            LEFT JOIN staff s ON c.class_teacher_id = s.id
            WHERE c.id=?
            """,
            (class_id,),
        ).fetchone()
        if row is None:
            return None
        return dict(row)


def list_classes_with_teachers() -> list:
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT c.*, s.full_name as teacher_name, s.employee_id as teacher_employee_id
            FROM classes c
            LEFT JOIN staff s ON c.class_teacher_id = s.id
            ORDER BY c.grade, c.section
            """
        ).fetchall()
        return [dict(r) for r in rows]


def get_class_students(class_id: int) -> list:
    with get_db() as conn:
        cls = conn.execute(
            "SELECT * FROM classes WHERE id=?", (class_id,)
        ).fetchone()
        if cls is None:
            return []
        rows = conn.execute(
            "SELECT * FROM students WHERE grade=? AND section=?",
            (cls["grade"], cls["section"]),
        ).fetchall()
        return [dict(r) for r in rows]


def count_classes() -> int:
    with get_db() as conn:
        row = conn.execute("SELECT COUNT(*) as cnt FROM classes").fetchone()
        return row["cnt"] if row else 0