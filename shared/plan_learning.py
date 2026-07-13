"""Plan learning — turn successful plans into reusable skills.

When a plan completes successfully, this module extracts the step sequence and
turns it into a skill. When a plan fails, it records the failure pattern so the
planner can avoid the same mistake. This is the first layer of SHIMS Omni's
self-improvement loop.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import STORAGE_DIR
from .desktop_planner import get_plan, list_plans
from .security import new_id
from .skills import save_skill

try:
    from .telemetry import log_event
except Exception:  # pragma: no cover
    def log_event(*args: Any, **kwargs: Any) -> None:
        return None

LEARNED_PLANS_DIR = STORAGE_DIR / "plan_learning"
LEARNED_PLANS_DIR.mkdir(parents=True, exist_ok=True)
PLAN_FAILURES_PATH = LEARNED_PLANS_DIR / "plan_failures.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_goal(goal: str) -> str:
    """Normalize a goal into a skill name and keyword set."""
    text = (goal or "").strip().lower()
    # Remove politeness and filler
    text = re.sub(r"^(please |can you |could you |hey shims |hi shims |ok shims )+", "", text)
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _extract_keywords(goal: str) -> list[str]:
    """Extract useful keywords from a goal for skill matching."""
    stop = {"the", "a", "an", "and", "or", "to", "of", "in", "on", "at", "for", "with", "by", "from", "as", "is", "it", "this", "that", "i", "me", "my"}
    tokens = [t for t in _normalize_goal(goal).split() if t not in stop and len(t) > 2]
    # Deduplicate preserving order
    seen: set[str] = set()
    out: list[str] = []
    for t in tokens:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def plan_to_skill(plan_id: str) -> dict[str, Any]:
    """Convert a completed plan into a reusable skill."""
    plan = get_plan(plan_id)
    if not plan:
        return {"ok": False, "error": "plan not found"}
    if plan.status != "completed":
        return {"ok": False, "error": f"plan status is {plan.status}, not completed"}

    keywords = _extract_keywords(plan.goal)
    skill_name = f"Plan: {plan.goal[:80]}" if len(plan.goal) <= 80 else f"Plan: {plan.goal[:77]}..."
    steps = []
    for s in plan.steps:
        steps.append({
            "step_id": s.step_id,
            "description": s.description,
            "tool_hint": s.tool_hint,
            "depends_on": s.depends_on or [],
        })

    body = json.dumps({
        "plan_id": plan_id,
        "goal": plan.goal,
        "keywords": keywords,
        "steps": steps,
        "source": "plan_learning",
        "learned_at": _now(),
    }, indent=2, ensure_ascii=False)

    skill = save_skill(
        name=skill_name,
        summary=f"Learned plan for: {plan.goal}",
        body=body,
        tags=["plan", "learned"] + keywords[:5],
        source="plan_learning",
        pinned=False,
        weight=1.0,
    )

    # Persist learned plan mapping for introspection
    mapping = {
        "plan_id": plan_id,
        "skill_id": skill["id"],
        "goal": plan.goal,
        "keywords": keywords,
        "learned_at": _now(),
    }
    (LEARNED_PLANS_DIR / f"{plan_id}.json").write_text(
        json.dumps(mapping, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    log_event("plan_learning.skill_created", route="plan_learning", provider="local", model="self", ok=True,
              message=f"Created skill from plan {plan_id}", metadata={"skill_id": skill["id"], "plan_id": plan_id})
    return {"ok": True, "skill_id": skill["id"], "skill": skill}


def record_plan_failure(plan_id: str, reason: str) -> dict[str, Any]:
    """Record a plan failure pattern for planner improvement."""
    plan = get_plan(plan_id)
    failures: list[dict[str, Any]] = []
    if PLAN_FAILURES_PATH.exists():
        try:
            failures = json.loads(PLAN_FAILURES_PATH.read_text(encoding="utf-8"))
        except Exception:
            failures = []

    entry = {
        "id": new_id("plan_fail"),
        "plan_id": plan_id,
        "goal": plan.goal if plan else "",
        "keywords": _extract_keywords(plan.goal) if plan else [],
        "reason": reason,
        "recorded_at": _now(),
    }
    failures.append(entry)
    # Keep last 500 failures
    failures = failures[-500:]
    PLAN_FAILURES_PATH.write_text(json.dumps(failures, indent=2, ensure_ascii=False), encoding="utf-8")

    log_event("plan_learning.failure_recorded", route="plan_learning", provider="local", model="self", ok=False,
              message=reason, metadata={"plan_id": plan_id})
    return {"ok": True, "failure_id": entry["id"]}


def find_similar_learned_plan(goal: str, limit: int = 3) -> list[dict[str, Any]]:
    """Find previously learned plans whose keywords overlap with the goal."""
    goal_keywords = set(_extract_keywords(goal))
    if not goal_keywords:
        return []
    candidates: list[tuple[float, dict[str, Any]]] = []
    for p in LEARNED_PLANS_DIR.glob("*.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            overlap = len(goal_keywords & set(data.get("keywords", [])))
            if overlap:
                score = overlap / (len(goal_keywords) + 1)
                candidates.append((score, data))
        except Exception:
            continue
    candidates.sort(key=lambda x: x[0], reverse=True)
    return [c[1] for c in candidates[:limit]]


def learn_from_completed_plans(min_steps: int = 2, limit: int = 20) -> dict[str, Any]:
    """Scan recently completed plans and convert new ones to skills."""
    plans = list_plans(status="completed", limit=limit)
    created = 0
    skipped = 0
    for plan in plans:
        if len(plan.steps) < min_steps:
            skipped += 1
            continue
        mapping_path = LEARNED_PLANS_DIR / f"{plan.plan_id}.json"
        if mapping_path.exists():
            skipped += 1
            continue
        result = plan_to_skill(plan.plan_id)
        if result.get("ok"):
            created += 1
        else:
            skipped += 1
    return {"ok": True, "created": created, "skipped": skipped}


def suggest_plan_for_goal(goal: str) -> dict[str, Any]:
    """Suggest a learned plan for a new goal, if one exists."""
    matches = find_similar_learned_plan(goal)
    if not matches:
        return {"ok": True, "found": False, "matches": []}
    best = matches[0]
    return {"ok": True, "found": True, "best_match": best, "all_matches": matches}
