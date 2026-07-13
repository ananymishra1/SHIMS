"""Reasoning-event formatter for SHIMS Omni.

Produces the LM Studio-style "Thought for X.XXs" UX in the backend stream.
The frontend receives `thought` events with `stage`, `content`, and optional
`elapsed_ms` / `total_ms` fields and renders them in a collapsible block.
"""
from __future__ import annotations

import time
from typing import Any, AsyncGenerator

from .agent_state import AgentState, finish_node, start_node, total_elapsed_ms


# Human-readable stage labels for the reasoning block.
STAGE_LABELS: dict[str, str] = {
    "start": "Start",
    "router": "Intent",
    "memory_load": "Memory",
    "plan": "Plan",
    "research": "Research",
    "automation": "Automation",
    "tool": "Tool",
    "synthesis": "Synthesize",
    "memory_save": "Remember",
    "agent": "Agent",
    "conversation": "Context",
    "stt_correction": "Voice",
    "status": "Status",
}


def _format_elapsed(ms: int) -> str:
    """Return a compact elapsed-time string."""
    if ms < 1000:
        return f"{ms}ms"
    return f"{ms / 1000:.2f}s"


class ReasoningStream:
    """Helper that yields reasoning events with timing for one agent turn."""

    def __init__(self, state: AgentState):
        self.state = state
        self._current_node: str | None = None
        self._node_started_at: float = 0.0
        self._thought_count = 0

    async def emit(
        self,
        stage: str,
        content: str,
        *,
        elapsed_ms: int | None = None,
        total_ms: int | None = None,
        model: str | None = None,
        provider: str | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Yield a single thought event."""
        self._thought_count += 1
        total = total_ms if total_ms is not None else total_elapsed_ms(self.state)
        event: dict[str, Any] = {
            "type": "thought",
            "stage": stage,
            "content": content,
            "thought_index": self._thought_count,
            "total_ms": total,
            "total_elapsed": _format_elapsed(total),
        }
        if elapsed_ms is not None:
            event["elapsed_ms"] = elapsed_ms
            event["elapsed"] = _format_elapsed(elapsed_ms)
        if model:
            event["model"] = model
        if provider:
            event["provider"] = provider
        yield event

    async def node(
        self,
        node: str,
        content: str = "",
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Start a node, optionally emit a thought, and finish timing on exit.

        This is an async generator helper; use it as:

            async for ev in reasoning.node("research", "Starting research"):
                yield ev
            # ... do work ...
            async for ev in reasoning.emit("research", "Done"):
                yield ev

        It only records start time; finish_node() must be called explicitly or via
        finish_current_node() when the node's work is complete.
        """
        start_node(self.state, node)
        self._current_node = node
        self._node_started_at = time.perf_counter()
        if content:
            async for ev in self.emit(node, content):
                yield ev

    def finish_current_node(self) -> AsyncGenerator[dict[str, Any], None]:
        """Finish the current node and yield a summary thought."""
        return self._finish_node(self._current_node)

    async def _finish_node(self, node: str | None) -> AsyncGenerator[dict[str, Any], None]:
        timing = finish_node(self.state, node)
        self._current_node = None
        elapsed = timing.get("elapsed_ms", 0)
        label = STAGE_LABELS.get(node or "unknown", node or "unknown")
        async for ev in self.emit(
            node or "agent",
            f"{label} finished in {_format_elapsed(elapsed)}",
            elapsed_ms=elapsed,
        ):
            yield ev

    async def thought_for(
        self,
        stage: str,
        content: str,
        started_at: float,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Emit a thought whose elapsed time is computed from a start timestamp."""
        elapsed = int((time.perf_counter() - started_at) * 1000)
        async for ev in self.emit(stage, content, elapsed_ms=elapsed, total_ms=total_elapsed_ms(self.state)):
            yield ev

    async def model_thought(
        self,
        stage: str,
        content: str,
        started_at: float,
        *,
        model: str,
        provider: str,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Emit a thought specifically about a model call, with timing and model info."""
        elapsed = int((time.perf_counter() - started_at) * 1000)
        async for ev in self.emit(
            stage,
            content,
            elapsed_ms=elapsed,
            model=model,
            provider=provider,
        ):
            yield ev


def stage_label(stage: str) -> str:
    """Return a human-readable label for a stage."""
    return STAGE_LABELS.get(stage, stage.replace("_", " ").title())


def build_reasoning_summary(state: AgentState) -> dict[str, Any]:
    """Build a summary object for the frontend after a turn ends."""
    timings = state.get("node_timings", [])
    total = total_elapsed_ms(state)
    by_node: dict[str, int] = {}
    for t in timings:
        node = t.get("node", "unknown")
        by_node[node] = by_node.get(node, 0) + t.get("elapsed_ms", 0)
    return {
        "total_ms": total,
        "total_elapsed": _format_elapsed(total),
        "node_breakdown": by_node,
        "thought_count": len(timings),
    }
