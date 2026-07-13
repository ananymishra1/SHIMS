"""Unit tests for the DuoBot collaborative task runner.

These tests use deterministic mocked AI/LLM responses so they run quickly and do
not require a running SHIMS server or Ollama.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from shared import duobot_tasks


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _patch_ai_module(monkeypatch):
    """Prevent heavy model loading by replacing the AI module with a stub."""
    monkeypatch.setattr(duobot_tasks, "ai_module", object())


@pytest.fixture
def sample_task(tmp_path, monkeypatch):
    """Create a real Coder project and a DuoBot task backed by it."""
    # Point DuoBot task storage at a temp directory for isolation.
    monkeypatch.setattr(duobot_tasks, "TASKS_DIR", tmp_path / "duobot_tasks")
    duobot_tasks.TASKS_DIR.mkdir(parents=True, exist_ok=True)

    project = duobot_tasks.create_project(name="duobot_test_project", template="python")
    assert project.get("ok")

    task = duobot_tasks.create_task(
        conv_id="conv_test",
        title="hello cli",
        description="Create a small Python CLI that prints hello world and a unittest.",
    )
    assert task["ok"]
    return task["task"]


def _make_primary_responses(plan_json: dict, review_text: str = "DONE"):
    """Return an async function that returns *plan_json* on the first call (plan
    step) and *review_text* on any later call (review step)."""
    async def _ask_primary(system: str, prompt: str, model: str = "", timeout: float = 60.0) -> str:
        if "architect" in system.lower():
            return json.dumps(plan_json)
        return review_text

    return _ask_primary


def _make_local_responses(files: dict[str, str]):
    """Return an async function that writes code based on the file requested in
    the prompt."""
    async def _ask_local(messages: list[dict[str, str]], model: str = "qwen2.5:3b", timeout: float = 60.0) -> str:
        user = messages[-1]["content"]
        # Extract "Implement file: <path>" or "File to fix: <path>".
        marker = "Implement file:"
        if marker not in user:
            marker = "File to fix:"
        idx = user.find(marker)
        if idx == -1:
            return "# no file marker"
        rel = user[idx + len(marker):].split("\n")[0].strip()
        code = files.get(rel, f"# placeholder for {rel}")
        return f"```python\n{code}\n```"

    return _ask_local


class TestDuoBotCollaboration:
    def test_plan_step_parses_json(self, sample_task, monkeypatch):
        plan = {
            "plan": [
                {"file": "hello.py", "purpose": "core greeting", "depends_on": []},
                {"file": "main.py", "purpose": "entrypoint", "depends_on": ["hello.py"]},
                {"file": "test_hello.py", "purpose": "unittest", "depends_on": ["hello.py"]},
            ],
            "entrypoint": "main.py",
            "test_command": "python -m unittest discover -s . -p test_*.py",
        }
        monkeypatch.setattr(duobot_tasks, "_ask_primary", _make_primary_responses(plan))

        result = _run(duobot_tasks._plan_step(sample_task))
        assert result["ok"]
        assert sample_task["status"] == "planned"
        assert len(sample_task["plan"]) == 3

    def test_full_collaboration_loop(self, sample_task, monkeypatch):
        plan = {
            "plan": [
                {"file": "hello.py", "purpose": "core greeting", "depends_on": []},
                {"file": "main.py", "purpose": "entrypoint", "depends_on": ["hello.py"]},
                {"file": "test_hello.py", "purpose": "unittest", "depends_on": ["hello.py"]},
            ],
            "entrypoint": "main.py",
            "test_command": "python -m unittest discover -s . -p test_*.py",
        }
        monkeypatch.setattr(duobot_tasks, "_ask_primary", _make_primary_responses(plan, review_text="DONE"))

        files = {
            "hello.py": (
                "def say_hello(name='World'):\n"
                "    return f'Hello, {name}!'\n"
            ),
            "main.py": (
                "from hello import say_hello\n"
                "def main():\n"
                "    print(say_hello())\n"
                "if __name__ == '__main__':\n"
                "    main()\n"
            ),
            "test_hello.py": (
                "import unittest\n"
                "from hello import say_hello\n"
                "class TestHello(unittest.TestCase):\n"
                "    def test_say_hello(self):\n"
                "        self.assertEqual(say_hello(), 'Hello, World!')\n"
                "if __name__ == '__main__':\n"
                "    unittest.main()\n"
            ),
        }
        monkeypatch.setattr(duobot_tasks, "_ask_local", _make_local_responses(files))

        # Plan
        result = _run(duobot_tasks.run_collaboration_round(sample_task["id"]))
        assert result["task"]["status"] == "planned"

        # Run rounds until the task is complete.
        for _ in range(15):
            result = _run(duobot_tasks.run_collaboration_round(sample_task["id"]))
            if result["task"]["status"] == "complete":
                break

        assert set(result["task"]["files"]) == {"hello.py", "main.py", "test_hello.py"}
        assert result["task"]["last_test"]["ok"] is True
        assert "OK" in result["task"]["last_test"]["output"]
        assert result["task"]["status"] == "complete"

    def test_apply_fix_step(self, sample_task, monkeypatch):
        # Seed a file so we can fix it.
        project_id = sample_task["project_id"]
        duobot_tasks.write_file(project_id, "hello.py", "def say_hello():\n    return 'Hi'\n")
        sample_task["pending_fix"] = {
            "fix_file": "hello.py",
            "instructions": "Return 'Hello, World!' instead of 'Hi'.",
        }

        files = {
            "hello.py": "def say_hello():\n    return 'Hello, World!'\n",
        }
        monkeypatch.setattr(duobot_tasks, "_ask_local", _make_local_responses(files))

        result = _run(duobot_tasks._apply_fix_step(sample_task))
        assert result["ok"] is True
        # _extract_code_block strips trailing whitespace, so the written file has no trailing newline.
        assert duobot_tasks.read_file(project_id, "hello.py")["content"] == files["hello.py"].rstrip("\n")
