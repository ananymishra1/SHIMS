"""Tests for the dynamic skill runtime."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from shared import agent_tools
from shared import skill_runtime as sr
from shared import skills as sk


@pytest.fixture
def skills_dir(monkeypatch):
    """Use a project-local temp directory to avoid Windows temp path issues."""
    base = Path(__file__).resolve().parents[1] / "storage" / "_agent_test"
    base.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=str(base), prefix="skills_test_") as d:
        path = Path(d)
        monkeypatch.setattr(sk, "SKILLS_DIR", path)
        yield path


def _tool_skill_code() -> str:
    return """
def run(args):
    return {"ok": True, "greeting": "hello " + str(args.get("name", "world"))}
"""


def test_register_all_skill_tools_loads_tool_runtime(skills_dir):
    saved = sk.save_skill(
        "Greeter Tool",
        "A dynamic tool that greets",
        runtime="tool",
        tool_name="greeter_tool",
        tool_schema={
            "name": "greeter_tool",
            "description": "Greets someone",
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
        },
        tool_code=_tool_skill_code(),
        tags=["test", "tool"],
    )
    # Clear any previous registration under the same name.
    agent_tools.TOOLS.pop("greeter_tool", None)
    result = sr.register_all_skill_tools()
    assert result["ok"] is True
    assert "greeter_tool" in result["registered"]
    assert "greeter_tool" in agent_tools.TOOLS

    out = agent_tools.run_tool("greeter_tool", {"name": "SHIMS"})
    assert out["ok"] is True
    assert out["greeting"] == "hello SHIMS"

    # Cleanup
    agent_tools.TOOLS.pop("greeter_tool", None)
    sk.forget_skill(saved["id"])


def test_skill_prompt_block_includes_tool_schema(skills_dir):
    schema = {
        "name": "greeter_tool",
        "description": "Greets someone",
        "parameters": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
        },
    }
    saved = sk.save_skill(
        "Greeter Tool",
        "A dynamic tool that greets",
        runtime="tool",
        tool_name="greeter_tool",
        tool_schema=schema,
        tool_code=_tool_skill_code(),
        tags=["test", "tool"],
    )
    sr.register_all_skill_tools()
    block = sr.skill_prompt_block("greet", limit=5)
    assert "greeter_tool" in block
    # The prompt block shows the parameter schema (the part the LLM needs to
    # construct a call), not the wrapper that also contains name/description.
    assert json.dumps(schema["parameters"])[:40] in block

    # Cleanup
    agent_tools.TOOLS.pop("greeter_tool", None)
    sk.forget_skill(saved["id"])
