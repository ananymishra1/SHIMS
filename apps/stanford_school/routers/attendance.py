from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from ..database import get_db
from .auth import require_user

router = APIRouter(dependencies=[Depends(require_user)])


class AttendanceIn(BaseModel):
    student_id: int
    date: str
    status: str


@router.post("/api/attendance")
def mark_attendance(body: AttendanceIn):
    with get_db() as conn:
        cur = conn.execute(
            "SELECT id FROM student WHERE id = ?", (body.student_id,)
        )
        if cur.fetchone() is None:
            raise HTTPException(status_code=404, detail="Student not found")
        conn.execute(
            "INSERT INTO attendance (student_id, date, status) VALUES (?, ?, ?)",
            (body.student_id, body.date, body.status),
        )
        conn.commit()
        row = conn.execute(
            "SELECT id, student_id, date, status FROM attendance WHERE rowid = last_insert_rowid()"
        ).fetchone()
    return {"id": row[0], "student_id": row[1], "date": row[2], "status": row[3]}


@router.get("/api/attendance")
def get_attendance(student_id: Optional[int] = None, date: Optional[str] = None):
    with get_db() as conn:
        query = "SELECT id, student_id, date, status FROM attendance WHERE 1=1"
        params = []
        if student_id is not None:
            query += " AND student_id = ?"
            params.append(student_id)
        if date is not None:
            query += " AND date = ?"
            params.append(date)
        rows = conn.execute(query, params).fetchall()
    return [
        {"id": r[0], "student_id": r[1], "date": r[2], "status": r[3]}
        for r in rows
    ]