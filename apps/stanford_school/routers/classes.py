from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from ..database import get_db
from .auth import require_user

router = APIRouter(dependencies=[Depends(require_user)])


class ClassIn(BaseModel):
    grade: str
    section: str
    class_teacher_id: Optional[int] = None


@router.post("/api/classes")
def create_class(body: ClassIn):
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO class (grade, section, class_teacher_id) VALUES (?, ?, ?)",
            (body.grade, body.section, body.class_teacher_id),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM class WHERE id = ?", (cur.lastrowid,)).fetchone()
    return dict(row)


@router.get("/api/classes")
def list_classes():
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM class").fetchall()
    return [dict(r) for r in rows]