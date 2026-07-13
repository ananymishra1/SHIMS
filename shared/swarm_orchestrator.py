"""Real meta-orchestrator swarm for SHIMS Omni.

The Orchestrator takes a user prompt, analyzes it, breaks it into sub-tasks,
dispatches specialist agents in parallel waves, and synthesizes a final answer.
Every agent reads from and writes to a shared scratchpad so the whole team stays
aware of the project context.

Specialist agents:
- planner: analyzes the task and emits a dependency-aware plan
- coder: iteratively writes, syntax-checks, runs, tests, and fixes code
- reviewer: reviews code/output for bugs, security, and clarity
- tester: writes and runs additional test cases
- researcher: searches the web or local docs when needed
"""
from __future__ import annotations

import asyncio
import inspect
import json
import os
import py_compile
import re
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

import httpx

from .security import new_id


# --------------------------------------------------------------------------- #
# Events
# --------------------------------------------------------------------------- #

@dataclass
class SwarmEvent:
    agent_id: str
    stage: str
    message: str
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "stage": self.stage,
            "message": self.message,
            "payload": self.payload,
            "timestamp": self.timestamp,
        }


EventEmitter = Callable[[SwarmEvent], None]


def _noop_emit(event: SwarmEvent) -> None:
    pass


# --------------------------------------------------------------------------- #
# Shared scratchpad
# --------------------------------------------------------------------------- #

@dataclass
class SubTask:
    id: str
    agent_type: str
    prompt: str
    dependencies: list[str] = field(default_factory=list)
    project_id: str | None = None
    status: str = "pending"  # pending, running, done, failed
    result: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "agent_type": self.agent_type,
            "prompt": self.prompt,
            "dependencies": self.dependencies,
            "project_id": self.project_id,
            "status": self.status,
            "result": self.result,
        }


@dataclass
class SwarmScratchpad:
    original_prompt: str
    plan: list[SubTask] = field(default_factory=list)
    projects: dict[str, dict[str, Any]] = field(default_factory=dict)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    final_synthesis: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "original_prompt": self.original_prompt,
            "plan": [p.to_dict() for p in self.plan],
            "projects": self.projects,
            "artifacts": self.artifacts,
            "final_synthesis": self.final_synthesis,
        }


# --------------------------------------------------------------------------- #
# LLM helper
# --------------------------------------------------------------------------- #

LLMCaller = Callable[[str, str], Awaitable[dict[str, Any]]]
RawLLMCaller = Callable[[str, str], dict[str, Any] | Awaitable[dict[str, Any]]]


def _ensure_async_llm(llm: RawLLMCaller | None) -> LLMCaller:
    """Normalize an LLM callable so it can always be awaited.

    Accepts either an async callable or a plain sync callable that returns a
    dict. This keeps tests simple while letting production callers stay fully
    async.
    """
    if llm is None:
        return _default_llm_call

    async def _wrapper(system: str, prompt: str) -> dict[str, Any]:
        result = llm(system, prompt)
        if inspect.isawaitable(result):
            return await result
        return result  # type: ignore[return-value]

    return _wrapper


async def _default_llm_call(system: str, prompt: str) -> dict[str, Any]:
    """Best-effort async LLM call to the local Ollama endpoint.

    Tests can monkeypatch this function. Returns {"ok": bool, "text": str,
    "error": str}.
    """
    host = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
    model = os.getenv("SHIMS_ORCHESTRATOR_MODEL", os.getenv("SHIMS_ROUTER_MODEL", "qwen2.5:7b"))
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "stream": False,
        "options": {"temperature": 0.3, "num_predict": 2048},
        "keep_alive": "5m",
    }
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(f"{host}/api/chat", json=payload)
            r.raise_for_status()
            data = r.json()
    except Exception as exc:
        return {"ok": False, "text": "", "error": str(exc)[:200]}
    text = (data.get("message") or {}).get("content") or data.get("response") or ""
    return {"ok": True, "text": text.strip(), "error": ""}


