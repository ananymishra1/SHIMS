from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from ..database import get_db
from .auth import require_user

router = APIRouter(dependencies=[Depends(require_user)])


class StudentIn(BaseModel):
    admission_number: str
    full_name: str
    grade: str
    section: str
    parent_phone: Optional[str] = None


@router.post("/api/students")
def admit_student(body: StudentIn):
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO student (admission_number, full_name, grade, section, parent_phone) VALUES (?, ?, ?, ?, ?)",
            (body.admission_number, body.full_name, body.grade, body.section, body.parent_phone),
        )
        conn.commit()
        return {"id": cur.lastrowid, **body.dict()}


@router.get("/api/students")
def list_students():
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM student").fetchall()
        return [dict(r) for r in rows]


@router.get("/api/students/{id}")
def get_student(id: int):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM student WHERE id = ?", (id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Student not found")
        return dict(row)