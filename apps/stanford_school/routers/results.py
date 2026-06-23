from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from ..database import get_db
from .auth import require_user

router = APIRouter(dependencies=[Depends(require_user)])


class ResultIn(BaseModel):
    student_id: int
    exam_id: int
    marks: float


@router.post("/api/results")
def record_result(body: ResultIn):
    with get_db() as conn:
        cur = conn.execute(
            "SELECT id FROM student WHERE id = ?", (body.student_id,)
        )
        if cur.fetchone() is None:
            raise HTTPException(status_code=404, detail="Student not found")
        cur = conn.execute(
            "SELECT id, max_marks FROM exam WHERE id = ?", (body.exam_id,)
        )
        exam = cur.fetchone()
        if exam is None:
            raise HTTPException(status_code=404, detail="Exam not found")
        if body.marks < 0 or body.marks > exam["max_marks"]:
            raise HTTPException(
                status_code=400,
                detail=f"Marks must be between 0 and {exam['max_marks']}",
            )
        cur = conn.execute(
            "INSERT INTO result (student_id, exam_id, marks) VALUES (?, ?, ?)",
            (body.student_id, body.exam_id, body.marks),
        )
        conn.commit()
        result_id = cur.lastrowid
        row = conn.execute(
            "SELECT * FROM result WHERE id = ?", (result_id,)
        ).fetchone()
        return dict(row)


@router.get("/api/results")
def list_results(student_id: Optional[int] = None, exam_id: Optional[int] = None):
    with get_db() as conn:
        query = """
            SELECT r.id, r.student_id, s.full_name AS student_name,
                   r.exam_id, e.name AS exam_name, e.subject, e.grade,
                   e.max_marks, r.marks,
                   ROUND(r.marks * 100.0 / e.max_marks, 2) AS percentage
            FROM result r
            JOIN student s ON s.id = r.student_id
            JOIN exam e ON e.id = r.exam_id
        """
        params = []
        conditions = []
        if student_id is not None:
            conditions.append("r.student_id = ?")
            params.append(student_id)
        if exam_id is not None:
            conditions.append("r.exam_id = ?")
            params.append(exam_id)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY r.id DESC"
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]