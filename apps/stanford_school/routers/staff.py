from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from ..database import get_db
from .auth import require_user

router = APIRouter(dependencies=[Depends(require_user)])


class StaffIn(BaseModel):
    employee_id: str
    full_name: str
    role: str
    subject: Optional[str] = None


@router.post("/api/staff")
def add_staff(body: StaffIn):
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO staff (employee_id, full_name, role, subject) VALUES (?, ?, ?, ?)",
            (body.employee_id, body.full_name, body.role, body.subject),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM staff WHERE id = ?", (cur.lastrowid,)).fetchone()
    return dict(row)


@router.get("/api/staff")
def list_staff():
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM staff").fetchall()
    return [dict(r) for r in rows]