def _role_llm_caller(role: str) -> LLMCaller:
    """Build an async LLM caller for a swarm sub-agent role.

    Resolves the role to a concrete provider/model via ``agent_model_router``
    and talks through ``agent_loop._llm_chat``.
    """
    from . import agent_loop
    from .agent_model_router import resolve_role

    async def _call(system: str, prompt: str) -> dict[str, Any]:
        provider, model, reason = resolve_role(role)
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ]
        try:
            result, success, _, error = await agent_loop._llm_chat(
                provider, model, messages, tools=[], temperature=0.3, timeout=120.0
            )
            if success:
                text = result.get("content") or ""
                tool_calls = result.get("tool_calls") or []
                if tool_calls and not text:
                    text = str(tool_calls[0])[:2000]
                return {"ok": True, "text": text.strip(), "error": "", "provider": provider, "model": model, "reason": reason}
            return {"ok": False, "text": "", "error": error or f"{provider}/{model} failed", "provider": provider, "model": model}
        except Exception as exc:
            return {"ok": False, "text": "", "error": str(exc)[:200], "provider": provider, "model": model}

    return _call


def _extract_json(text: str) -> dict[str, Any] | None:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            parsed = json.loads(text[start : end + 1])
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            return None


# --------------------------------------------------------------------------- #
# Sub-agents
# --------------------------------------------------------------------------- #

class SubAgent:
    def __init__(self, agent_id: str, emit: EventEmitter, llm: LLMCaller | None = None, **kwargs: Any) -> None:
        self.agent_id = agent_id
        self.emit = emit
        self.llm = _ensure_async_llm(llm)

    def _emit(self, stage: str, message: str, payload: dict[str, Any] | None = None) -> None:
        self.emit(SwarmEvent(self.agent_id, stage, message, payload or {}))

    async def run(self, task: SubTask, scratchpad: SwarmScratchpad) -> dict[str, Any]:
        raise NotImplementedError


class PlannerSubAgent(SubAgent):
    """Analyzes the user prompt and produces a structured plan.

    The orchestrator normally calls the planner first. In LLM mode the planner
    prompt asks the model for a JSON plan; in deterministic mode it falls back
    to keyword-based decomposition.
    """

    def __init__(self, agent_id: str, emit: EventEmitter, llm: LLMCaller | None = None) -> None:
        super().__init__(agent_id, emit)
        self.llm = _ensure_async_llm(llm)

    async def run(self, task: SubTask, scratchpad: SwarmScratchpad) -> dict[str, Any]:
        self._emit("analyze", "Analyzing task and building dependency-aware plan")
        prompt = task.prompt
        system = (
            "You are a project planner. Break the user's request into a small list of "
            "concrete sub-tasks. Return ONLY valid JSON with this schema:\n"
            '{"analysis": "<1-2 sentence understanding>", "subtasks": ['
            '{"agent_type": "coder|reviewer|tester|researcher", "prompt": "<specific task>", '
            '"dependencies": []}]}\n'
            "Dependencies are the 0-based indices of earlier subtasks this one needs. "
            "Keep subtasks minimal and executable."
        )
        llm_result = await self.llm(system, prompt)
        plan_data: dict[str, Any] | None = None
        if llm_result.get("ok"):
            plan_data = _extract_json(llm_result.get("text", ""))
        if plan_data and isinstance(plan_data.get("subtasks"), list) and plan_data["subtasks"]:
            subtasks = plan_data["subtasks"]
        else:
            self._emit("analyze", f"LLM planner unavailable ({llm_result.get('error') or 'empty'}); using deterministic fallback")
            subtasks = _deterministic_plan(prompt)

        parsed: list[SubTask] = []
        for i, st in enumerate(subtasks):
            deps = [d for d in st.get("dependencies", []) if isinstance(d, int) and 0 <= d < i]
            parsed.append(
                SubTask(
                    id=new_id("sw-task"),
                    agent_type=str(st.get("agent_type", "coder")),
                    prompt=str(st.get("prompt", "")),
                    dependencies=deps,
                )
            )
        self._emit("plan", f"Created plan with {len(parsed)} subtasks", {"subtasks": [s.to_dict() for s in parsed]})
        return {"ok": True, "subtasks": [s.to_dict() for s in parsed], "analysis": plan_data.get("analysis", "") if plan_data else ""}


