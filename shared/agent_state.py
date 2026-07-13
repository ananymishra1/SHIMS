"""Typed turn state for the SHIMS agent graph and loop.

This module defines the single source of truth for one agent turn. It is
intentionally dependency-light so it can be imported by the frontend-facing
backend, the wave engine, and the graph nodes without dragging in heavy
frameworks.
"""
from __future__ import annotations

import time
from typing import Any, TypedDict


class NodeTiming(TypedDict, total=False):
    """Per-node timing record."""

    node: str
    started_at: float
    finished_at: float
    elapsed_ms: int


class ResearchSource(TypedDict, total=False):
    """One source captured by the research sub-agent."""

    url: str
    title: str
    snippet: str
    citation: str


class ResearchContext(TypedDict, total=False):
    """Output of the research sub-agent."""

    query: str
    urls: list[str]
    sources: list[ResearchSource]
    summaries: list[str]
    citations: list[dict[str, Any]]


class AutomationStep(TypedDict, total=False):
    """One step in an automation plan."""

    step: str
    tool: str
    args: dict[str, Any]
    purpose: str


class ToolOutput(TypedDict, total=False):
    """Output from an automation tool execution."""

    tool: str
    ok: bool
    result: dict[str, Any]
    error: str | None


class MemoryUpdate(TypedDict, total=False):
    """A durable fact or skill to persist after a turn."""

    type: str  # "fact" | "skill" | "preference"
    content: str
    tags: list[str]
    source: str


class AgentState(TypedDict, total=False):
    """Mutable turn state passed through the agent graph/loop.

    Mirrors the LangGraph-style state you drafted, but stays JSON-serializable
    so it can be cheaply checkpointed to SQLite or logged.
    """

    # Identity / routing
    session_id: str
    user_query: str
    intent: str  # conversation | research | automation | hybrid
    current_node: str
    previous_node: str | None

    # Conversation context
    messages: list[dict[str, Any]]
    system_prompt: str
    original_request: str

    # Sub-agent outputs
    research_context: ResearchContext
    automation_plan: list[AutomationStep]
    tool_outputs: dict[str, ToolOutput]
    react_iterations: int
    max_react_steps: int

    # Memory / skills
    memory_updates: list[MemoryUpdate]
    skills_used: list[str]
    brain_addendum: str

    # Approvals / gates
    approvals_pending: list[dict[str, Any]]
    stop_reason: str | None

    # Timing / telemetry
    started_at: float
    node_timings: list[NodeTiming]
    model: str
    provider: str


def new_agent_state(
    *,
    session_id: str,
    user_query: str,
    messages: list[dict[str, Any]] | None = None,
    system_prompt: str = "",
    provider: str = "ollama",
    model: str = "",
    max_react_steps: int = 5,
) -> AgentState:
    """Return a fresh state for a new turn."""
    return {
        "session_id": session_id,
        "user_query": user_query,
        "intent": "conversation",
        "current_node": "start",
        "previous_node": None,
        "messages": list(messages or []),
        "system_prompt": system_prompt,
        "original_request": user_query,
        "research_context": {},
        "automation_plan": [],
        "tool_outputs": {},
        "react_iterations": 0,
        "max_react_steps": max_react_steps,
        "memory_updates": [],
        "skills_used": [],
        "brain_addendum": "",
        "approvals_pending": [],
        "stop_reason": None,
        "started_at": time.perf_counter(),
        "node_timings": [],
        "provider": provider,
        "model": model,
    }


def start_node(state: AgentState, node: str) -> None:
    """Mark the start of a node in the state."""
    state["previous_node"] = state.get("current_node")
    state["current_node"] = node
    state.setdefault("node_timings", []).append({
        "node": node,
        "started_at": time.perf_counter(),
    })


def finish_node(state: AgentState, node: str | None = None) -> NodeTiming:
    """Mark the end of the current or named node and return its timing."""
    timings = state.setdefault("node_timings", [])
    target = node or state.get("current_node")
    if not target:
        return {"node": "unknown", "elapsed_ms": 0}
    # Find the most recent unfinished timing for this node
    for record in reversed(timings):
        if record.get("node") == target and "finished_at" not in record:
            record["finished_at"] = time.perf_counter()
            elapsed = int((record["finished_at"] - record["started_at"]) * 1000)
            record["elapsed_ms"] = elapsed
            return dict(record)
    return {"node": target, "elapsed_ms": 0}


def total_elapsed_ms(state: AgentState) -> int:
    """Return elapsed ms since the turn started."""
    return int((time.perf_counter() - state["started_at"]) * 1000)


def add_memory_update(
    state: AgentState,
    update_type: str,
    content: str,
    tags: list[str] | None = None,
    source: str = "agent_graph",
) -> None:
    """Queue a durable memory update."""
    state.setdefault("memory_updates", []).append({
        "type": update_type,
        "content": content,
        "tags": list(tags or []),
        "source": source,
    })


def set_research_context(state: AgentState, ctx: ResearchContext) -> None:
    """Replace the research context."""
    state["research_context"] = ctx


def append_research_summary(state: AgentState, summary: str, sources: list[ResearchSource] | None = None) -> None:
    """Append a research summary and its sources."""
    ctx = state.setdefault("research_context", {})
    ctx.setdefault("summaries", []).append(summary)
    if sources:
        ctx.setdefault("sources", []).extend(sources)


def append_tool_output(state: AgentState, key: str, output: ToolOutput) -> None:
    """Record a tool output from the automation sub-agent."""
    state.setdefault("tool_outputs", {})[key] = output


def increment_react_iterations(state: AgentState) -> int:
    """Increment and return the ReAct iteration counter."""
    state["react_iterations"] = state.get("react_iterations", 0) + 1
    return state["react_iterations"]
