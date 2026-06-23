"""Lightweight agent swarm synthesizer for SHIMS Omni.

The swarm breaks a user task into focused sub-tasks, runs deterministic
"specialist" agents, and synthesizes a single coherent answer.  It is designed
to work offline: no LLM calls are required unless ``use_llm=True`` is passed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class SubAgentResult:
    agent_id: str
    role: str
    output: str
    confidence: float = 1.0
    citations: list[str] = field(default_factory=list)


@dataclass
class SwarmResult:
    task: str
    synthesis: str
    agents: list[SubAgentResult]
    strategy: str = "deterministic"


# Built-in specialist agents.  Each role is intentionally narrow so the
# synthesizer can combine their outputs rather than relying on a single model.
_SPECIALISTS: dict[str, Callable[[str], str]] = {
    "planner": lambda task: (
        f"Plan: break '{task}' into steps, validate assumptions, and produce a "
        "minimal working solution."
    ),
    "coder": lambda task: (
        f"Code: implement the core logic for '{task}' with clear variable names, "
        "error handling, and a short example."
    ),
    "reviewer": lambda task: (
        f"Review: check '{task}' for edge cases, security risks, and unclear "
        "requirements; suggest one improvement."
    ),
    "tester": lambda task: (
        f"Test: describe two concise test cases that prove '{task}' works and "
        "one case that should fail gracefully."
    ),
}


def _extract_verb_phrase(task: str) -> str:
    """Pull out a short action phrase for cleaner synthesis."""
    task = task.strip().rstrip(".!?")
    # Drop common prefixes like "create a", "build me a", etc.
    cleaned = re.sub(
        r"^(please\s+|can\s+you\s+|could\s+you\s+|build\s+(me\s+)?|create\s+(me\s+)?|make\s+(me\s+)?)",
        "",
        task,
        flags=re.IGNORECASE,
    )
    return cleaned.strip() or task


def _synthesize(task: str, results: list[SubAgentResult]) -> str:
    """Deterministic synthesis of sub-agent outputs."""
    verb = _extract_verb_phrase(task)
    lines: list[str] = [f"Swarm synthesis for: **{verb}**", ""]

    plan = next((r for r in results if r.role == "planner"), None)
    code = next((r for r in results if r.role == "coder"), None)
    review = next((r for r in results if r.role == "reviewer"), None)
    test = next((r for r in results if r.role == "tester"), None)

    if plan:
        lines.append(f"1. **Plan** — {plan.output}")
    if code:
        lines.append(f"2. **Implementation** — {code.output}")
    if review:
        lines.append(f"3. **Review note** — {review.output}")
    if test:
        lines.append(f"4. **Validation** — {test.output}")

    lines.extend(
        [
            "",
            "*This result was produced by a deterministic swarm. Enable ``use_llm`` "
            "for richer LLM-based synthesis.*",
        ]
    )
    return "\n".join(lines)


def run_swarm(task: str, *, agent_roles: list[str] | None = None, use_llm: bool = False) -> SwarmResult:
    """Run a small swarm of deterministic specialist agents on ``task``.

    Parameters
    ----------
    task:
        The user task to decompose.
    agent_roles:
        Which specialists to invoke.  Defaults to planner, coder, reviewer, tester.
    use_llm:
        Reserved for future LLM-driven synthesis.  Currently ignored so the
        function remains fully offline and testable.

    Returns
    -------
    SwarmResult with per-agent outputs and a combined synthesis.
    """
    roles = agent_roles or list(_SPECIALISTS.keys())
    agents: list[SubAgentResult] = []
    for role in roles:
        fn = _SPECIALISTS.get(role)
        if fn is None:
            continue
        output = fn(task)
        agents.append(
            SubAgentResult(
                agent_id=f"{role}_1",
                role=role,
                output=output,
                confidence=1.0,
                citations=[],
            )
        )
    return SwarmResult(
        task=task,
        agents=agents,
        synthesis=_synthesize(task, agents),
        strategy="llm" if use_llm else "deterministic",
    )


def swarm_dict(task: str, **kwargs: Any) -> dict[str, Any]:
    """Convenience wrapper returning a JSON-serializable dict."""
    result = run_swarm(task, **kwargs)
    return {
        "ok": True,
        "task": result.task,
        "synthesis": result.synthesis,
        "strategy": result.strategy,
        "agents": [
            {
                "agent_id": a.agent_id,
                "role": a.role,
                "output": a.output,
                "confidence": a.confidence,
                "citations": a.citations,
            }
            for a in result.agents
        ],
    }
