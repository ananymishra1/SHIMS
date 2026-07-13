"""Dynamic Skill Runtime — turn learned skills into executable tools.

Skills used to be plain text memories. The runtime adds three executable
flavors:

* ``runtime='text'`` (default) — injected into prompts as before.
* ``runtime='tool'`` — registers a new agent tool dynamically.
* ``runtime='python'`` — runs a sandboxed Python snippet and returns a result.
* ``runtime='jinja'`` — renders a template into a prompt fragment.

All executable skills are stored as JSON sidecars under ``storage/skills/``
alongside regular skills. Registration happens at import time and after any
skill is saved.
"""
from __future__ import annotations

import ast
import json
import signal
import time
from pathlib import Path
from typing import Any, Callable

from . import agent_tools
from .config import STORAGE_DIR
from .skills import SKILLS_DIR, get_skill, list_skills

RUNTIME_WHITELIST = {"text", "tool", "python", "jinja"}

# Restricted builtins for python-runtime skills.
_SAFE_BUILTINS: dict[str, Any] = {
    "abs": abs,
    "all": all,
    "any": any,
    "dict": dict,
    "enumerate": enumerate,
    "filter": filter,
    "float": float,
    "int": int,
    "len": len,
    "list": list,
    "map": map,
    "max": max,
    "min": min,
    "range": range,
    "round": round,
    "set": set,
    "sorted": sorted,
    "str": str,
    "sum": sum,
    "tuple": tuple,
    "zip": zip,
    "json": json,
    "time": time,
}


class SkillRuntimeError(Exception):
    pass


def _is_safe_ast(source: str) -> tuple[bool, str]:
    """Reject imports, class definitions, and anything that looks dangerous."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return False, f"syntax error: {exc}"
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            return False, "imports are not allowed inside skill code"
        if isinstance(node, ast.ClassDef):
            return False, "class definitions are not allowed inside skill code"
        if isinstance(node, ast.FunctionDef) and node.name != "run":
            return False, "only a top-level 'run' function is allowed"
        if isinstance(
            node,
            tuple(
                t
                for t in [
                    getattr(ast, "Delete", None),
                    getattr(ast, "Exec", None),
                    getattr(ast, "Global", None),
                    getattr(ast, "Nonlocal", None),
                    getattr(ast, "Raise", None),
                    getattr(ast, "Try", None),
                    getattr(ast, "With", None),
                ]
                if t is not None
            ),
        ):
            # Allow With in the future for resource managers; ban for now.
            return False, f"{type(node).__name__} is not allowed inside skill code"
    return True, ""


def _sandbox_exec(source: str, args: dict[str, Any], timeout: float = 5.0) -> dict[str, Any]:
    """Run a python-runtime skill in a restricted namespace with a timeout."""
    safe, reason = _is_safe_ast(source)
    if not safe:
        return {"ok": False, "error": reason}

    namespace: dict[str, Any] = {"__builtins__": _SAFE_BUILTINS}
    try:
        exec(compile(source, "<skill>", "exec"), namespace)
    except Exception as exc:
        return {"ok": False, "error": f"compile failed: {exc}"}

    run_fn = namespace.get("run")
    if not callable(run_fn):
        return {"ok": False, "error": "skill code must define a 'run(args)' function"}

    # Soft timeout using signal is Unix-only; Windows ignores SIGALRM.
    # We use a simple time-based guard for Windows and signal for Unix.
    start = time.time()
    try:
        if hasattr(signal, "SIGALRM"):
            def _timeout_handler(_s, _f):
                raise SkillRuntimeError("timeout")
            old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
            signal.alarm(int(timeout))
        result = run_fn(dict(args))
        if hasattr(signal, "SIGALRM"):
            signal.alarm(0)
            signal.signal(signal.SIGALRM, old_handler)
        if not isinstance(result, dict):
            return {"ok": False, "error": "run() must return a dict"}
        return result
    except SkillRuntimeError as exc:
        return {"ok": False, "error": f"skill timed out after {timeout}s: {exc}"}
    except Exception as exc:
        return {"ok": False, "error": f"skill execution failed: {exc}"}
    finally:
        elapsed = time.time() - start
        if elapsed > timeout:
            return {"ok": False, "error": f"skill exceeded {timeout}s time budget"}


def _build_tool_runner(skill_id: str, source: str) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Factory that returns a tool.run closure for a dynamic skill."""

    def _run(args: dict[str, Any]) -> dict[str, Any]:
        return _sandbox_exec(source, args or {})

    return _run


