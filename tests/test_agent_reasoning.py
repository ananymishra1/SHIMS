"""Tests for shared/agent_reasoning.py."""
from __future__ import annotations

import asyncio
import time

import pytest

from shared.agent_reasoning import ReasoningStream, _format_elapsed, build_reasoning_summary, stage_label
from shared.agent_state import new_agent_state


def test_format_elapsed() -> None:
    assert _format_elapsed(500) == "500ms"
    assert _format_elapsed(1500) == "1.50s"
    assert _format_elapsed(2600) == "2.60s"


def test_stage_label() -> None:
    assert stage_label("router") == "Intent"
    assert stage_label("unknown_stage") == "Unknown Stage"


@pytest.mark.asyncio
async def test_reasoning_emit() -> None:
    state = new_agent_state(session_id="s", user_query="q")
    rs = ReasoningStream(state)
    events = [e async for e in rs.emit("plan", "Planning...")]
    assert len(events) == 1
    ev = events[0]
    assert ev["type"] == "thought"
    assert ev["stage"] == "plan"
    assert ev["content"] == "Planning..."
    assert "total_ms" in ev
    assert "total_elapsed" in ev
    assert ev["thought_index"] == 1


@pytest.mark.asyncio
async def test_reasoning_model_thought() -> None:
    state = new_agent_state(session_id="s", user_query="q")
    rs = ReasoningStream(state)
    started = time.perf_counter()
    time.sleep(0.01)
    events = [e async for e in rs.model_thought("research", "Searching", started, model="qwen2.5:7b", provider="ollama")]
    assert len(events) == 1
    ev = events[0]
    assert ev["model"] == "qwen2.5:7b"
    assert ev["provider"] == "ollama"
    assert ev["elapsed_ms"] >= 10


@pytest.mark.asyncio
async def test_reasoning_node_generator() -> None:
    state = new_agent_state(session_id="s", user_query="q")
    rs = ReasoningStream(state)
    collected: list[dict] = []
    async for ev in rs.node("research", "Starting research"):
        collected.append(ev)
    async for ev in rs.emit("research", "Fetching URLs"):
        collected.append(ev)
    async for ev in rs.finish_current_node():
        collected.append(ev)
    assert len(collected) >= 2
    assert collected[0]["stage"] == "research"
    assert collected[-1]["stage"] == "research"
    assert "finished" in collected[-1]["content"].lower() or "ms" in collected[-1]["content"]


@pytest.mark.asyncio
async def test_build_reasoning_summary() -> None:
    state = new_agent_state(session_id="s", user_query="q")
    rs = ReasoningStream(state)
    async for _ in rs.node("router"):
        pass
    async for _ in rs.emit("router", "Classifying"):
        pass
    async for _ in rs.finish_current_node():
        pass
    summary = build_reasoning_summary(state)
    assert summary["total_ms"] >= 0
    assert summary["thought_count"] >= 1
    assert "router" in summary["node_breakdown"]


@pytest.mark.asyncio
async def test_reasoning_stream_counts_thoughts() -> None:
    state = new_agent_state(session_id="s", user_query="q")
    rs = ReasoningStream(state)
    events = []
    async for ev in rs.emit("a", "1"):
        events.append(ev)
    async for ev in rs.emit("b", "2"):
        events.append(ev)
    assert events[0]["thought_index"] == 1
    assert events[1]["thought_index"] == 2