class CoderSubAgent(SubAgent):
    """Iterative coding agent: plan → write → syntax-check → run → test → fix.

    The agent creates a Coder v2/v3 project, writes files, runs syntax checks,
    executes the project, runs tests, and rewrites files on failure up to a
    maximum number of iterations. Every step emits an event so the user can
    follow the whole process.
    """

    def __init__(
        self,
        agent_id: str,
        emit: EventEmitter,
        llm: LLMCaller | None = None,
        max_iterations: int = 4,
    ) -> None:
        super().__init__(agent_id, emit)
        self.llm = _ensure_async_llm(llm)
        self.max_iterations = max(max_iterations, 1)

    async def run(self, task: SubTask, scratchpad: SwarmScratchpad) -> dict[str, Any]:
        self._emit("code", f"Starting iterative coding for: {task.prompt[:120]}")
        project = self._create_project(task.prompt)
        if not project.get("ok"):
            self._emit("code", "Project creation failed", project)
            return project
        project_id = project["project_id"]
        task.project_id = project_id
        scratchpad.projects[project_id] = project
        self._emit("code", f"Created project {project_id}", {"project": project})

        # Initial code generation via LLM or deterministic template
        files = await self._generate_initial_files(task.prompt, project_id)
        for path, content in files.items():
            self._write_file(project_id, path, content)

        # Iterative fix loop
        iteration = 0
        final_run: dict[str, Any] = {"ok": False, "stdout": "", "stderr": "No run performed"}
        while iteration < self.max_iterations:
            iteration += 1
            self._emit("code", f"Iteration {iteration}/{self.max_iterations}")

            # Syntax check
            syntax_ok, syntax_errors = self._check_syntax(project_id)
            self._emit("check", f"Syntax check: {'ok' if syntax_ok else 'failed'}", {"errors": syntax_errors})
            if not syntax_ok:
                if iteration >= self.max_iterations:
                    break
                await self._fix_errors(project_id, task.prompt, syntax_errors, stage="syntax")
                continue

            # Run the project
            final_run = self._run_project(project_id)
            self._emit("run", f"Run exit ok={final_run.get('ok')}", {"stdout": final_run.get("stdout", "")[:500], "stderr": final_run.get("stderr", "")[:500]})
            if final_run.get("ok"):
                # Try running tests if any exist
                test_run = self._run_tests(project_id)
                # pytest exit code 5 means no tests were collected; treat as success.
                is_no_tests = test_run.get("returncode") == 5
                self._emit("test", f"Test run ok={test_run.get('ok')}{' (no tests found)' if is_no_tests else ''}", {"stdout": test_run.get("stdout", "")[:500], "stderr": test_run.get("stderr", "")[:500]})
                if test_run.get("ok") or is_no_tests:
                    break
                if iteration >= self.max_iterations:
                    final_run = test_run
                    break
                await self._fix_errors(project_id, task.prompt, test_run.get("stderr", "") or test_run.get("stdout", ""), stage="test")
                continue

            if iteration >= self.max_iterations:
                break
            await self._fix_errors(project_id, task.prompt, final_run.get("stderr", "") or final_run.get("stdout", ""), stage="run")

        # Final file listing
        file_list = self._list_files(project_id)
        result = {
            "ok": final_run.get("ok", False),
            "project_id": project_id,
            "iterations": iteration,
            "files": file_list,
            "run_result": final_run,
            "summary": f"Completed {iteration} iteration(s). Final run ok={final_run.get('ok', False)}.",
        }
        self._emit("code", "Coding complete", {"result": result})
        return result

    def _create_project(self, prompt: str) -> dict[str, Any]:
        from .coder_v2 import create_project
        name = re.sub(r"[^a-zA-Z0-9_]+", "_", prompt[:40]).strip("_") or "swarm_project"
        name = name[:50]
        return create_project(name=name, template=None)

    async def _generate_initial_files(self, prompt: str, project_id: str) -> dict[str, str]:
        """Ask the LLM for an initial file map or fall back to a minimal stub."""
        system = (
            "You are a senior engineer. The user wants a small, self-contained program. "
            "Return ONLY valid JSON: {\"files\": {\"relative/path.py\": \"full source code\"}}. "
            "Prefer a single file named main.py. Include short comments and basic error handling."
        )
        llm_result = await self.llm(system, prompt)
        if llm_result.get("ok"):
            data = _extract_json(llm_result.get("text", ""))
            if data and isinstance(data.get("files"), dict) and data["files"]:
                return {str(k): str(v) for k, v in data["files"].items()}
        self._emit("code", "LLM file generation unavailable; using deterministic stub")
        return _deterministic_code_stub(prompt)

    def _write_file(self, project_id: str, path: str, content: str) -> dict[str, Any]:
        from .coder_v2 import write_file
        self._emit("write", f"Writing {path}", {"bytes": len(content)})
        return write_file(project_id, path, content)

    def _check_syntax(self, project_id: str) -> tuple[bool, list[str]]:
        from .coder_v2 import list_files, read_file
        files = list_files(project_id, recursive=True)
        errors: list[str] = []
        for f in files:
            if not f.get("is_dir") and f.get("path", "").endswith(".py"):
                res = read_file(project_id, f["path"])
                content = res.get("content", "")
                if not content:
                    continue
                try:
                    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as tmp:
                        tmp.write(content)
                        tmp_path = tmp.name
                    py_compile.compile(tmp_path, doraise=True)
                except py_compile.PyCompileError as exc:
                    errors.append(f"{f['path']}: {exc}")
                finally:
                    try:
                        Path(tmp_path).unlink(missing_ok=True)
                    except Exception:
                        pass
        return (not errors), errors

    def _run_project(self, project_id: str) -> dict[str, Any]:
        from .coder_v2 import run_project
        # coder_v2.run_project reads project meta and runs the configured entry file.
        return run_project(project_id)

    def _run_tests(self, project_id: str) -> dict[str, Any]:
        from .coder_v3 import run_tests
        return run_tests(project_id)

    async def _fix_errors(self, project_id: str, task_prompt: str, error_output: str, stage: str) -> None:
        self._emit("fix", f"Fixing {stage} errors", {"error_snippet": str(error_output)[:800]})
        from .coder_v2 import list_files, read_file, write_file
        files = list_files(project_id, recursive=True)
        context = ""
        for f in files:
            if f.get("is_dir"):
                continue
            res = read_file(project_id, f["path"])
            content = res.get("content", "")
            if content:
                context += f"\n--- {f['path']} ---\n{content[:2000]}\n"
        system = (
            "You are a debugging engineer. Given the task, current source files, and error output, "
            "produce corrected files. Return ONLY valid JSON: {\"files\": {\"path\": \"full new content\"}}. "
            "Only include files that need changes."
        )
        prompt = f"Task:\n{task_prompt}\n\nError ({stage}):\n{error_output[:1500]}\n\nCurrent files:\n{context[:4000]}"
        llm_result = await self.llm(system, prompt)
        if llm_result.get("ok"):
            data = _extract_json(llm_result.get("text", ""))
            if data and isinstance(data.get("files"), dict):
                for path, content in data["files"].items():
                    write_file(project_id, path, str(content))
                self._emit("fix", f"Rewrote {len(data['files'])} file(s)")
                return
        self._emit("fix", "LLM fix unavailable; attempting simple retry")

    def _list_files(self, project_id: str) -> list[str]:
        return [f["path"] for f in self._list_files_raw(project_id) if not f.get("is_dir")]

    def _list_files_raw(self, project_id: str) -> list[dict[str, Any]]:
        from .coder_v2 import list_files
        return list_files(project_id, recursive=True)


