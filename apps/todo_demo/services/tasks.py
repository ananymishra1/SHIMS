import time
from ..database import get_db


def create_task(data: dict) -> dict:
    with get_db() as conn:
        cursor = conn.execute(
            "INSERT INTO tasks(title, done, created_at) VALUES (?, ?, ?)",
            (data["title"], int(data.get("done", False)), time.time()),
        )
        task_id = cursor.lastrowid
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return dict(row)


def list_tasks() -> list:
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM tasks ORDER BY created_at DESC").fetchall()
        return [dict(row) for row in rows]


def get_task(task_id: int) -> dict | None:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return dict(row) if row else None


def update_task(task_id: int, data: dict) -> dict | None:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not row:
            return None
        existing = dict(row)
        title = data.get("title", existing["title"])
        done = int(data.get("done", existing["done"]))
        conn.execute(
            "UPDATE tasks SET title=?, done=? WHERE id=?",
            (title, done, task_id),
        )
        updated = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return dict(updated)


def delete_task(task_id: int) -> bool:
    with get_db() as conn:
        cursor = conn.execute("DELETE FROM tasks WHERE id=?", (task_id,))
        return cursor.rowcount > 0