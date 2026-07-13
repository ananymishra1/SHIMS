"""Desktop scheduler — time-aware task execution for SHIMS Omni.

Keeps a lightweight cron-like schedule in SQLite and exposes tool endpoints so the
agent can create, list, and cancel scheduled tasks. Tasks are run by a background
thread that polls once per minute and invokes the agent loop or a tool directly.
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .action_ledger import record_action
from .config import STORAGE_DIR
from .security import new_id

SCHEDULER_DB = STORAGE_DIR / "state" / "desktop_scheduler.sqlite3"
SCHEDULER_DB.parent.mkdir(parents=True, exist_ok=True)

_runners: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {}
_poller: threading.Thread | None = None
_stop_event = threading.Event()


@dataclass
class ScheduledTask:
    task_id: str
    title: str
    schedule_type: str  # "once", "interval", "cron"
    when: str  # ISO datetime, interval seconds, or cron expression
    action_type: str  # "tool", "plan", "message"
    payload: dict[str, Any]
    enabled: bool = True
    last_run: float | None = None
    next_run: float | None = None
    run_count: int = 0
    created_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "title": self.title,
            "schedule_type": self.schedule_type,
            "when": self.when,
            "action_type": self.action_type,
            "payload": self.payload,
            "enabled": self.enabled,
            "last_run": self.last_run,
            "next_run": self.next_run,
            "run_count": self.run_count,
            "created_at": self.created_at,
        }


def _connect() -> sqlite3.Connection:
    con = sqlite3.connect(str(SCHEDULER_DB))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS scheduled_tasks (
            task_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            schedule_type TEXT NOT NULL,
            when_expr TEXT NOT NULL,
            action_type TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            last_run REAL,
            next_run REAL,
            run_count INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL
        )
        """
    )
    con.execute("CREATE INDEX IF NOT EXISTS idx_sched_next ON scheduled_tasks(next_run)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_sched_enabled ON scheduled_tasks(enabled)")
    con.commit()
    return con


def _now() -> float:
    return time.time()


def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)


def _load_json(text: str | None, default: Any) -> Any:
    if not text:
        return default
    try:
        return json.loads(text)
    except Exception:
        return default


def _parse_when(schedule_type: str, when: str) -> float | None:
    """Compute next Unix timestamp from schedule expression."""
    try:
        if schedule_type == "once":
            dt = datetime.fromisoformat(when.replace("Z", "+00:00"))
            return dt.timestamp()
        if schedule_type == "interval":
            return _now() + max(1, int(when))
        if schedule_type == "cron":
            # Very limited cron: "M H * * *" in local time, daily only
            parts = when.strip().split()
            if len(parts) >= 2:
                minute, hour = int(parts[0]), int(parts[1])
                now = datetime.now()
                target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                if target.timestamp() <= _now():
                    # schedule for tomorrow
                    from datetime import timedelta
                    target = target + timedelta(days=1)
                return target.timestamp()
    except Exception:
        pass
    return None


