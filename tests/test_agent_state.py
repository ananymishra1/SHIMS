"""Tests for shared/agent_state.py."""
from __future__ import annotations

import time

import pytest

from shared.agent_state import (
    AgentState,
    add_memory_update,
    append_research_summary,
    append_tool_output,
    finish_node,
    increment_react_iterations,
    new_agent_state,
    set_research_context,
    start_node,
    total_elapsed_ms,
)


def test_new_agent_state_defaults() -> None:
    state = new_agent_state(session_id="sess-1", user_query="hello")
    assert state["session_id"] == "sess-1"
    assert state["user_query"] == "hello"
    assert state["intent"] == "conversation"
    assert state["current_node"] == "start"
    assert state["messages"] == []
    assert state["max_react_steps"] == 5
    assert state["provider"] == "ollama"


def test_new_agent_state_with_messages() -> None:
    msgs = [{"role": "user", "content": "hi"}]
    state = new_agent_state(session_id="sess-2", user_query="hi", messages=msgs, provider="lmstudio", model="qwen2.5-7b")
    assert state["messages"] == msgs
    assert state["provider"] == "lmstudio"
    assert state["model"] == "qwen2.5-7b"


def test_node_timing() -> None:
    state = new_agent_state(session_id="sess-3", user_query="test")
    start_node(state, "router")
    assert state["current_node"] == "router"
    assert state["previous_node"] == "start"
    time.sleep(0.01)
    timing = finish_node(state, "router")
    assert timing["node"] == "router"
    assert timing["elapsed_ms"] >= 10
    assert timing["finished_at"] >= timing["started_at"]


def test_total_elapsed_ms() -> None:
    state = new_agent_state(session_id="sess-4", user_query="test")
    time.sleep(0.01)
    assert total_elapsed_ms(state) >= 10


def test_memory_update() -> None:
    state = new_agent_state(session_id="sess-5", user_query="test")
    add_memory_update(state, "preference", "I like dark mode", tags=["ui"], source="test")
    assert len(state["memory_updates"]) == 1
    assert state["memory_updates"][0]["type"] == "preference"


def test_research_context() -> None:
    state = new_agent_state(session_id="sess-6", user_query="research test")
    set_research_context(state, {"query": "AI", "urls": ["http://x.com"]})
    assert state["research_context"]["query"] == "AI"
    append_research_summary(state, "Summary one", sources=[{"url": "http://y.com", "title": "Y"}])
    assert state["research_context"]["summaries"] == ["Summary one"]
    assert len(state["research_context"]["sources"]) == 1


def test_tool_output_and_react() -> None:
    state = new_agent_state(session_id="sess-7", user_query="automation test")
    append_tool_output(state, "shell_1", {"tool": "shell.run", "ok": True, "result": {"stdout": "ok"}, "error": None})
    assert "shell_1" in state["tool_outputs"]
    assert increment_react_iterations(state) == 1
    assert increment_react_iterations(state) == 2


def test_state_is_json_serializable() -> None:
    import json
    state = new_agent_state(session_id="s", user_query="q")
    start_node(state, "router")
    finish_node(state, "router")
    dumped = json.dumps(state, default=str)
    loaded = json.loads(dumped)
    assert loaded["session_id"] == "s"