def _register_skill_tool(skill: dict[str, Any]) -> dict[str, Any]:
    """Register one skill as a dynamic agent tool."""
    schema = skill.get("tool_schema") or {}
    name = str(schema.get("name") or skill.get("tool_name") or "").strip()
    if not name:
        return {"ok": False, "error": "tool skill missing 'tool_schema.name'"}
    description = str(schema.get("description") or skill.get("summary") or "").strip()
    parameters = schema.get("parameters") or {"type": "object", "properties": {}}
    source = str(skill.get("tool_code") or skill.get("body") or "").strip()
    if not source:
        return {"ok": False, "error": "tool skill missing 'tool_code'"}

    safe, reason = _is_safe_ast(source)
    if not safe:
        return {"ok": False, "error": reason}

    # Unregister any previous version with the same name.
    if name in agent_tools.TOOLS:
        agent_tools.TOOLS.pop(name, None)

    run_fn = _build_tool_runner(skill.get("id", "unknown"), source)
    agent_tools.register_ephemeral_tool(name, description, run_fn)
    return {"ok": True, "tool": name}


def register_all_skill_tools() -> dict[str, Any]:
    """Scan every skill and register the ones marked as runtime='tool'."""
    registered: list[str] = []
    errors: list[dict[str, Any]] = []
    for skill in list_skills(limit=500):
        if skill.get("runtime") != "tool":
            continue
        result = _register_skill_tool(skill)
        if result.get("ok"):
            registered.append(result["tool"])
        else:
            errors.append({"skill": skill.get("id"), "error": result.get("error")})
    return {"ok": True, "registered": registered, "errors": errors}


def execute_skill(skill_id: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    """Run a skill by ID according to its runtime type."""
    skill = get_skill(skill_id)
    if not skill:
        return {"ok": False, "error": f"skill not found: {skill_id}"}

    runtime = skill.get("runtime") or "text"
    if runtime not in RUNTIME_WHITELIST:
        return {"ok": False, "error": f"unknown runtime: {runtime}"}

    if runtime == "text":
        body = skill.get("body") or skill.get("summary") or ""
        return {"ok": True, "rendered": body, "type": "text"}

    if runtime == "jinja":
        try:
            from jinja2 import Template
        except Exception as exc:
            return {"ok": False, "error": f"jinja2 not available: {exc}"}
        body = skill.get("body") or skill.get("summary") or ""
        try:
            rendered = Template(body).render(args or {})
        except Exception as exc:
            return {"ok": False, "error": f"template render failed: {exc}"}
        return {"ok": True, "rendered": rendered, "type": "jinja"}

    if runtime == "python":
        source = skill.get("body") or ""
        if not source:
            return {"ok": False, "error": "python skill missing body"}
        return _sandbox_exec(source, args or {})

    if runtime == "tool":
        if skill.get("tool_name") not in agent_tools.TOOLS:
            _register_skill_tool(skill)
        tool_name = skill.get("tool_name") or skill.get("tool_schema", {}).get("name")
        if not tool_name or tool_name not in agent_tools.TOOLS:
            return {"ok": False, "error": f"tool skill could not register: {tool_name}"}
        return agent_tools.run_tool(tool_name, args or {}, allow_gated=False)

    return {"ok": False, "error": "unreachable"}


def skill_prompt_block(query: str, limit: int = 3) -> str:
    """Build a prompt fragment with relevant text skills and dynamic tool list."""
    from .skills import relevant_skills
    skills = relevant_skills(query, limit=limit)
    lines: list[str] = []
    if skills:
        lines.append("Learned skills / user preferences to apply:")
        for s in skills:
            prefix = "[tool]" if s.get("runtime") == "tool" else "[memo]"
            lines.append(f"- {prefix} {s.get('name')}: {s.get('summary', '')}")
    dynamic = [n for n, t in agent_tools.TOOLS.items() if n.startswith("skill.")]
    if dynamic:
        lines.append("Dynamic tools available from learned skills:")
        for n in dynamic[:10]:
            t = agent_tools.TOOLS[n]
            lines.append(f"- {n}: {t.description[:120]}")
    return "\n".join(lines)


# Auto-register on module import so restarted processes pick up skill tools.
register_all_skill_tools()
