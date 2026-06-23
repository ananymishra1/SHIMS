from __future__ import annotations

import asyncio

import pytest

from shared.swarm_runtime import SwarmDispatcher, SwarmResult


async def _fake_runner(agent: dict[str, Any], prompt: str, tools: list[str], context: dict[str, Any]) -> dict[str, Any]:
    await asyncio.sleep(0.01)
    return {
        "ok": True,
        "output": f"{agent['id']}: processed '{prompt}' with {len(tools)} tools",
        "tools_used": tools[:2],
    }


async def _fake_synthesizer(results: list[SwarmResult], context: dict[str, Any]) -> str:
    return "Synthesis: " + "; ".join(r.output for r in results if r.ok)


def test_swarm_dispatches_multiple_agents_in_parallel() -> None:
    dispatcher = SwarmDispatcher(agent_runner=_fake_runner, synthesizer=_fake_synthesizer)
    result = asyncio.run(dispatcher.dispatch("test prompt", agent_ids=["search", "memory"]))
    assert result.ok
    assert result.agent_count == 2
    assert any(r.agent_id == "search" and r.ok for r in result.results)
    assert any(r.agent_id == "memory" and r.ok for r in result.results)
    assert "search" in result.synthesis
    assert "memory" in result.synthesis


def test_swarm_uses_defaults_when_no_agent_ids_given() -> None:
    dispatcher = SwarmDispatcher(agent_runner=_fake_runner, synthesizer=_fake_synthesizer)
    result = asyncio.run(dispatcher.dispatch("hello"))
    assert result.agent_count >= 1
    assert result.ok


def test_swarm_handles_runner_failure() -> None:
    async def failing_runner(agent: dict[str, Any], prompt: str, tools: list[str], context: dict[str, Any]) -> dict[str, Any]:
        if agent["id"] == "search":
            raise RuntimeError("search agent offline")
        return {"ok": True, "output": "ok"}

    dispatcher = SwarmDispatcher(agent_runner=failing_runner, synthesizer=_fake_synthesizer)
    result = asyncio.run(dispatcher.dispatch("test", agent_ids=["search", "memory"]))
    assert result.ok  # memory succeeded
    search_result = next(r for r in result.results if r.agent_id == "search")
    assert not search_result.ok
    assert "search agent offline" in search_result.error


def test_swarm_handles_agent_returning_error() -> None:
    async def error_runner(agent: dict[str, Any], prompt: str, tools: list[str], context: dict[str, Any]) -> dict[str, Any]:
        if agent["id"] == "search":
            return {"ok": False, "error": "rate limited"}
        return {"ok": True, "output": "ok"}

    dispatcher = SwarmDispatcher(agent_runner=error_runner, synthesizer=_fake_synthesizer)
    result = asyncio.run(dispatcher.dispatch("test", agent_ids=["search", "memory"]))
    assert result.ok
    search_result = next(r for r in result.results if r.agent_id == "search")
    assert not search_result.ok
    assert "rate limited" in search_result.error


def test_swarm_filters_tools_to_agent_allowed_set() -> None:
    captured: list[tuple[str, list[str]]] = []

    async def recording_runner(agent: dict[str, Any], prompt: str, tools: list[str], context: dict[str, Any]) -> dict[str, Any]:
        captured.append((agent["id"], tools))
        return {"ok": True, "output": "done"}

    dispatcher = SwarmDispatcher(agent_runner=recording_runner, synthesizer=_fake_synthesizer)
    asyncio.run(dispatcher.dispatch("test", agent_ids=["search", "memory"]))
    search_entry = next(entry for entry in captured if entry[0] == "search")
    memory_entry = next(entry for entry in captured if entry[0] == "memory")
    assert all(tool in {"web.search", "web.health"} for tool in search_entry[1])
    assert all(tool in {"memory.save", "memory.search", "memory.forget", "memory.consolidate"} for tool in memory_entry[1])


def test_swarm_empty_prompt_returns_error() -> None:
    from shared import agent_tools
    result = agent_tools.run_tool("agent.swarm", {"prompt": "   "})
    assert not result["ok"]
    assert "prompt required" in result["error"]


def test_swarm_invalid_agent_ids_type_returns_error() -> None:
    from shared import agent_tools
    # Test the legacy SwarmDispatcher path explicitly.
    result = agent_tools.run_tool("agent.swarm", {"prompt": "hello", "agent_ids": "search", "orchestrate": False})
    assert not result["ok"]
    assert "agent_ids must be a list" in result["error"]