class ReviewerSubAgent(SubAgent):
    def __init__(self, agent_id: str, emit: EventEmitter, **kwargs: Any) -> None:
        super().__init__(agent_id, emit, **kwargs)

    async def run(self, task: SubTask, scratchpad: SwarmScratchpad) -> dict[str, Any]:
        self._emit("review", "Reviewing outputs")
        # Collect files from dependency coder tasks
        files: list[dict[str, Any]] = []
        for dep_idx in task.dependencies:
            if 0 <= dep_idx < len(scratchpad.plan):
                dep = scratchpad.plan[dep_idx]
                pid = dep.project_id
                if pid:
                    from .coder_v2 import list_files, read_file
                    for f in list_files(pid, recursive=True):
                        if f.get("is_dir"):
                            continue
                        res = read_file(pid, f["path"])
                        files.append({"path": f["path"], "content": res.get("content", "")})
        if not files:
            return {"ok": True, "review": "No code found to review."}
        context = "\n".join(f"--- {f['path']} ---\n{f['content'][:1500]}" for f in files)
        system = (
            "You are a code reviewer. Review the code for bugs, security risks, and unclear "
            "requirements. Return a concise bulleted review."
        )
        llm_result = await self.llm(system, f"Task: {task.prompt}\n\nCode:\n{context}")
        review = llm_result.get("text") or "Review unavailable."
        self._emit("review", "Review complete", {"review": review[:1000]})
        return {"ok": True, "review": review}


