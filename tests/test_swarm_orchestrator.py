"""Tests for the real meta-orchestrator swarm.

The coder backend (create_project, write_file, run_project, run_tests) is mocked
for most tests so the suite stays fast and does not require subprocess calls.
One integration test exercises the real coder_v2 backend end-to-end.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from shared import swarm_orchestrator as so
from shared.swarm_orchestrator import (
    CoderSubAgent,
    Orchestrator,
    PlannerSubAgent,
    ReviewerSubAgent,
    SubTask,
    SwarmEvent,
    SwarmScratchpad,
)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _noop_emit(event: SwarmEvent) -> None:
    pass


def _failing_llm(_system: str, _prompt: str) -> dict:
    return {"ok": False, "text": "", "error": "mock-llm-down"}


def _planner_llm(_system: str, _prompt: str) -> dict:
    return {
        "ok": True,
        "text": (
            '{"analysis": "Build a small tracker", "subtasks": ['
            '{"agent_type": "coder", "prompt": "Create a daily tracker in Python", "dependencies": []},'
            '{"agent_type": "reviewer", "prompt": "Review the tracker", "dependencies": [0]}'
            ']}'
        ),
        "error": "",
    }


def _coder_llm(system: str, prompt: str) -> dict:
    if "debug" in system.lower() or "error" in prompt.lower():
        return {"ok": True, "text": '{"files": {"main.py": "print(\"fixed\")"}}', "error": ""}
    return {
        "ok": True,
        "text": '{"files": {"main.py": "def main():\\n    print(\"hello\")\\nif __name__ == \"__main__\":\\n    main()\\n"}}',
        "error": "",
    }


def _mock_coder_backend(monkeypatch, run_ok: bool = True, test_rc: int = 5) -> None:
    """Patch coder_v2/v3 functions so no real subprocesses run."""
    import shared.coder_v2 as cv2
    import shared.coder_v3 as cv3

    state = {"files": {"main.py": 'def main():\n    print("hello")\n'}, "run_ok": run_ok}

    def _create_project(name: str, template: str | None = None):
        return {"ok": True, "project_id": "mockproj", "name": name, "language": "python", "entry_file": "main.py"}

    def _write_file(project_id: str, file_path: str, content: str):
        state["files"][file_path] = content
        return {"ok": True}

    def _read_file(project_id: str, file_path: str):
        return {"ok": True, "path": file_path, "content": state["files"].get(file_path, ""), "size": len(state["files"].get(file_path, ""))}

    def _list_files(project_id: str, subdir: str = "", *, recursive: bool = False):
        return [{"name": Path(p).name, "path": p, "is_dir": False, "size": len(c), "modified": "2026-01-01T00:00:00"} for p, c in state["files"].items()]

    def _run_project(project_id: str, entry_file: str | None = None):
        return {"ok": state["run_ok"], "returncode": 0 if state["run_ok"] else 1, "stdout": "hello\n", "stderr": "", "command": "python main.py"}

    def _run_tests(project_id: str, target: str | None = None):
        return {"ok": test_rc == 0, "returncode": test_rc, "stdout": "", "stderr": ""}

    monkeypatch.setattr(cv2, "create_project", _create_project)
    monkeypatch.setattr(cv2, "write_file", _write_file)
    monkeypatch.setattr(cv2, "read_file", _read_file)
    monkeypatch.setattr(cv2, "list_files", _list_files)
    monkeypatch.setattr(cv2, "run_project", _run_project)
    monkeypatch.setattr(cv3, "run_tests", _run_tests)
    return state


# --------------------------------------------------------------------------- #
# Planner
# --------------------------------------------------------------------------- #

@pytest.mark.anyio
async def test_planner_parses_llm_json_plan():
    planner = PlannerSubAgent("planner", _noop_emit, llm=_planner_llm)
    result = await planner.run(SubTask(id="t1", agent_type="planner", prompt="build a tracker"), SwarmScratchpad(""))
    assert result["ok"] is True
    assert len(result["subtasks"]) == 2
    assert result["subtasks"][0]["agent_type"] == "coder"
    assert result["subtasks"][1]["agent_type"] == "reviewer"
    assert result["subtasks"][1]["dependencies"] == [0]


@pytest.mark.anyio
async def test_planner_falls_back_to_deterministic_plan():
    planner = PlannerSubAgent("planner", _noop_emit, llm=_failing_llm)
    result = await planner.run(SubTask(id="t1", agent_type="planner", prompt="build a tracker"), SwarmScratchpad(""))
    assert result["ok"] is True
    assert len(result["subtasks"]) >= 2
    assert result["subtasks"][0]["agent_type"] == "coder"


# --------------------------------------------------------------------------- #
# Coder agent (mocked backend)
# --------------------------------------------------------------------------- #

@pytest.mark.anyio
async def test_coder_agent_creates_project_and_runs(monkeypatch):
    _mock_coder_backend(monkeypatch)
    events: list[SwarmEvent] = []
    coder = CoderSubAgent("coder", events.append, llm=_coder_llm, max_iterations=2)
    task = SubTask(id="t1", agent_type="coder", prompt="Create a hello world script")
    result = await coder.run(task, SwarmScratchpad(""))
    assert result["ok"] is True, result
    assert result["project_id"] == "mockproj"
    assert "main.py" in result["files"]
    assert result["iterations"] >= 1


@pytest.mark.anyio
async def test_coder_agent_fixes_syntax_errors(monkeypatch):
    state = _mock_coder_backend(monkeypatch, run_ok=False)
    bad_llm = lambda _s, _p: {"ok": True, "text": '{"files": {"main.py": "print(\\"hello\\""}}', "error": ""}  # noqa: E731
    fix_llm = lambda _s, _p: {"ok": True, "text": '{"files": {"main.py": "print(\\"hello\\")"}}', "error": ""}  # noqa: E731
    calls = []

    def _switch_llm(system: str, prompt: str) -> dict:
        calls.append((system, prompt))
        if len(calls) == 1:
            return bad_llm(system, prompt)
        # Simulate the backend now running successfully after the fix.
        state["run_ok"] = True
        return fix_llm(system, prompt)

    events: list[SwarmEvent] = []
    coder = CoderSubAgent("coder", events.append, llm=_switch_llm, max_iterations=2)
    task = SubTask(id="t1", agent_type="coder", prompt="Create a hello world script")
    result = await coder.run(task, SwarmScratchpad(""))
    assert result["ok"] is True, result
    assert any(e.stage == "fix" for e in events)


# --------------------------------------------------------------------------- #
# Coder agent integration (real backend)
# --------------------------------------------------------------------------- #

@pytest.mark.anyio
async def test_coder_agent_integration_runs_real_project(tmp_path):
    """One integration test that exercises the real coder_v2 backend."""
    events: list[SwarmEvent] = []
    coder = CoderSubAgent("coder", events.append, llm=_coder_llm, max_iterations=2)
    task = SubTask(id="t1", agent_type="coder", prompt="Create a hello world script")
    result = await coder.run(task, SwarmScratchpad(""))
    assert result["ok"] is True, result
    assert "project_id" in result
    assert "main.py" in result["files"]


# --------------------------------------------------------------------------- #
# Reviewer agent
# --------------------------------------------------------------------------- #

@pytest.mark.anyio
async def test_reviewer_reads_dependency_files(monkeypatch):
    _mock_coder_backend(monkeypatch)
    events: list[SwarmEvent] = []
    coder = CoderSubAgent("coder", events.append, llm=_coder_llm, max_iterations=1)
    coder_task = SubTask(id="t1", agent_type="coder", prompt="Create a hello world script")
    scratchpad = SwarmScratchpad("")
    await coder.run(coder_task, scratchpad)
    scratchpad.plan = [coder_task]

    def _fast_reviewer_llm(_system: str, _prompt: str) -> dict:
        return {"ok": True, "text": "- Good structure.\n- Add input validation.", "error": ""}

    reviewer = ReviewerSubAgent("reviewer", events.append, llm=_fast_reviewer_llm)
    review_task = SubTask(id="t2", agent_type="reviewer", prompt="Review code", dependencies=[0])
    result = await reviewer.run(review_task, scratchpad)
    assert result["ok"] is True
    assert "review" in result


# --------------------------------------------------------------------------- #
# Orchestrator end-to-end
# --------------------------------------------------------------------------- #

@pytest.mark.anyio
async def test_orchestrator_runs_coder_reviewer_tester_wave(monkeypatch):
    _mock_coder_backend(monkeypatch)
    events: list[SwarmEvent] = []
    orch = Orchestrator(emit=events.append, llm=_planner_llm)
    result = await orch.execute("build a pharma daily updates tracker", use_llm=True)
    assert result["ok"] is True, result
    assert "synthesis" in result
    assert result["scratchpad"]["plan"]
    stages = {e.stage for e in events}
    assert "plan_ready" in stages
    assert "code" in stages
    assert "run" in stages


@pytest.mark.anyio
async def test_orchestrator_falls_back_without_llm(monkeypatch):
    _mock_coder_backend(monkeypatch)
    events: list[SwarmEvent] = []
    orch = Orchestrator(emit=events.append, llm=_failing_llm)
    result = await orch.execute("build a tracker", use_llm=False)
    assert result["ok"] is True, result
    assert "synthesis" in result
    assert any(t["agent_type"] == "coder" for t in result["scratchpad"]["plan"])


# --------------------------------------------------------------------------- #
# Tool integration
# --------------------------------------------------------------------------- #

def test_agent_swarm_tool_runs_orchestrator(monkeypatch):
    _mock_coder_backend(monkeypatch)
    from shared.agent_tools import run_tool
    with patch.object(so, "_default_llm_call", _planner_llm):
        result = run_tool(
            "agent.swarm",
            {"prompt": "build a small tracker", "orchestrate": True, "use_llm": True},
            allow_gated=True,
        )
    assert result["ok"] is True, result
    assert result["mode"] == "orchestrated"
    assert "events" in result
    assert "synthesis" in result
