from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from ..database import get_db
from .auth import require_user

router = APIRouter(dependencies=[Depends(require_user)])


class ExamCreate(BaseModel):
    name: str
    subject: str
    grade: str
    max_marks: float


@router.post("/api/exams")
def create_exam(exam: ExamCreate):
    with get_db() as conn:
        cursor = conn.execute(
            "INSERT INTO exam (name, subject, grade, max_marks) VALUES (?, ?, ?, ?)",
            (exam.name, exam.subject, exam.grade, exam.max_marks),
        )
        conn.commit()
        exam_id = cursor.lastrowid
        row = conn.execute("SELECT * FROM exam WHERE id = ?", (exam_id,)).fetchone()
        return dict(row)


@router.get("/api/exams")
def list_exams(grade: Optional[str] = None, subject: Optional[str] = None):
    with get_db() as conn:
        query = "SELECT * FROM exam WHERE 1=1"
        params = []
        if grade:
            query += " AND grade = ?"
            params.append(grade)
        if subject:
            query += " AND subject = ?"
            params.append(subject)
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


@router.get("/api/exams/{exam_id}")
def get_exam(exam_id: int):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM exam WHERE id = ?", (exam_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Exam not found")
        return dict(row)


@router.put("/api/exams/{exam_id}")
def update_exam(exam_id: int, exam: ExamCreate):
    with get_db() as conn:
        existing = conn.execute("SELECT * FROM exam WHERE id = ?", (exam_id,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Exam not found")
        conn.execute(
            "UPDATE exam SET name = ?, subject = ?, grade = ?, max_marks = ? WHERE id = ?",
            (exam.name, exam.subject, exam.grade, exam.max_marks, exam_id),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM exam WHERE id = ?", (exam_id,)).fetchone()
        return dict(row)


@router.delete("/api/exams/{exam_id}")
def delete_exam(exam_id: int):
    with get_db() as conn:
        existing = conn.execute("SELECT * FROM exam WHERE id = ?", (exam_id,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Exam not found")
        conn.execute("DELETE FROM exam WHERE id = ?", (exam_id,))
        conn.commit()
        return {"detail": "Exam deleted", "id": exam_id}