class TesterSubAgent(SubAgent):
    def __init__(self, agent_id: str, emit: EventEmitter, **kwargs: Any) -> None:
        super().__init__(agent_id, emit, **kwargs)

    async def run(self, task: SubTask, scratchpad: SwarmScratchpad) -> dict[str, Any]:
        self._emit("test", "Running focused tests")
        # Reuse the coder project if available
        project_id: str | None = None
        for dep_idx in task.dependencies:
            if 0 <= dep_idx < len(scratchpad.plan):
                project_id = scratchpad.plan[dep_idx].project_id
                if project_id:
                    break
        if not project_id:
            return {"ok": True, "test_output": "No project to test."}
        from .coder_v3 import run_tests
        result = run_tests(project_id)
        self._emit("test", f"Tests ok={result.get('ok')}", {"result": result})
        return {"ok": result.get("ok", False), "test_output": result}


class ResearcherSubAgent(SubAgent):
    def __init__(self, agent_id: str, emit: EventEmitter, **kwargs: Any) -> None:
        super().__init__(agent_id, emit, **kwargs)

    async def run(self, task: SubTask, scratchpad: SwarmScratchpad) -> dict[str, Any]:
        self._emit("research", "Searching for context")
        # Try web search via existing agent tool
        try:
            from .agent_tools import run_tool
            search_result = run_tool("web.search", {"query": task.prompt, "limit": 3}, allow_gated=False)
            self._emit("research", "Search complete", {"result": search_result})
            return {"ok": True, "research": search_result}
        except Exception as exc:
            return {"ok": False, "research": f"Research unavailable: {exc}"}


AGENT_REGISTRY: dict[str, type[SubAgent]] = {
    "planner": PlannerSubAgent,
    "coder": CoderSubAgent,
    "reviewer": ReviewerSubAgent,
    "tester": TesterSubAgent,
    "researcher": ResearcherSubAgent,
}


# --------------------------------------------------------------------------- #
# Orchestrator
# --------------------------------------------------------------------------- #

