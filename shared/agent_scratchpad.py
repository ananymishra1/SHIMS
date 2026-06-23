"""Persistent working memory for the SHIMS agent.

Each chat session gets a scratchpad (markdown file) that survives server restarts.
The agent reads it at the start of every turn and writes observations after
every tool call. This gives the agent structured memory across multi-step tasks.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from .config import ROOT_DIR

_SCRATCHPAD_DIR = Path(ROOT_DIR) / "data" / "state" / "scratchpads"
_SCRATCHPAD_DIR.mkdir(parents=True, exist_ok=True)

_MAX_OBSERVATIONS = 20
_MAX_NOTES = 10


@dataclass
class PlanStep:
    idx: int
    tool: str
    args: dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    status: str = "pending"  # pending | running | done | failed
    result_summary: str = ""


@dataclass
class Observation:
    step_idx: int
    tool: str
    args: dict[str, Any]
    result: dict[str, Any]
    timestamp: float = field(default_factory=time.time)


class AgentScratchpad:
    """Per-session working memory for the agent.

    Stored as markdown at ``data/state/scratchpads/{session_id}.md``.
    """

    def __init__(self, session_id: str | None):
        self.session_id = session_id or "default"
        self.path = _SCRATCHPAD_DIR / f"{self.session_id}.md"
        self.plan_steps: list[PlanStep] = []
        self.observations: list[Observation] = []
        self.notes: list[str] = []
        self.goal: str = ""
        self.status: str = "idle"  # idle | planning | executing | reflecting | done
        self.created_at: float = time.time()
        self._load()

    # ------------------------------------------------------------------ #
    # Plan management
    # ------------------------------------------------------------------ #
    def set_plan(self, steps: list[dict[str, Any]]) -> None:
        """Set the current execution plan."""
        self.plan_steps = [
            PlanStep(
                idx=i,
                tool=s.get("tool", ""),
                args=s.get("args") or {},
                reason=s.get("reason", ""),
                status="pending",
            )
            for i, s in enumerate(steps)
        ]
        self.status = "executing"
        self.save()

    def mark_step_running(self, idx: int) -> None:
        if 0 <= idx < len(self.plan_steps):
            self.plan_steps[idx].status = "running"
            self.save()

    def mark_step_done(self, idx: int, result_summary: str = "") -> None:
        if 0 <= idx < len(self.plan_steps):
            self.plan_steps[idx].status = "done"
            self.plan_steps[idx].result_summary = result_summary
            self.save()

    def mark_step_failed(self, idx: int, error: str = "") -> None:
        if 0 <= idx < len(self.plan_steps):
            self.plan_steps[idx].status = "failed"
            self.plan_steps[idx].result_summary = f"ERROR: {error}"
            self.save()

    # ------------------------------------------------------------------ #
    # Observations & notes
    # ------------------------------------------------------------------ #
    def observe(self, step_idx: int, tool: str, args: dict[str, Any], result: dict[str, Any]) -> None:
        """Record a tool execution result."""
        self.observations.append(Observation(
            step_idx=step_idx,
            tool=tool,
            args=dict(args),
            result=_trim_result(dict(result)),
        ))
        while len(self.observations) > _MAX_OBSERVATIONS:
            self.observations.pop(0)
        self.save()

    def note(self, text: str) -> None:
        """Add a free-form note."""
        self.notes.append(text)
        while len(self.notes) > _MAX_NOTES:
            self.notes.pop(0)
        self.save()

    # ------------------------------------------------------------------ #
    # Prompt generation
    # ------------------------------------------------------------------ #
    def to_prompt(self) -> str:
        """Convert scratchpad to a prompt section for the LLM."""
        lines: list[str] = ["## AGENT WORKING MEMORY"]

        if self.goal:
            lines.append(f"**Current Goal:** {self.goal}")

        if self.plan_steps:
            lines.append("### Execution Plan")
            for s in self.plan_steps:
                icon = {"pending": "⏸️", "running": "⏳", "done": "✅", "failed": "❌"}.get(s.status, "•")
                lines.append(f"{icon} Step {s.idx + 1}: `{s.tool}` — {s.reason} [{s.status}]")
                if s.result_summary:
                    lines.append(f"   → {s.result_summary}")

        if self.observations:
            lines.append("### Recent Observations")
            for obs in self.observations[-5:]:
                result_str = _obs_summary(obs.result)
                lines.append(f"- `{obs.tool}` → {result_str}")

        if self.notes:
            lines.append("### Notes")
            for note in self.notes[-3:]:
                lines.append(f"- {note}")

        lines.append("")
        return "\n".join(lines)

    # ------------------------------------------------------------------ #
    # Persistence
    # ------------------------------------------------------------------ #
    def save(self) -> None:
        """Persist to disk as markdown."""
        try:
            self.path.write_text(self._to_markdown(), encoding="utf-8")
        except Exception:
            pass

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            text = self.path.read_text(encoding="utf-8")
            self._from_markdown(text)
        except Exception:
            pass

    def _to_markdown(self) -> str:
        lines = [
            f"# Agent Scratchpad — Session {self.session_id}",
            f"**Status:** {self.status}  ",
            f"**Goal:** {self.goal or '(none)'}  ",
            f"**Updated:** {time.strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            "## Plan",
        ]
        for s in self.plan_steps:
            lines.append(f"- [{s.status}] Step {s.idx + 1}: `{s.tool}` — {s.reason}")
            if s.result_summary:
                lines.append(f"  → {s.result_summary}")
        lines.append("")
        if self.observations:
            lines.append("## Observations")
            for obs in self.observations:
                ts = time.strftime("%H:%M:%S", time.localtime(obs.timestamp))
                lines.append(f"- `{ts}` `{obs.tool}` → {_obs_summary(obs.result)}")
            lines.append("")
        if self.notes:
            lines.append("## Notes")
            for note in self.notes:
                lines.append(f"- {note}")
            lines.append("")
        return "\n".join(lines)

    def _from_markdown(self, text: str) -> None:
        """Best-effort restore from markdown. For now we just keep the file for human readability;
        in-memory state is rebuilt each session. This method is a hook for future full round-trip."""
        pass


# ------------------------------------------------------------------ #
# Helpers
# ------------------------------------------------------------------ #
def _trim_result(result: dict[str, Any]) -> dict[str, Any]:
    """Shrink big fields before storing."""
    out: dict[str, Any] = {}
    for k, v in result.items():
        if isinstance(v, str) and len(v) > 800:
            out[k] = v[:800] + f"\n…[+{len(v) - 800} chars]"
        else:
            out[k] = v
    return out


def _obs_summary(result: dict[str, Any]) -> str:
    """One-line summary of a tool result."""
    if not isinstance(result, dict):
        return str(result)[:120]
    if result.get("ok") is False:
        err = result.get("error") or result.get("stderr") or "failed"
        return f"❌ {str(err)[:100]}"
    if "files" in result:
        files = result["files"]
        return f"✅ {len(files)} file(s)"
    if "stdout" in result:
        out = str(result["stdout"]).strip()
        return f"✅ {out[:100]}"
    if "content" in result:
        return f"✅ {len(str(result['content']))} chars"
    ok = "✅" if result.get("ok") else "❌"
    return f"{ok} {json.dumps(result, default=str)[:100]}"