def schedule_task(
    title: str,
    schedule_type: str,
    when: str,
    action_type: str,
    payload: dict[str, Any],
    task_id: str | None = None,
) -> dict[str, Any]:
    if schedule_type not in {"once", "interval", "cron"}:
        return {"ok": False, "error": "invalid schedule_type"}
    if action_type not in {"tool", "plan", "message", "inbox_ingest"}:
        return {"ok": False, "error": "invalid action_type"}
    next_run = _parse_when(schedule_type, when)
    if next_run is None:
        return {"ok": False, "error": "could not parse when expression"}
    tid = task_id or new_id("sched")
    created = _now()
    with _connect() as con:
        con.execute(
            """
            INSERT INTO scheduled_tasks
            (task_id, title, schedule_type, when_expr, action_type, payload_json, enabled, last_run, next_run, run_count, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (tid, title, schedule_type, when, action_type, _json(payload), 1, None, next_run, 0, created),
        )
        con.commit()
    record_action("scheduler.create", f"Scheduled task {tid}: {title}", result={"task_id": tid}, requested_level="L1")
    return {"ok": True, "task_id": tid, "next_run": next_run}


def list_tasks(enabled_only: bool = False, limit: int = 100) -> list[ScheduledTask]:
    with _connect() as con:
        if enabled_only:
            rows = con.execute(
                "SELECT * FROM scheduled_tasks WHERE enabled = 1 ORDER BY next_run ASC LIMIT ?",
                (limit,),
            ).fetchall()
        else:
            rows = con.execute("SELECT * FROM scheduled_tasks ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    out = []
    for row in rows:
        out.append(
            ScheduledTask(
                task_id=row["task_id"],
                title=row["title"],
                schedule_type=row["schedule_type"],
                when=row["when_expr"],
                action_type=row["action_type"],
                payload=_load_json(row["payload_json"], {}),
                enabled=bool(row["enabled"]),
                last_run=row["last_run"],
                next_run=row["next_run"],
                run_count=row["run_count"],
                created_at=row["created_at"],
            )
        )
    return out


def get_task(task_id: str) -> ScheduledTask | None:
    with _connect() as con:
        row = con.execute("SELECT * FROM scheduled_tasks WHERE task_id = ?", (task_id,)).fetchone()
    if not row:
        return None
    return ScheduledTask(
        task_id=row["task_id"],
        title=row["title"],
        schedule_type=row["schedule_type"],
        when=row["when_expr"],
        action_type=row["action_type"],
        payload=_load_json(row["payload_json"], {}),
        enabled=bool(row["enabled"]),
        last_run=row["last_run"],
        next_run=row["next_run"],
        run_count=row["run_count"],
        created_at=row["created_at"],
    )


def cancel_task(task_id: str) -> dict[str, Any]:
    with _connect() as con:
        con.execute("UPDATE scheduled_tasks SET enabled = 0 WHERE task_id = ?", (task_id,))
        con.commit()
    return {"ok": True}


def delete_task(task_id: str) -> dict[str, Any]:
    with _connect() as con:
        con.execute("DELETE FROM scheduled_tasks WHERE task_id = ?", (task_id,))
        con.commit()
    return {"ok": True}


def _advance_next_run(task: ScheduledTask) -> float | None:
    if task.schedule_type == "interval":
        try:
            return _now() + max(1, int(task.when))
        except Exception:
            return None
    if task.schedule_type == "cron":
        return _parse_when("cron", task.when)
    return None  # once-only


def _execute_task(task: ScheduledTask) -> dict[str, Any]:
    runner = _runners.get(task.action_type)
    if not runner:
        return {"ok": False, "error": f"no runner for {task.action_type}"}
    try:
        return runner(task.payload)
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:500]}


def _tick() -> int:
    now = _now()
    with _connect() as con:
        rows = con.execute(
            "SELECT * FROM scheduled_tasks WHERE enabled = 1 AND next_run IS NOT NULL AND next_run <= ?",
            (now,),
        ).fetchall()
    ran = 0
    for row in rows:
        task = ScheduledTask(
            task_id=row["task_id"],
            title=row["title"],
            schedule_type=row["schedule_type"],
            when=row["when_expr"],
            action_type=row["action_type"],
            payload=_load_json(row["payload_json"], {}),
            enabled=bool(row["enabled"]),
            last_run=row["last_run"],
            next_run=row["next_run"],
            run_count=row["run_count"],
            created_at=row["created_at"],
        )
        result = _execute_task(task)
        task.last_run = now
        task.run_count += 1
        nxt = _advance_next_run(task)
        if nxt is None:
            task.enabled = False
        else:
            task.next_run = nxt
        with _connect() as con:
            con.execute(
                "UPDATE scheduled_tasks SET last_run = ?, next_run = ?, run_count = ?, enabled = ? WHERE task_id = ?",
                (task.last_run, task.next_run, task.run_count, int(task.enabled), task.task_id),
            )
            con.commit()
        record_action(
            "scheduler.run",
            f"Ran scheduled task {task.task_id}: {task.title}",
            result={"ok": result.get("ok"), "action_type": task.action_type},
            requested_level="L1",
        )
        ran += 1
    return ran


def _loop() -> None:
    while not _stop_event.is_set():
        try:
            _tick()
        except Exception:
            pass
        # Sleep in 5s chunks so shutdown is responsive
        for _ in range(12):
            if _stop_event.is_set():
                break
            time.sleep(5)


def register_runner(action_type: str, fn: Callable[[dict[str, Any]], dict[str, Any]]) -> None:
    _runners[action_type] = fn


def start_scheduler() -> None:
    global _poller
    if _poller and _poller.is_alive():
        return
    _stop_event.clear()
    _poller = threading.Thread(target=_loop, daemon=True, name="shims-scheduler")
    _poller.start()


def stop_scheduler() -> None:
    _stop_event.set()
    if _poller:
        try:
            _poller.join(timeout=2)
        except Exception:
            pass
