"""
Smart brain.

Runs in the background. Pulls tasks off the bus, performs deep reasoning:
  * retrosynthesis (calls into retrosynthesis.plan)
  * symbolic verification of every step
  * multi-objective scoring (calls into fto.scoring)
  * writes a structured, citation-grounded summary

Emits progress events at every stage so the fast brain can narrate. Cancellable.
"""
from __future__ import annotations
import asyncio
import time
from typing import Any

from ..bus import Bus, ProgressEvent, TaskRequest, TaskResult
from ..fto import score_routes
from ..retrosynthesis import plan_retrosynthesis
from ..verifier import score_route_feasibility
from .llm import LLMProvider


SMART_SYSTEM = """You are the smart brain of Shims Chem. You handle deep chemistry reasoning.

For every retrosynthesis task you receive, you:
  1. Have the planner produce candidate routes (already symbolically verified
     by the time you see them).
  2. Score them on the Pareto front: feasibility, FTO risk, cost, yield,
     impurity, scalability, regulatory.
  3. Write a structured summary the chemist can act on — including:
     - the top 3 routes with their explicit trade-offs,
     - the verifier issues that ruled out worse routes,
     - the specific patents that drove the FTO scoring,
     - explicit, named limitations.

Tone: scientific, exact, no hype, no apology. Cite every claim to the tool or
patent that supports it. If something is uncertain, say so.
"""


class SmartBrain:
    def __init__(self, provider: LLMProvider):
        self.provider = provider

    async def handle(self, req: TaskRequest, bus: Bus) -> TaskResult:
        t0 = time.time()
        await bus.publish_progress(ProgressEvent(req.task_id, "started",
                                                  f"Smart brain picked up: {req.intent}"))

        try:
            if req.intent in {"retrosynthesis", "free_text"} and req.context.get("target_smiles"):
                return await self._handle_retrosynthesis(req, bus, t0)

            # Generic chat fallback — still run through the LLM but with the smart-brain prompt
            await bus.publish_progress(ProgressEvent(req.task_id, "thinking", "Smart brain composing answer"))
            text = await self.provider.chat([
                {"role": "system", "content": SMART_SYSTEM},
                {"role": "user", "content": req.user_text},
            ])
            return TaskResult(req.task_id, True, text,
                              {"intent": req.intent},
                              elapsed_s=round(time.time() - t0, 2))
        except asyncio.CancelledError:
            await bus.publish_progress(ProgressEvent(req.task_id, "cancelled", "Smart brain task cancelled"))
            raise
        except Exception as e:
            await bus.publish_progress(ProgressEvent(req.task_id, "error", f"Smart brain error: {e}"))
            return TaskResult(req.task_id, False, f"Error: {e}",
                              {"intent": req.intent}, elapsed_s=round(time.time() - t0, 2))

    async def _handle_retrosynthesis(self, req: TaskRequest, bus: Bus, t0: float) -> TaskResult:
        target = req.context["target_smiles"]
        budget = int(req.context.get("max_routes", 10))

        await bus.publish_progress(ProgressEvent(req.task_id, "planning",
                                                  f"Generating up to {budget} routes for {target}"))
        routes = plan_retrosynthesis(target, max_routes=budget)
        await bus.publish_progress(ProgressEvent(req.task_id, "planning",
                                                  f"Got {len(routes)} candidate routes"))

        # Symbolic verification
        verified: list[dict[str, Any]] = []
        for i, r in enumerate(routes, 1):
            sc = score_route_feasibility(r["steps"])
            r["feasibility"] = sc.data
            r["feasibility_score"] = sc.data["score"]
            verified.append(r)
            await bus.publish_progress(ProgressEvent(
                req.task_id, "verifying",
                f"Route {i}/{len(routes)} feasibility={sc.data['score']}"
            ))

        # FTO + Pareto scoring
        await bus.publish_progress(ProgressEvent(req.task_id, "fto_scoring", "Running FTO + multi-objective scoring"))
        scored = score_routes(verified, target_smiles=target,
                              regulatory_market=req.context.get("market", "IN+US+EU"))
        top = scored[:3]

        # Compose final summary
        await bus.publish_progress(ProgressEvent(req.task_id, "drafting", "Composing final summary"))
        bullets = []
        for r in top:
            bullets.append(
                f"- Route {r['route_id']}: feasibility={r.get('feasibility_score', 0):.2f}, "
                f"FTO_risk={r['scores']['fto_risk']:.2f}, "
                f"yield_est={r['scores']['yield_est']:.2f}, "
                f"impurity_risk={r['scores']['impurity_risk']:.2f}, "
                f"scalability={r['scores']['scalability']:.2f}, "
                f"composite={r['composite']:.2f}"
            )
        summary_input = (
            f"Target: {target}\n"
            f"Verified {len(verified)} routes; top 3 after multi-objective scoring:\n"
            + "\n".join(bullets) +
            "\n\nWrite the chemist-facing summary."
        )
        text = await self.provider.chat([
            {"role": "system", "content": SMART_SYSTEM},
            {"role": "user", "content": summary_input},
        ])

        return TaskResult(
            req.task_id, True, text,
            detail={
                "intent": "retrosynthesis",
                "target_smiles": target,
                "n_routes_total": len(verified),
                "top_routes": top,
            },
            elapsed_s=round(time.time() - t0, 2),
        )
