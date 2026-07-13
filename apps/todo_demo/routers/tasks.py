from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, List
from ..database import get_db

router = APIRouter()


class TaskCreate(BaseModel):
    title: str
    done: Optional[bool] = False


class TaskOut(BaseModel):
    id: int
    title: str
    done: bool


@router.post("/api/tasks")
def create_task(body: TaskCreate):
    with get_db() as conn:
        cursor = conn.execute(
            "INSERT INTO tasks (title, done) VALUES (?, ?)",
            (body.title, 1 if body.done else 0),
        )
        task_id = cursor.lastrowid
        row = conn.execute("SELECT id, title, done FROM tasks WHERE id = ?", (task_id,)).fetchone()
    return {"id": row[0], "title": row[1], "done": bool(row[2])}


@router.get("/api/tasks")
def list_tasks():
    with get_db() as conn:
        rows = conn.execute("SELECT id, title, done FROM tasks").fetchall()
    return [{"id": r[0], "title": r[1], "done": bool(r[2])} for r in rows]