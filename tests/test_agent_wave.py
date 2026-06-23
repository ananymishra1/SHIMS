"""Tests for the wave-based agent execution engine."""
from __future__ import annotations

import asyncio
import json
from typing import Any

from shared import agent_wave


def await_coro(coro):
    return asyncio.run(coro)


def test_plan_wave_includes_tool_names_in_prompt():
    seen_prompt = ""

    async def fake_chat(messages: list[dict[str, Any]]):
        nonlocal seen_prompt
        seen_prompt = [m for m in messages if m.get("role") == "system"][-1]["content"]
        return {"content": '{"reasoning":"direct","wave":[],"final":"done"}', "tool_calls": []}

    calls, final = await_coro(agent_wave.plan_wave(
        [{"role": "user", "content": "search fluconazole patents"}],
        fake_chat,
        {"web.search", "web.fetch", "shell.run"},
    ))

    assert "AVAILABLE TOOLS (use exact names):" in seen_prompt
    assert "- web.search" in seen_prompt
    assert "- web.fetch" in seen_prompt
    assert "- shell.run" in seen_prompt
    assert final == "done"
    assert calls == []


def test_plan_wave_parses_json_wave():
    async def fake_chat(messages: list[dict[str, Any]]):
        return {
            "content": json.dumps({
                "reasoning": "search web",
                "wave": [
                    {"tool": "web.search", "args": {"query": "fluconazole patents"}, "purpose": "find patents"},
                    {"tool": "shell.run", "args": {"command": "echo ok"}, "purpose": "echo"},
                ],
                "final": None,
            }),
            "tool_calls": [],
        }

    calls, final = await_coro(agent_wave.plan_wave(
        [{"role": "user", "content": "search patents"}],
        fake_chat,
        {"web.search", "shell.run"},
    ))

    assert final is None
    assert len(calls) == 2
    assert calls[0].name == "web.search"
    assert calls[0].args == {"query": "fluconazole patents"}
    assert calls[1].name == "shell.run"
