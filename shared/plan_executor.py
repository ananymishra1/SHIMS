"""Plan executor — runs plan steps through SHIMS tools or the agent loop.

This bridges the persistent planner (`desktop_planner.py`) with the live agent so
long-horizon tasks can actually execute without human babysitting.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from . import agent_tools
from .config import settings
from .desktop_planner import PlanStep, Plan, execute_plan_wave, get_plan


_MAX_STEP_RETRIES = 2
_STEP_RETRY_BACKOFF_BASE = 1.5


def _step_executor(step: PlanStep, plan: Plan) -> dict[str, Any]:
    """Execute a single plan step with retry/backoff.

    If the step has a `tool_hint` like `shell.run` or `desktop.interpreter`,
    run that tool directly. If the hint is `agent.run` or missing, treat the
    step description as a chat message and run it through the real wave-based
    agent loop.
    """
    hint = (step.tool_hint or "agent.run").strip()
    description = step.description.strip()
    last_result: dict[str, Any] = {}

    for attempt in range(_MAX_STEP_RETRIES + 1):
        if hint != "agent.run" and "." in hint:
            tool_name = hint
            args = _infer_tool_args(tool_name, description, plan)
            result = agent_tools.run_tool(tool_name, args, allow_gated=False)
        else:
            result = _run_agent_loop_step(description, plan)

        last_result = result if isinstance(result, dict) else {"ok": True, "result": result}
        if last_result.get("needs_approval"):
            break
        if last_result.get("ok", True):
            break
        if attempt < _MAX_STEP_RETRIES:
            time.sleep(_STEP_RETRY_BACKOFF_BASE ** attempt)

    last_result["attempts"] = attempt + 1
    return last_result


def _last_result_text(plan: Plan) -> str:
    """Extract the most useful string from prior completed steps."""
    for step in reversed(plan.steps):
        if step.status != "done" or not step.result:
            continue
        r = step.result
        for key in ("stdout", "content", "text", "answer", "message"):
            val = r.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
        if isinstance(r.get("output"), str) and r["output"].strip():
            return r["output"].strip()
    return ""


def _dummy_create_pending(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Approval stubs are not surfaced in the plan executor; agent.run steps run unsupervised."""
    return {"id": "plan-approval-dummy"}


def _model_for_provider(provider: str | None = None) -> str:
    """Pick the default model for the configured (or supplied) provider."""
    provider = provider or settings.ai_provider
    return {
        "ollama": settings.ollama_model,
        "openai": settings.openai_model,
        "google": settings.gemini_model,
        "anthropic": settings.anthropic_model,
        "huggingface": settings.huggingface_model,
    }.get(provider, settings.ollama_model)


async def _run_agent_loop_async(description: str, plan: Plan) -> dict[str, Any]:
    """Run one agent-loop turn for an agent.run plan step."""
    from . import agent_loop

    events: list[dict[str, Any]] = []
    async for event in agent_loop.run_agent_loop(
        message=description,
        messages=[{"role": "user", "content": description}],
        model=_model_for_provider(),
        provider=settings.ai_provider,
        session_id=plan.plan_id,
        create_pending=_dummy_create_pending,
        max_steps=4,
    ):
        events.append(event)

    final: dict[str, Any] = {}
    for ev in reversed(events):
        if "__final__" in ev:
            final = ev["__final__"]
            break

    return {
        "ok": True,
        "answer": final.get("answer", ""),
        "tools_used": final.get("tools_used", []),
        "jobs": final.get("jobs", []),
    }


def _run_agent_loop_step(description: str, plan: Plan) -> dict[str, Any]:
    """Synchronous wrapper around the async agent-loop generator."""
    try:
        return asyncio.run(_run_agent_loop_async(description, plan))
    except Exception as exc:
        return {"ok": False, "error": f"agent.run failed: {exc}"[:500]}


