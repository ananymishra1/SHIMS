"""Lab orders and results."""
from __future__ import annotations

from typing import Any

from ..database import execute, insert, query_all, query_one


def create_order(visit_id: int, data: dict[str, Any], ordered_by: int | None = None) -> dict[str, Any]:
    oid = insert(
        "INSERT INTO lab_orders (visit_id, test_name, category, status, ordered_by) VALUES (?, ?, ?, ?, ?)",
        (visit_id, data["test_name"], data.get("category"), data.get("status", "ordered"), ordered_by),
    )
    return query_one("SELECT * FROM lab_orders WHERE id=?", (oid,))


def list_orders(visit_id: int | None = None, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    where = ["1=1"]
    params: list[Any] = []
    if visit_id:
        where.append("visit_id = ?")
        params.append(visit_id)
    if status:
        where.append("status = ?")
        params.append(status)
    sql = f"SELECT * FROM lab_orders WHERE {' AND '.join(where)} ORDER BY ordered_at DESC LIMIT ?"
    params.append(limit)
    return query_all(sql, tuple(params))


def update_order_status(order_id: int, status: str) -> dict[str, Any] | None:
    execute("UPDATE lab_orders SET status=?, reported_at=CURRENT_TIMESTAMP WHERE id=?", (status, order_id))
    return query_one("SELECT * FROM lab_orders WHERE id=?", (order_id,))


def add_result(order_id: int, data: dict[str, Any], reported_by: int | None = None) -> dict[str, Any]:
    rid = insert(
        "INSERT INTO lab_results (lab_order_id, parameter, value, unit, reference_range, status, notes, reported_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (order_id, data["parameter"], data.get("value"), data.get("unit"), data.get("reference_range"), data.get("status", "normal"), data.get("notes"), reported_by),
    )
    execute("UPDATE lab_orders SET status='reported' WHERE id=?", (order_id,))
    return query_one("SELECT * FROM lab_results WHERE id=?", (rid,))


def get_results(order_id: int) -> list[dict[str, Any]]:
    return query_all("SELECT * FROM lab_results WHERE lab_order_id=? ORDER BY reported_at DESC", (order_id,))
