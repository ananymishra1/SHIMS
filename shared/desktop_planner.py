"""Long-horizon task planner — persists plans, executes them in waves, and resumes across sessions.

The desktop planner turns a user goal into a DAG of steps, runs each wave of independent
steps through the agent loop, and persists state so multi-minute or multi-hour tasks survive
restarts. It integrates with the existing agent_tool/action_ledger approval system.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from .action_ledger import record_action
from .config import STORAGE_DIR
from .security import new_id

PLANNER_DB = STORAGE_DIR / "state" / "desktop_planner.sqlite3"
PLANNER_DB.parent.mkdir(parents=True, exist_ok=True)


@dataclass
class PlanStep:
    step_id: str
    description: str
    tool_hint: str | None = None
    depends_on: list[str] | None = None
    status: str = "pending"  # pending, running, done, failed, skipped
    result: dict[str, Any] | None = None
    started_at: float | None = None
    finished_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Plan:
    plan_id: str
    goal: str
    steps: list[PlanStep]
    status: str = "active"
    created_at: float | None = None
    updated_at: float | None = None
    context: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "goal": self.goal,
            "steps": [s.to_dict() for s in self.steps],
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "context": self.context or {},
        }


def _connect() -> sqlite3.Connection:
    con = sqlite3.connect(str(PLANNER_DB))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS plans (
            plan_id TEXT PRIMARY KEY,
            goal TEXT NOT NULL,
            steps_json TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            context_json TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        )
        """
    )
    con.execute("CREATE INDEX IF NOT EXISTS idx_plans_status ON plans(status)")
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


def create_plan(goal: str, steps: list[dict[str, Any]], context: dict[str, Any] | None = None) -> Plan:
    plan_id = new_id("plan")
    plan_steps = []
    for i, s in enumerate(steps):
        step_id = s.get("step_id") if s.get("step_id") else f"s{i+1}"
        plan_steps.append(
            PlanStep(
                step_id=step_id,
                description=s["description"],
                tool_hint=s.get("tool_hint"),
                depends_on=s.get("depends_on") or [],
            )
        )
    plan = Plan(plan_id=plan_id, goal=goal, steps=plan_steps, context=context or {})
    plan.created_at = _now()
    plan.updated_at = _now()
    with _connect() as con:
        con.execute(
            "INSERT INTO plans (plan_id, goal, steps_json, status, context_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                plan.plan_id,
                plan.goal,
                _json([s.to_dict() for s in plan.steps]),
                plan.status,
                _json(plan.context),
                plan.created_at,
                plan.updated_at,
            ),
        )
        con.commit()
    record_action("plan.create", f"Created plan {plan_id}: {goal}", result={"plan_id": plan_id}, requested_level="L1")
    return plan


