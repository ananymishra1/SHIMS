"""DuoBot collaborative task/project runner.

Creates a Coder v2/v3 project and runs a primary/local collaboration loop:
  plan → implement next file → test → review → repeat.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from pathlib import Path
from typing import Any

from .coder_v2 import create_project
from .coder_v3 import run_shell_command, write_file, read_file, list_files
from .config import STORAGE_DIR
from .inter_instance_bridge import PeerClient, get_peer
from .security import new_id

try:
    from . import ai as ai_module
except Exception:  # pragma: no cover
    ai_module = None

TASKS_DIR = STORAGE_DIR / "duobot" / "tasks"
TASKS_DIR.mkdir(parents=True, exist_ok=True)


def _now() -> float:
    return time.time()


def create_task(conv_id: str, title: str, description: str) -> dict[str, Any]:
    project_name = f"duobot_{title.replace(' ', '_').lower()[:30]}_{new_id('task')[:6]}"
    project = create_project(name=project_name, template="python")
    if not project.get("ok"):
        return project
    task_id = new_id("task")
    task = {
        "id": task_id,
        "conv_id": conv_id,
        "title": title,
        "description": description,
        "project_id": project["project_id"],
        "project_name": project_name,
        "created_at": _now(),
        "status": "created",
        "plan": [],
        "files": [],
        "log": [],
    }
    _save_task(task)
    return {"ok": True, "task": task}


def _task_path(task_id: str) -> Path:
    return TASKS_DIR / f"{task_id}.json"


def _save_task(task: dict[str, Any]) -> None:
    _task_path(task["id"]).write_text(json.dumps(task, indent=2, default=str), encoding="utf-8")


def get_task(task_id: str) -> dict[str, Any] | None:
    path = _task_path(task_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def list_tasks(conv_id: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    for path in sorted(TASKS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            task = json.loads(path.read_text(encoding="utf-8"))
            if conv_id is None or task.get("conv_id") == conv_id:
                tasks.append(task)
        except Exception:
            continue
    return tasks[:limit]


def _flatten_file_list(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    flat: list[dict[str, Any]] = []
    for entry in entries:
        if entry.get("is_dir"):
            flat.extend(_flatten_file_list(entry.get("children", [])))
        else:
            flat.append(entry)
    return flat


def _project_files(project_id: str) -> list[dict[str, Any]]:
    return _flatten_file_list(list_files(project_id, recursive=True))


def _read_file_text(project_id: str, rel_path: str, max_chars: int = 4000) -> str:
    result = read_file(project_id, rel_path)
    text = result.get("content") or ""
    if len(text) > max_chars:
        text = text[:max_chars] + "\n... [truncated]"
    return text


def _append_log(task: dict[str, Any], role: str, step: str, content: str, metadata: dict[str, Any] | None = None) -> None:
    task.setdefault("log", []).append({
        "ts": _now(),
        "role": role,
        "step": step,
        "content": content,
        "metadata": metadata or {},
    })


def _extract_json(text: str) -> Any:
    match = re.search(r"```json\s*(.*?)\s*```", text, re.S)
    if match:
        text = match.group(1)
    match = re.search(r"\{.*\}", text, re.S)
    if match:
        try:
            return json.loads(match.group(0))
        except Exception:
            pass
    try:
        return json.loads(text)
    except Exception:
        return None


def _extract_code_block(text: str) -> str:
    match = re.search(r"```(\w+)?\s*(.*?)\s*```", text, re.S)
    if match:
        return match.group(2).strip()
    return text.strip()


async def _ask_primary(system: str, prompt: str, model: str = "", timeout: float = 60.0) -> str:
    if not ai_module:
        return ""
    try:
        result = await asyncio.wait_for(
            ai_module.ask_ai(prompt, system=system, model=model),
            timeout=timeout,
        )
        return result.text or ""
    except Exception as exc:
        return f"[primary error: {exc!r}]"


async def _ask_local(messages: list[dict[str, str]], model: str = "qwen2.5:3b", timeout: float = 60.0) -> str:
    peer = get_peer("local")
    if not peer:
        return "[local peer not configured]"
    try:
        client = PeerClient(peer)
        result = await asyncio.wait_for(
            client.call_tool("local_llm.chat", {"messages": messages, "model": model}),
            timeout=timeout,
        )
        if not result.get("ok"):
            return f"[local error: {result.get('error', 'unknown')}]"
        return (result.get("result") or {}).get("content", "")
    except Exception as exc:
        return f"[local error: {exc}]"


async def _plan_step(task: dict[str, Any]) -> dict[str, Any]:
    prompt = (
        f"You are the architect. The team needs to build this project:\n\n"
        f"Title: {task['title']}\n"
        f"Description: {task['description']}\n\n"
        f"Constraints:\n"
        f"- Use only the Python standard library (no pytest, no external packages).\n"
        f"- Tests must be written with the built-in `unittest` module.\n"
        f"- Keep the plan small (3-7 files).\n\n"
        f"Return ONLY a JSON object with this schema:\n"
        f'{{"plan": [{{"file": "relative/path.py", "purpose": "...", "depends_on": []}}], '
        f'"entrypoint": "main.py", "test_command": "python -m unittest discover -s . -p test_*.py"}}\n\n'
        f"No extra commentary."
    )
    system = "You are SHIMS Omni (primary architect). Produce concise, actionable plans."
    text = await _ask_primary(system, prompt)
    parsed = _extract_json(text)
    if parsed and isinstance(parsed, dict):
        task["plan"] = parsed.get("plan", [])
        task["entrypoint"] = parsed.get("entrypoint", "")
        task["test_command"] = parsed.get("test_command", "python -m unittest discover -s . -p test_*.py")
        task["status"] = "planned"
        _append_log(task, "primary", "plan", text, {"parsed": bool(task["plan"])})
    else:
        task["status"] = "plan_failed"
        _append_log(task, "primary", "plan", text, {"parsed": False})
    _save_task(task)
    return {"ok": parsed is not None, "task": task}


async def _implement_step(task: dict[str, Any]) -> dict[str, Any]:
    plan = task.get("plan", [])
    files = {f["path"]: f for f in _project_files(task["project_id"])}
    # Find first planned file that does not exist or is empty.
    next_item = None
    for item in plan:
        rel = item.get("file", "")
        existing = files.get(rel)
        if not existing or (existing.get("size", 0) < 10):
            next_item = item
            break
    if not next_item:
        task["status"] = "implemented"
        _save_task(task)
        return {"ok": True, "task": task, "message": "all planned files exist"}

    rel_path = next_item["file"]
    # Build context from already implemented files.
    context_lines = []
    for it in plan:
        rp = it.get("file", "")
        if rp == rel_path:
            continue
        if rp in files:
            context_lines.append(f"--- {rp} ---\n{_read_file_text(task['project_id'], rp, max_chars=2000)}")
    context = "\n\n".join(context_lines)

    is_test = rel_path.startswith(("test_", "tests/"))
    messages = [
        {"role": "system", "content": (
            "You are SHIMS Local Factory (implementer). Write clean, working Python code. "
            "Return the code inside a markdown code block. "
            "Use ONLY the Python standard library. Do NOT use pytest, pytest-mock, or any external package."
        )},
        {"role": "user", "content": (
            f"Project: {task['title']}\n"
            f"Task: {task['description']}\n"
            f"Implement file: {rel_path}\n"
            f"Purpose: {next_item.get('purpose', '')}\n\n"
            f"Existing files context:\n{context}\n\n"
            f"{'This is a test file: use unittest.TestCase and built-in assertions only. ' if is_test else ''}"
            f"Write the full content for {rel_path}."
        )},
    ]
    text = await _ask_local(messages, model=os.getenv("SHIMS_FACTORY_DEFAULT_MODEL", "qwen2.5:3b"))
    code = _extract_code_block(text)
    # If the model prefixed the block with a language label, strip it.
    code = re.sub(r"^(python|py)\n", "", code, flags=re.IGNORECASE).strip()
    write_result = write_file(task["project_id"], rel_path, code)
    task["files"] = [f["path"] for f in _project_files(task["project_id"])]
    task["status"] = "implementing"
    _append_log(task, "local", "implement", text, {"file": rel_path, "write_ok": write_result.get("ok", False)})
    _save_task(task)
    return {"ok": write_result.get("ok", False), "task": task, "implemented_file": rel_path}


async def _test_step(task: dict[str, Any]) -> dict[str, Any]:
    project_id = task["project_id"]
    cmd = task.get("test_command", "python -m unittest discover -s . -p test_*.py")

    # Normalize legacy pytest defaults to the standard-library runner.
    if cmd in ("pytest", "python -m pytest -q"):
        cmd = "python -m unittest discover -s . -p test_*.py -v"

    result = run_shell_command(project_id, cmd, timeout=120)
    output = result.get("stdout", "") + "\n" + result.get("stderr", "")

    # If no tests were discovered, try the entrypoint as a smoke test.
    if (result.get("ok", False) and "Ran 0 tests" in output) or (not result.get("ok", False) and "Ran 0 tests" in output):
        entrypoint = task.get("entrypoint", "")
        if entrypoint:
            result = run_shell_command(project_id, f"python {entrypoint}", timeout=60)
            output = result.get("stdout", "") + "\n" + result.get("stderr", "")

    task["last_test"] = {
        "ok": result.get("ok", False) and result.get("returncode", -1) == 0,
        "returncode": result.get("returncode", -1),
        "output": output[:2000],
    }
    task["status"] = "tested"
    _append_log(task, "system", "test", output[:1000], task["last_test"])
    _save_task(task)
    return {"ok": True, "task": task, "test": task["last_test"]}


async def _apply_fix_step(task: dict[str, Any]) -> dict[str, Any]:
    fix = task.pop("pending_fix", None)
    if not fix or not fix.get("fix_file"):
        task["status"] = "implementing"
        _save_task(task)
        return {"ok": False, "task": task, "error": "invalid fix instruction"}

    rel_path = fix["fix_file"]
    instructions = fix.get("instructions", "")
    current = _read_file_text(task["project_id"], rel_path, max_chars=4000)

    messages = [
        {"role": "system", "content": (
            "You are SHIMS Local Factory (implementer). Apply the requested fix. "
            "Return the complete updated file inside a markdown code block. "
            "Use ONLY the Python standard library."
        )},
        {"role": "user", "content": (
            f"Project: {task['title']}\n"
            f"File to fix: {rel_path}\n"
            f"Instructions:\n{instructions}\n\n"
            f"Current content:\n{current}\n\n"
            f"Return the full updated content for {rel_path}."
        )},
    ]
    text = await _ask_local(messages, model=os.getenv("SHIMS_FACTORY_DEFAULT_MODEL", "qwen2.5:3b"))
    code = _extract_code_block(text)
    code = re.sub(r"^(python|py)\n", "", code, flags=re.IGNORECASE).strip()
    write_result = write_file(task["project_id"], rel_path, code)
    task["files"] = [f["path"] for f in _project_files(task["project_id"])]
    task["status"] = "implementing"
    _append_log(task, "local", "fix", text, {"file": rel_path, "write_ok": write_result.get("ok", False)})
    _save_task(task)
    return {"ok": write_result.get("ok", False), "task": task, "fixed_file": rel_path}


async def _review_step(task: dict[str, Any]) -> dict[str, Any]:
    last_test = task.get("last_test", {})
    files_summary = "\n".join(
        f"{p}: {_read_file_text(task['project_id'], p, max_chars=1500)[:300]}..."
        for p in task.get("files", [])
    )
    prompt = (
        f"You are the reviewer. The project status is:\n"
        f"Title: {task['title']}\n"
        f"Plan: {json.dumps(task.get('plan', []))}\n"
        f"Test result: {json.dumps(last_test)}\n\n"
        f"Files:\n{files_summary}\n\n"
        f"If tests passed, reply exactly 'DONE'. "
        f"If not, reply with ONLY a JSON object: "
        f'{{"fix_file": "path.py", "instructions": "what to change"}}.'
    )
    system = "You are SHIMS Omni (reviewer). Be concise."
    text = await _ask_primary(system, prompt)
    task["status"] = "reviewed"
    if text.strip().upper().startswith("DONE"):
        task["status"] = "complete"
        _append_log(task, "primary", "review", "Tests passed; task complete.")
    elif text.startswith("[primary error:"):
        _append_log(task, "primary", "review", text, {"parsed": False, "error": True})
    else:
        parsed = _extract_json(text)
        if parsed and parsed.get("fix_file"):
            task["pending_fix"] = parsed
            _append_log(task, "primary", "review", text, {"fix": parsed})
        else:
            _append_log(task, "primary", "review", text, {"parsed": False})
    _save_task(task)
    return {"ok": True, "task": task}


async def run_collaboration_round(task_id: str) -> dict[str, Any]:
    task = get_task(task_id)
    if not task:
        return {"ok": False, "error": "task not found"}
    status = task.get("status", "created")
    if status == "created" or not task.get("plan"):
        return await _plan_step(task)
    # Apply a pending fix from the reviewer before moving on.
    if task.get("pending_fix"):
        return await _apply_fix_step(task)
    # If there are still missing files, implement next.
    files = {f["path"]: f for f in _project_files(task["project_id"])}
    missing = [it for it in task.get("plan", []) if not files.get(it.get("file", ""), {}).get("size", 0)]
    if missing:
        return await _implement_step(task)
    # If not yet tested this round, test.
    if status in ("implemented", "reviewed", "plan_failed") or not task.get("last_test"):
        return await _test_step(task)
    # Otherwise review and decide next.
    return await _review_step(task)


async def run_collaboration_loop(task_id: str, max_rounds: int = 10) -> dict[str, Any]:
    for i in range(max_rounds):
        result = await run_collaboration_round(task_id)
        task = result.get("task") or get_task(task_id)
        if task and task.get("status") == "complete":
            return {"ok": True, "task": task, "rounds": i + 1}
        if not result.get("ok"):
            return {"ok": False, "error": result.get("error", "round failed"), "task": task}
    return {"ok": True, "task": get_task(task_id), "rounds": max_rounds, "note": "max rounds reached"}