def _infer_tool_args(tool_name: str, description: str, plan: Plan) -> dict[str, Any]:
    """Naive but useful argument extraction for common tools."""
    lower = description.lower()
    if tool_name == "shell.run":
        return {"command": description}
    if tool_name == "web.search":
        return {"query": description}
    if tool_name == "web.fetch":
        # Try to find a URL
        import re
        urls = re.findall(r"https?://\S+", description)
        return {"url": urls[0] if urls else description}
    if tool_name == "fs.read":
        return {"path": description.split()[-1]}
    if tool_name == "fs.write":
        # Look for "save X to/as Y" pattern; if X looks like a placeholder (the result, it, etc.), use prior step output
        import re
        from .config import STORAGE_DIR
        m = re.search(r"save\s+(.+?)\s+(?:to|as)\s+(\S+)", lower)
        if m:
            path = m.group(2).strip()
            content_guess = m.group(1).strip()
            placeholder_words = {"the", "result", "it", "this", "that", "output", "previous"}
            is_placeholder = all(w in placeholder_words for w in content_guess.split()) or len(content_guess.split()) <= 2
            content = _last_result_text(plan) if is_placeholder else content_guess
        else:
            path = description.split()[-1]
            content = _last_result_text(plan) or description
        # Force output into an allowed scratch directory
        if not path.startswith((str(STORAGE_DIR), str(STORAGE_DIR.parent / "workspace"))):
            path = str(STORAGE_DIR / "plan_outputs" / plan.plan_id / path.lstrip("/\\"))
        return {"path": path, "content": content}
    if tool_name == "desktop.interpreter":
        # If description is natural language, wrap it as a comment and print a note
        # so the agent can later replace this step with real code.
        looks_like_code = any(k in description for k in ("def ", "import ", "print(", "return ", "for ", "if ", "=", "[", "]", "{", "}"))
        if looks_like_code:
            return {"code": description}
        return {"code": f"# {description}\nprint('Step waiting for actual Python code from agent')"}
    if tool_name == "memory.save":
        return {"content": description, "tags": ["plan", plan.plan_id]}
    if tool_name in {"plan.create", "plan.list", "plan.get", "plan.cancel"}:
        return {"goal": description}
    return {"query": description, "goal": description}


def _run_via_agent_router(description: str, plan: Plan) -> dict[str, Any]:
    """Lightweight router: pick one deterministic tool from the description.

    This avoids spawning a full async agent loop from a sync executor thread.
    It is intentionally conservative: if uncertain, it saves the step to memory
    and asks for clarification.
    """
    lower = description.lower()
    # Search intent
    if any(w in lower for w in {"search", "find", "look up", "google", "lookup"}):
        q = description.split("for")[-1].split("about")[-1].strip(" ?")
        return agent_tools.run_tool("web.search", {"query": q or description}, allow_gated=False)
    # Read intent
    if any(w in lower for w in {"read file", "open file", "show file", "cat "}):
        path = description.split()[-1]
        return agent_tools.run_tool("fs.read", {"path": path}, allow_gated=False)
    # Write intent
    if any(w in lower for w in {"write file", "save file", "create file"}):
        parts = description.split(" as ", 1)
        path = parts[-1].strip() if len(parts) > 1 else "plan_output.txt"
        return agent_tools.run_tool("fs.write", {"path": path, "content": parts[0].strip()}, allow_gated=False)
    # Code intent
    if any(w in lower for w in {"calculate", "plot ", "chart", "analyze data", "run python"}):
        return agent_tools.run_tool("desktop.interpreter", {"code": description}, allow_gated=False)
    # Shell intent
    if any(w in lower for w in {"run command", "execute ", "shell "}):
        cmd = description.split("run")[-1].split("execute")[-1].strip(" :")
        return agent_tools.run_tool("shell.run", {"command": cmd or description}, allow_gated=False)
    # Memory intent
    if any(w in lower for w in {"remember", "save that", "note that"}):
        return agent_tools.run_tool("memory.save", {"content": description, "tags": ["plan", plan.plan_id]}, allow_gated=False)
    # Default: save as memory and return a clarifying note
    agent_tools.run_tool("memory.save", {"content": f"Plan step needed clarification: {description}", "tags": ["plan", "clarify", plan.plan_id]}, allow_gated=False)
    return {"ok": True, "note": "Step saved to memory; no direct action taken. Describe the tool/action more explicitly for automatic execution."}


def run_plan_wave(plan_id: str) -> dict[str, Any]:
    """Public entry point: run the next ready wave of a plan."""
    return execute_plan_wave(plan_id, _step_executor)


def run_plan_to_completion(plan_id: str, max_waves: int = 20) -> dict[str, Any]:
    """Run waves until the plan is terminal or max_waves reached.

    This is a synchronous, blocking helper — use it from background threads or
    scheduled tasks. Chat streaming should call `run_plan_wave` per wave and
    yield progress events.
    """
    for _ in range(max_waves):
        result = run_plan_wave(plan_id)
        plan = result.get("plan", {})
        if plan.get("status") in {"completed", "failed", "cancelled"}:
            break
    return result