class Orchestrator:
    """Meta-agent: analyze, plan, dispatch, observe, synthesize."""

    def __init__(
        self,
        emit: EventEmitter | None = None,
        llm: LLMCaller | None = None,
        max_workers: int = 4,
    ) -> None:
        self.emit = emit or _noop_emit
        self.llm = _ensure_async_llm(llm)
        self.max_workers = max_workers

    def _emit(self, agent_id: str, stage: str, message: str, payload: dict[str, Any] | None = None) -> None:
        self.emit(SwarmEvent(agent_id, stage, message, payload or {}))

    async def execute(self, prompt: str, use_llm: bool = True) -> dict[str, Any]:
        started = time.perf_counter()
        scratchpad = SwarmScratchpad(original_prompt=prompt)
        self._emit("orchestrator", "start", "Starting swarm execution", {"prompt": prompt})

        # When running without an LLM, use a fast-failing LLM so agents don't wait
        # for an Ollama timeout before falling back to deterministic behavior.
        async def _fast_failing_llm(_system: str, _prompt: str) -> dict:
            return {"ok": False, "text": "", "error": "llm-disabled"}

        active_llm = self.llm if use_llm else _fast_failing_llm
        self._active_llm = active_llm

        # --- Phase 1: plan --------------------------------------------------
        planner = PlannerSubAgent("planner", self.emit, llm=active_llm)
        plan_result = await planner.run(
            SubTask(id=new_id("sw-task"), agent_type="planner", prompt=prompt),
            scratchpad,
        )
        if not plan_result.get("ok"):
            return {"ok": False, "error": "planning failed", "details": plan_result}

        raw_subtasks = plan_result.get("subtasks", [])
        scratchpad.plan = [SubTask(**st) for st in raw_subtasks]
        if not scratchpad.plan:
            # Fallback single coder task
            scratchpad.plan = [SubTask(id=new_id("sw-task"), agent_type="coder", prompt=prompt)]

        self._emit("orchestrator", "plan_ready", f"Plan ready: {len(scratchpad.plan)} subtasks", {"plan": [p.to_dict() for p in scratchpad.plan]})

        # --- Phase 2: execute in dependency waves ----------------------------
        completed_ids: set[str] = set()
        remaining = [t for t in scratchpad.plan]
        while remaining:
            ready = [
                t for t in remaining
                if all(scratchpad.plan[d].status == "done" for d in t.dependencies)
            ]
            if not ready:
                # Cyclic dependency or stuck; run the first remaining anyway
                ready = [remaining[0]]

            for t in ready:
                t.status = "running"
            self._emit("orchestrator", "wave_start", f"Dispatching wave with {len(ready)} agent(s)", {"ready": [t.id for t in ready]})

            # Run ready tasks concurrently
            coros = [self._run_subtask(t, scratchpad) for t in ready]
            await asyncio.gather(*coros)

            for t in ready:
                completed_ids.add(t.id)
                remaining.remove(t)

        # --- Phase 3: synthesize --------------------------------------------
        synthesis = self._synthesize(scratchpad)
        scratchpad.final_synthesis = synthesis
        elapsed_ms = (time.perf_counter() - started) * 1000
        self._emit("orchestrator", "done", "Swarm execution complete", {"elapsed_ms": elapsed_ms})
        return {
            "ok": True,
            "analysis": plan_result.get("analysis", ""),
            "synthesis": synthesis,
            "scratchpad": scratchpad.to_dict(),
            "elapsed_ms": elapsed_ms,
        }

    async def _run_subtask(self, task: SubTask, scratchpad: SwarmScratchpad) -> None:
        agent_cls = AGENT_REGISTRY.get(task.agent_type, CoderSubAgent)
        role_map = {
            "planner": "router",
            "coder": "coder",
            "reviewer": "smart",
            "tester": "coder",
            "researcher": "research",
        }
        role = role_map.get(task.agent_type, "smart")
        # Use the role-specific LLM unless the caller supplied an explicit one.
        active_llm = getattr(self, "_active_llm", None)
        llm = active_llm if active_llm else _role_llm_caller(role)
        agent = agent_cls(task.agent_type, self.emit, llm=llm)
        from .agent_model_router import resolve_role
        provider, model, reason = resolve_role(role)
        self._emit(task.agent_type, "start", f"{task.agent_type} agent starting", {"task_id": task.id, "provider": provider, "model": model, "reason": reason})
        try:
            result = await agent.run(task, scratchpad)
            task.result = result
            task.status = "done" if result.get("ok") else "failed"
            self._emit(task.agent_type, "done", f"{task.agent_type} agent finished", {"task_id": task.id, "provider": provider, "model": model, "ok": result.get("ok", False)})
        except Exception as exc:
            task.result = {"ok": False, "error": str(exc)[:300]}
            task.status = "failed"
            self._emit(task.agent_type, "error", f"Agent failed: {exc}", {"task_id": task.id, "provider": provider, "model": model})

    def _synthesize(self, scratchpad: SwarmScratchpad) -> str:
        parts = ["## Swarm synthesis\n"]
        parts.append(f"**Original request:** {scratchpad.original_prompt}\n")

        # Code project summary
        code_tasks = [t for t in scratchpad.plan if t.agent_type == "coder"]
        if code_tasks:
            parts.append("### Code produced\n")
            for t in code_tasks:
                pid = t.project_id
                res = t.result
                if pid:
                    parts.append(f"- Project `{pid}` — {res.get('summary', '')}")
                    files = res.get("files", [])
                    if files:
                        parts.append(f"- Files: {', '.join(files[:8])}")
                else:
                    parts.append(f"- Coder task {t.id}: {res.get('error', 'no output')}")
            parts.append("")

        # Review summary
        review_tasks = [t for t in scratchpad.plan if t.agent_type == "reviewer"]
        for rt in review_tasks:
            review = rt.result.get("review", "")
            if review:
                parts.append("### Review notes\n")
                parts.append(review[:1500])
                parts.append("")

        # Test summary
        test_tasks = [t for t in scratchpad.plan if t.agent_type == "tester"]
        for tt in test_tasks:
            out = tt.result.get("test_output", {})
            if out:
                parts.append("### Test results\n")
                parts.append(f"ok={out.get('ok')}; stdout/stderr captured in event log.")
                parts.append("")

        # Research summary
        research_tasks = [t for t in scratchpad.plan if t.agent_type == "researcher"]
        for rt in research_tasks:
            research = rt.result.get("research", {})
            if research:
                parts.append("### Research\n")
                results = research.get("results", [])
                for r in results[:3]:
                    parts.append(f"- {r.get('title', 'result')}: {r.get('url', '')}")
                parts.append("")

        parts.append("_Swarm execution finished. Expand the agent activity log to see every step._")
        return "\n".join(parts)


