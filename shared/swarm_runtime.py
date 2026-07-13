from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from .agent_registry import get_agent, list_agents


@dataclass
class SwarmResult:
    agent_id: str
    ok: bool
    output: str
    latency_ms: float
    error: str | None = None
    tools_used: list[str] = field(default_factory=list)


@dataclass
class SwarmDispatchResult:
    ok: bool
    results: list[SwarmResult]
    synthesis: str
    agent_count: int
    latency_ms: float
    error: str | None = None


AgentRunner = Callable[
    [dict[str, Any], str, list[str], dict[str, Any]],
    Awaitable[dict[str, Any]],
]
Synthesizer = Callable[[list[SwarmResult], dict[str, Any]], Awaitable[str]]


class SwarmDispatcher:
    """Dispatch multiple SHIMS agents in parallel and synthesize a unified answer.

    The dispatcher is intentionally dependency-injected: production code can
    supply a real agent runner, while tests can pass lightweight mocks.
    """

    def __init__(
        self,
        agent_runner: AgentRunner | None = None,
        synthesizer: Synthesizer | None = None,
    ) -> None:
        self.agent_runner = agent_runner or self._default_agent_runner
        self.synthesizer = synthesizer or self._default_synthesizer

    @staticmethod
    def _agent_system_prompt(agent: dict[str, Any], user_prompt: str, shared_context: dict[str, Any]) -> str:
        role = agent.get("purpose", "general assistant")
        name = agent.get("name", agent.get("id", "Agent"))
        tools = ", ".join(agent.get("tools", []))
        return (
            f"You are {name}, a specialized SHIMS agent.\n"
            f"Role: {role}\n"
            f"Allowed tools: {tools}\n"
            f"Only use the tools listed above. Do not invent facts. "
            f"Return concise, actionable output.\n\n"
            f"Shared context: {shared_context}\n\n"
            f"Task: {user_prompt}"
        )

    async def _default_agent_runner(
        self,
        agent: dict[str, Any],
        prompt: str,
        tools: list[str],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """Default runner delegates to the SHIMS agent loop."""
        system = self._agent_system_prompt(agent, prompt, context)
        try:
            from .agent_loop import run_agent_loop
            from .agent_model_router import resolve_agent, resolve_role
            provider, model, _ = resolve_agent(agent.get("id", "unknown"))
            router_provider, router_model, _ = resolve_role("router")
            async for ev in run_agent_loop(
                message=prompt,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
                model=model,
                provider=provider,
                router_model=router_model,
                router_provider=router_provider,
                session_id=None,
                create_pending=lambda **_: {"ok": True, "id": "swarm-" + agent.get("id", "unknown")},
                tool_names=tools,
            ):
                if ev.get("__final__"):
                    result = ev["__final__"]
                    break
            else:
                result = {}
            return {
                "ok": True,
                "output": str(result.get("answer", result.get("output", ""))),
                "tools_used": result.get("tools_used", []),
            }
        except Exception as exc:
            return {"ok": False, "error": f"agent loop failed: {exc}"}

    async def _default_synthesizer(self, results: list[SwarmResult], context: dict[str, Any]) -> str:
        """Rule-based fallback synthesis; overridden for LLM-based synthesis."""
        ok_results = [r for r in results if r.ok]
        if not ok_results:
            errors = "; ".join(f"{r.agent_id}: {r.error}" for r in results if r.error)
            return f"No agents produced a result. Errors: {errors}"
        lines: list[str] = []
        for r in ok_results:
            lines.append(f"### {r.agent_id}\n{r.output}")
        return "\n\n".join(lines)

    async def dispatch(
        self,
        prompt: str,
        agent_ids: list[str] | None = None,
        context: dict[str, Any] | None = None,
        shared_context: dict[str, Any] | None = None,
    ) -> SwarmDispatchResult:
        """Resolve agents, run them in parallel, and synthesize the outputs."""
        context = context or {}
        shared_context = shared_context or {}
        shared_context.setdefault("original_prompt", prompt)

        agents = self._resolve_agents(agent_ids)
        if not agents:
            return SwarmDispatchResult(
                ok=False,
                results=[],
                synthesis="No agents available.",
                agent_count=0,
                latency_ms=0.0,
                error="no agents resolved",
            )

        started = time.perf_counter()
        tasks = [self._run_one(agent, prompt, context, shared_context) for agent in agents]
        gathered = await asyncio.gather(*tasks, return_exceptions=True)

        results: list[SwarmResult] = []
        for item in gathered:
            if isinstance(item, Exception):
                results.append(
                    SwarmResult(
                        agent_id="unknown",
                        ok=False,
                        output="",
                        latency_ms=0.0,
                        error=str(item),
                    )
                )
            else:
                results.append(item)

        synthesis = await self.synthesizer(results, shared_context)
        latency_ms = (time.perf_counter() - started) * 1000
        return SwarmDispatchResult(
            ok=any(r.ok for r in results),
            results=results,
            synthesis=synthesis,
            agent_count=len(agents),
            latency_ms=latency_ms,
            error=None,
        )

    async def _run_one(
        self,
        agent: dict[str, Any],
        prompt: str,
        context: dict[str, Any],
        shared_context: dict[str, Any],
    ) -> SwarmResult:
        agent_id = agent.get("id", "unknown")
        started = time.perf_counter()
        allowed_tools = agent.get("tools", [])
        try:
            raw = await self.agent_runner(agent, prompt, allowed_tools, context)
            latency_ms = (time.perf_counter() - started) * 1000
            if isinstance(raw, dict) and raw.get("ok"):
                return SwarmResult(
                    agent_id=agent_id,
                    ok=True,
                    output=str(raw.get("output", raw.get("answer", ""))),
                    latency_ms=latency_ms,
                    tools_used=raw.get("tools_used", []),
                )
            return SwarmResult(
                agent_id=agent_id,
                ok=False,
                output="",
                latency_ms=latency_ms,
                error=raw.get("error", "agent returned ok=false")
                if isinstance(raw, dict)
                else "invalid agent result",
            )
        except Exception as exc:
            return SwarmResult(
                agent_id=agent_id,
                ok=False,
                output="",
                latency_ms=(time.perf_counter() - started) * 1000,
                error=str(exc),
            )

    def _resolve_agents(self, agent_ids: list[str] | None) -> list[dict[str, Any]]:
        if not agent_ids:
            # Default small set of generalist agents for open-ended questions.
            defaults = {"supervisor", "search", "memory", "documents"}
            return [a for a in list_agents() if a["id"] in defaults]
        resolved: list[dict[str, Any]] = []
        for aid in agent_ids:
            agent = get_agent(aid)
            if agent:
                resolved.append(agent)
        return resolved
