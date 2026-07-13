"""Tests for shared/agent_graph.py."""
from __future__ import annotations

import pytest

from shared.agent_graph import AgentGraph
from shared.agent_state import new_agent_state


def make_graph(
    chat_responses: list[str] | None = None,
    tool_responses: dict | None = None,
    *,
    router_uses_llm: bool = False,
) -> AgentGraph:
    """Build a graph with mocked runners.

    Args:
        chat_responses: responses returned by the LLM in call order.
        tool_responses: per-tool mock results.
        router_uses_llm: if True, the first response is consumed by the router
            intent classifier; otherwise the router uses keyword classification.
    """
    graph = AgentGraph()

    responses = list(chat_responses or [])
    counter = [0]

    async def chat_fn(messages: list) -> dict:
        idx = counter[0]
        counter[0] += 1
        # If router uses LLM, its first call is the intent classifier.
        if router_uses_llm and idx == 0:
            return {"content": responses[0] if responses else "conversation"}
        offset = 1 if router_uses_llm else 0
        if idx - offset < len(responses):
            return {"content": responses[idx - offset]}
        return {"content": "{}"}

    def tool_fn(name: str, args: dict, session_id: str) -> dict:
        if tool_responses and name in tool_responses:
            return tool_responses[name]
        if name == "web.search":
            return {"ok": True, "results": [{"url": "http://example.com", "title": "Example", "snippet": "snip"}]}
        if name == "web.fetch":
            return {"ok": True, "text": "Example content"}
        return {"ok": True, "result": "done"}

    graph.set_chat_runner(chat_fn)
    graph.set_tool_runner(tool_fn)
    return graph


@pytest.mark.asyncio
async def test_graph_conversation_path() -> None:
    graph = make_graph(chat_responses=["Hello there!"], router_uses_llm=True)
    state = new_agent_state(session_id="s1", user_query="hi", provider="ollama", model="qwen2.5:7b")
    events = [e async for e in graph.run(state)]
    assert any(e.get("type") == "token" for e in events)
    assert state["intent"] == "conversation"
    assert any(e.get("type") == "done" for e in events)


@pytest.mark.asyncio
async def test_graph_research_path() -> None:
    graph = make_graph(
        chat_responses=["A summary of AI patents."],
        tool_responses={
            "web.search": {"ok": True, "results": [{"url": "http://x.com", "title": "X", "snippet": "AI patents"}]},
            "web.fetch": {"ok": True, "text": "Patent content"},
        },
    )
    state = new_agent_state(session_id="s2", user_query="find recent patents on AI", provider="ollama", model="qwen2.5:7b")
    events = [e async for e in graph.run(state)]
    assert state["intent"] == "research"
    assert any(e.get("type") == "token" for e in events)
    assert any(e.get("type") == "done" for e in events)
    assert len(state.get("memory_updates", [])) >= 1


@pytest.mark.asyncio
async def test_graph_automation_path() -> None:
    graph = make_graph(
        chat_responses=[
            '{"tool": "shell.run", "args": {"command": "echo hi"}}',
            "Done running the shell command.",
        ],
        tool_responses={
            "shell.run": {"ok": True, "stdout": "hi"},
        },
    )
    state = new_agent_state(session_id="s3", user_query="run a shell command", provider="ollama", model="qwen2.5:7b")
    events = [e async for e in graph.run(state)]
    assert state["intent"] == "automation"
    assert any(e.get("type") == "token" for e in events)
    assert "shell.run_1" in state.get("tool_outputs", {})


@pytest.mark.asyncio
async def test_graph_hybrid_path() -> None:
    graph = make_graph(
        chat_responses=[
            "Research summary.",
            '{"tool": "code.run", "args": {"code": "1+1"}}',
            "Based on the research and calculation, the answer is 2.",
        ],
        tool_responses={
            "web.search": {"ok": True, "results": [{"url": "http://x.com", "title": "X", "snippet": "AI"}]},
            "web.fetch": {"ok": True, "text": "Content"},
            "code.run": {"ok": True, "result": "2"},
        },
    )
    state = new_agent_state(session_id="s4", user_query="research AI and run a calculation", provider="ollama", model="qwen2.5:7b")
    events = [e async for e in graph.run(state)]
    assert state["intent"] == "hybrid"
    assert any(e.get("type") == "token" for e in events)


@pytest.mark.asyncio
async def test_graph_state_json_serializable() -> None:
    import json
    graph = make_graph(chat_responses=["hi"])
    state = new_agent_state(session_id="s5", user_query="hello", provider="ollama", model="qwen2.5:7b")
    [e async for e in graph.run(state)]
    dumped = json.dumps(state, default=str)
    loaded = json.loads(dumped)
    assert loaded["session_id"] == "s5"
