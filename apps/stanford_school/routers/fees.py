from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from ..database import get_db
from .auth import require_user

router = APIRouter(dependencies=[Depends(require_user)])


class FeeIn(BaseModel):
    student_id: int
    amount: float
    term: str
    paid: Optional[float] = None


@router.post("/api/fees")
def add_fee(fee: FeeIn):
    with get_db() as conn:
        cursor = conn.execute(
            "INSERT INTO fee (student_id, amount, term, paid) VALUES (?, ?, ?, ?)",
            (fee.student_id, fee.amount, fee.term, fee.paid),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM fee WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=500, detail="Failed to create fee record")
    return dict(row)


@router.get("/api/fees")
def list_fees(student_id: Optional[int] = None, term: Optional[str] = None):
    with get_db() as conn:
        query = "SELECT * FROM fee WHERE 1=1"
        params = []
        if student_id is not None:
            query += " AND student_id = ?"
            params.append(student_id)
        if term is not None:
            query += " AND term = ?"
            params.append(term)
        rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]