# --------------------------------------------------------------------------- #
# Deterministic fallbacks
# --------------------------------------------------------------------------- #

def _deterministic_plan(prompt: str) -> list[dict[str, Any]]:
    lowered = prompt.lower()
    plan: list[dict[str, Any]] = [
        {"agent_type": "coder", "prompt": f"Implement a minimal working solution for: {prompt}", "dependencies": []},
        {"agent_type": "reviewer", "prompt": "Review the generated code for bugs and improvements.", "dependencies": [0]},
        {"agent_type": "tester", "prompt": "Run tests against the generated code.", "dependencies": [0]},
    ]
    if any(k in lowered for k in ("api", "library", "package", "tool")):
        plan.insert(0, {"agent_type": "researcher", "prompt": f"Search for relevant libraries and examples for: {prompt}", "dependencies": []})
        for i in range(1, len(plan)):
            plan[i]["dependencies"].append(0)
    return plan


def _deterministic_code_stub(prompt: str) -> dict[str, str]:
    lowered = prompt.lower()
    if any(k in lowered for k in ("tracker", "log", "record")):
        return {
            "main.py": (
                "from __future__ import annotations\n"
                "import json\n"
                "from datetime import datetime, timezone\n"
                "from pathlib import Path\n\n"
                "DATA_FILE = Path('updates.jsonl')\n\n"
                "def add_update(source: str, headline: str, notes: str = '') -> dict:\n"
                "    entry = {'timestamp': datetime.now(timezone.utc).isoformat(), 'source': source, 'headline': headline, 'notes': notes}\n"
                "    with DATA_FILE.open('a', encoding='utf-8') as f:\n"
                "        f.write(json.dumps(entry) + '\\n')\n"
                "    return entry\n\n"
                "def list_updates(limit: int = 10) -> list:\n"
                "    if not DATA_FILE.exists():\n"
                "        return []\n"
                "    lines = DATA_FILE.read_text(encoding='utf-8').strip().splitlines()\n"
                "    return [json.loads(line) for line in reversed(lines[-limit:])]\n\n"
                "if __name__ == '__main__':\n"
                "    add_update('FDA', 'New oncology guidance published')\n"
                "    for u in list_updates():\n"
                "        print(f\"{u['timestamp']} | {u['source']}: {u['headline']}\")\n"
            )
        }
    return {
        "main.py": (
            "from __future__ import annotations\n\n"
            "def main():\n"
            "    print('Hello from SHIMS swarm coder!')\n\n"
            "if __name__ == '__main__':\n"
            "    main()\n"
        )
    }


# --------------------------------------------------------------------------- #
# Public helper
# --------------------------------------------------------------------------- #

async def run_orchestrated_swarm(
    prompt: str,
    *,
    emit: EventEmitter | None = None,
    use_llm: bool = True,
) -> dict[str, Any]:
    """Convenience entry point used by the agent tool and API endpoint."""
    orchestrator = Orchestrator(emit=emit, llm=_default_llm_call if use_llm else None)
    return await orchestrator.execute(prompt, use_llm=use_llm)