def get_plan(plan_id: str) -> Plan | None:
    with _connect() as con:
        row = con.execute("SELECT * FROM plans WHERE plan_id = ?", (plan_id,)).fetchone()
    if not row:
        return None
    steps = [PlanStep(**s) for s in _load_json(row["steps_json"], [])]
    return Plan(
        plan_id=row["plan_id"],
        goal=row["goal"],
        steps=steps,
        status=row["status"],
        context=_load_json(row["context_json"], {}),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def list_plans(status: str | None = None, limit: int = 50) -> list[Plan]:
    with _connect() as con:
        if status:
            rows = con.execute("SELECT * FROM plans WHERE status = ? ORDER BY updated_at DESC LIMIT ?", (status, limit)).fetchall()
        else:
            rows = con.execute("SELECT * FROM plans ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall()
    out = []
    for row in rows:
        steps = [PlanStep(**s) for s in _load_json(row["steps_json"], [])]
        out.append(
            Plan(
                plan_id=row["plan_id"],
                goal=row["goal"],
                steps=steps,
                status=row["status"],
                context=_load_json(row["context_json"], {}),
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
        )
    return out


def _save_plan_state(plan: Plan) -> None:
    plan.updated_at = _now()
    with _connect() as con:
        con.execute(
            "UPDATE plans SET steps_json = ?, status = ?, context_json = ?, updated_at = ? WHERE plan_id = ?",
            (
                _json([s.to_dict() for s in plan.steps]),
                plan.status,
                _json(plan.context or {}),
                plan.updated_at,
                plan.plan_id,
            ),
        )
        con.commit()


def plan_from_goal(goal: str, *, planner_llm_fn=None, context: dict[str, Any] | None = None) -> Plan:
    """Generate a plan from a goal using an LLM planner, with keyword fallback."""
    if planner_llm_fn:
        try:
            planned = planner_llm_fn(goal)
            if planned and isinstance(planned, list):
                return create_plan(goal, planned, context)
            if planned and isinstance(planned, dict) and "steps" in planned:
                return create_plan(goal, planned["steps"], context)
        except Exception:
            pass
    # Try LLM planner via local Ollama
    try:
        steps = _llm_plan_steps(goal)
        if steps:
            return create_plan(goal, steps, context)
    except Exception:
        pass
    # Fallback: split obvious sequential tasks
    steps = _fallback_plan(goal)
    return create_plan(goal, steps, context)


def _llm_plan_steps(goal: str) -> list[dict[str, Any]]:
    """Ask a cheap local model to turn a goal into a DAG of plan steps."""
    if not goal or len(goal) < 10:
        return []

    system_prompt = (
        "You are a task planner for an AI assistant. Convert the user's goal into a JSON array of steps. "
        "Each step must have: step_id (e.g. s1), description (concise action), tool_hint (one of the allowed tools), "
        "and optional depends_on (list of step_ids that must finish first). "
        "Return ONLY the JSON array. No markdown, no explanation."
    )
    user_prompt = (
        f"Goal: {goal[:1500]}\n\n"
        "Allowed tool_hint values:\n"
        "- agent.run: general reasoning or multi-tool work (default)\n"
        "- web.search: find current information online\n"
        "- memory.search: recall prior user facts or stored knowledge\n"
        "- memory.save: persist a fact or finding to long-term memory\n"
        "- desktop.interpreter: run Python code or analyze data\n"
        "- vision.describe: analyze an image or video\n"
        "- mail.digest: check email inbox\n"
        "- plan.run_wave: execute the next ready wave of this plan\n\n"
        "Rules:\n"
        "- Break the goal into 2-8 concrete, ordered steps.\n"
        "- Every step MUST have a tool_hint chosen from the allowed list. Pick the specific tool that matches the action; only use agent.run for generic reasoning that needs no other tool.\n"
        "- Use depends_on to make steps wait for prerequisites.\n"
        "- Prefer parallel steps when independent.\n"
        "- Keep each description under 120 characters.\n"
        'Example: [{"step_id":"s1","description":"Search web for latest fluconazole prices","tool_hint":"web.search","depends_on":[]}, {"step_id":"s2","description":"Recall prior supplier knowledge","tool_hint":"memory.search","depends_on":[]}, {"step_id":"s3","description":"Draft procurement memo","tool_hint":"agent.run","depends_on":["s1","s2"]}, {"step_id":"s4","description":"Save memo to long-term memory","tool_hint":"memory.save","depends_on":["s3"]}]'
    )

    model = os.getenv("SHIMS_PLANNER_MODEL", "qwen2.5:7b")
    host = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "stream": False,
        "options": {"temperature": 0.2, "num_predict": 1024},
        "keep_alive": "5m",
    }

    with httpx.Client(timeout=90.0) as client:
        r = client.post(f"{host}/api/chat", json=payload)
        r.raise_for_status()
        data = r.json()

    raw = (data.get("message") or {}).get("content") or data.get("response") or ""
    raw = raw.strip()
    if "[" in raw and "]" in raw:
        raw = raw[raw.find("[") : raw.rfind("]") + 1]
    if not raw:
        return []

    parsed = json.loads(raw)
    if not isinstance(parsed, list):
        return []

    valid_tools = {
        "agent.run", "web.search", "memory.search", "memory.save", "desktop.interpreter",
        "vision.describe", "mail.digest", "plan.run_wave",
    }

    def _infer_tool_hint(description: str, raw_hint: str) -> str:
        hint = (raw_hint or "").strip().lower()
        if hint in valid_tools and hint != "agent.run":
            return hint
        desc = description.lower()
        if any(k in desc for k in ("web", "internet", "online", "google", "price trend", "market price", "latest price", "latest news", "research")):
            return "web.search"
        if any(k in desc for k in ("shims knowledge", "search memory", "find in memory", "recall", "prior knowledge", "stored knowledge")):
            return "memory.search"
        if any(k in desc for k in ("save", "remember this", "persist", "store in memory", "save finding")) and "memory" in desc:
            return "memory.save"
        if any(k in desc for k in ("python", "code", "calculate", "plot", "analyze data", "run script")):
            return "desktop.interpreter"
        if any(k in desc for k in ("image", "screenshot", "video", "frame", "describe visual")):
            return "vision.describe"
        if any(k in desc for k in ("email", "mail", "inbox", "gmail")):
            return "mail.digest"
        return "agent.run"

    steps: list[dict[str, Any]] = []

    for item in parsed:
        if not isinstance(item, dict):
            continue
        description = str(item.get("description") or item.get("desc") or "").strip()
        if not description:
            continue
        tool_hint = _infer_tool_hint(description, str(item.get("tool_hint") or "agent.run"))
        step_id = str(item.get("step_id") or f"s{len(steps)+1}").strip()
        depends_on = item.get("depends_on") or []
        if not isinstance(depends_on, list):
            depends_on = [str(depends_on)]
        depends_on = [str(d).strip() for d in depends_on if d]
        steps.append({
            "step_id": step_id,
            "description": description,
            "tool_hint": tool_hint,
            "depends_on": depends_on,
        })
    return steps


def _fallback_plan(goal: str) -> list[dict[str, Any]]:
    """Naive planner for when no LLM is available."""
    chunks = [s.strip("- ") for s in goal.replace(",", "\n").split("\n") if s.strip()]
    if len(chunks) <= 1:
        return [{"description": goal, "tool_hint": "agent.run"}]
    steps = []
    prev = None
    for i, c in enumerate(chunks):
        sid = f"s{i+1}"
        s = {"step_id": sid, "description": c, "tool_hint": "agent.run"}
        if prev:
            s["depends_on"] = [prev]
        steps.append(s)
        prev = sid
    return steps


def _ready_steps(plan: Plan) -> list[PlanStep]:
    done = {s.step_id for s in plan.steps if s.status in {"done", "skipped"}}
    failed = {s.step_id for s in plan.steps if s.status == "failed"}
    ready = []
    for s in plan.steps:
        if s.status != "pending":
            continue
        deps = set(s.depends_on or [])
        if deps & failed:
            s.status = "skipped"
            s.finished_at = _now()
            continue
        if deps.issubset(done):
            ready.append(s)
    return ready


def execute_plan_wave(plan_id: str, step_executor) -> dict[str, Any]:
    """Run one wave of ready steps and persist state. Call repeatedly until done."""
    plan = get_plan(plan_id)
    if not plan:
        return {"ok": False, "error": "plan not found"}
    if plan.status in {"completed", "failed", "cancelled"}:
        return {"ok": True, "plan": plan.to_dict(), "note": "plan already terminal"}

    ready = _ready_steps(plan)
    if not ready:
        pending = [s for s in plan.steps if s.status == "pending"]
        if not pending:
            plan.status = "completed"
            _save_plan_state(plan)
            return {"ok": True, "plan": plan.to_dict(), "note": "all steps complete"}
        return {"ok": True, "plan": plan.to_dict(), "note": "waiting on dependencies"}

    results = []
    for step in ready:
        step.status = "running"
        step.started_at = _now()
    _save_plan_state(plan)

    for step in ready:
        try:
            result = step_executor(step, plan)
            step.result = result if isinstance(result, dict) else {"result": result}
            step.status = "done" if step.result.get("ok", True) else "failed"
        except Exception as exc:
            step.result = {"ok": False, "error": str(exc)[:500]}
            step.status = "failed"
        step.finished_at = _now()
        results.append({"step_id": step.step_id, "status": step.status})

    if any(s.status == "failed" for s in plan.steps):
        plan.status = "failed"
    elif all(s.status in {"done", "skipped"} for s in plan.steps):
        plan.status = "completed"
    _save_plan_state(plan)
    record_action("plan.wave", f"Plan {plan_id} wave executed", result={"results": results}, requested_level="L1")
    # Auto-learn from successfully completed plans
    if plan.status == "completed":
        try:
            from .plan_learning import plan_to_skill
            plan_to_skill(plan.plan_id)
        except Exception:
            pass
    return {"ok": True, "plan": plan.to_dict(), "wave_results": results}


def cancel_plan(plan_id: str) -> dict[str, Any]:
    plan = get_plan(plan_id)
    if not plan:
        return {"ok": False, "error": "plan not found"}
    plan.status = "cancelled"
    for s in plan.steps:
        if s.status == "pending":
            s.status = "skipped"
            s.finished_at = _now()
    _save_plan_state(plan)
    return {"ok": True, "plan": plan.to_dict()}


def delete_plan(plan_id: str) -> dict[str, Any]:
    with _connect() as con:
        con.execute("DELETE FROM plans WHERE plan_id = ?", (plan_id,))
        con.commit()
    return {"ok": True}
