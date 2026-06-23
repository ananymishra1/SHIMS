from ..database import get_db
import time


def create_fee(data: dict) -> dict:
    with get_db() as conn:
        cursor = conn.execute(
            "INSERT INTO fees (student_id, amount, term, paid, created_at) VALUES (?, ?, ?, ?, ?)",
            (
                data["student_id"],
                data["amount"],
                data["term"],
                data.get("paid", 0),
                time.time(),
            ),
        )
        fee_id = cursor.lastrowid
        row = conn.execute("SELECT * FROM fees WHERE id = ?", (fee_id,)).fetchone()
        return dict(row)


def list_fees(student_id: int = None, term: str = None) -> list:
    with get_db() as conn:
        query = "SELECT * FROM fees WHERE 1=1"
        params = []
        if student_id is not None:
            query += " AND student_id = ?"
            params.append(student_id)
        if term is not None:
            query += " AND term = ?"
            params.append(term)
        query += " ORDER BY id DESC"
        rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]


def get_fee(fee_id: int) -> dict:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM fees WHERE id = ?", (fee_id,)).fetchone()
        return dict(row) if row else None


def update_fee(fee_id: int, data: dict) -> dict:
    with get_db() as conn:
        fields = []
        params = []
        for key in ("amount", "term", "paid"):
            if key in data:
                fields.append(f"{key} = ?")
                params.append(data[key])
        if not fields:
            row = conn.execute("SELECT * FROM fees WHERE id = ?", (fee_id,)).fetchone()
            return dict(row) if row else None
        params.append(fee_id)
        conn.execute(f"UPDATE fees SET {', '.join(fields)} WHERE id = ?", params)
        row = conn.execute("SELECT * FROM fees WHERE id = ?", (fee_id,)).fetchone()
        return dict(row) if row else None


def delete_fee(fee_id: int) -> bool:
    with get_db() as conn:
        cursor = conn.execute("DELETE FROM fees WHERE id = ?", (fee_id,))
        return cursor.rowcount > 0


def get_fee_summary() -> dict:
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM fees").fetchall()
        total_billed = sum(row["amount"] for row in rows)
        total_paid = sum(row["paid"] or 0 for row in rows)
        total_due = total_billed - total_paid
        unpaid_count = sum(1 for row in rows if (row["paid"] or 0) < row["amount"])
        return {
            "total_billed": total_billed,
            "total_paid": total_paid,
            "total_due": total_due,
            "unpaid_count": unpaid_count,
            "total_records": len(rows),
        }


def get_student_fee_status(student_id: int) -> dict:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM fees WHERE student_id = ?", (student_id,)
        ).fetchall()
        fees = [dict(row) for row in rows]
        total_billed = sum(f["amount"] for f in fees)
        total_paid = sum(f["paid"] or 0 for f in fees)
        return {
            "student_id": student_id,
            "fees": fees,
            "total_billed": total_billed,
            "total_paid": total_paid,
            "total_due": total_billed - total_paid,
        }