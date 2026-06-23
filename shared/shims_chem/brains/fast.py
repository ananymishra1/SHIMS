"""
Fast brain.

Responsibilities:
  * Talk to the user (low latency).
  * Classify intent.
  * Run the symbolic verifier on inputs the user gives ("check this SMILES").
  * For hard work (full retrosynthesis, multi-objective FTO, deep optimization)
    dispatch to the smart brain via the bus and narrate progress while waiting.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import AsyncIterator

from .llm import LLMProvider

FAST_SYSTEM = """You are the fast brain of Shims Chem, an air-gapped chemistry R&D copilot
for a pharmaceutical / API manufacturer.

You are paired with a slower, smarter brain that handles deep reasoning in the
background. Your job is to:
  • respond instantly to the chemist,
  • call the symbolic verifier tools to check any structure or reaction the
    chemist or you propose,
  • when a request is deep (retrosynthesis, multi-objective FTO, condition
    optimization), dispatch it to the smart brain and narrate progress while
    waiting,
  • never invent chemistry — every claim about a molecule or reaction must
    have been verified by a tool.

Style: concise, scientific, plain prose. Never use cutesy phrasing. State
limitations honestly. If the verifier says something is invalid or hazardous,
say so plainly.
"""


@dataclass
class FastTurn:
    user: str
    reply: str
    classified_intent: str
    dispatched_task_id: str | None = None


class FastBrain:
    def __init__(self, provider: LLMProvider):
        self.provider = provider

    async def classify_intent(self, user_text: str) -> str:
        prompt = (
            "Classify the user's intent in one word from this set: "
            "retrosynthesis, hazard_check, impurity_analysis, condition_optimization, "
            "fto_analysis, free_text. "
            f"User said: {user_text!r}"
        )
        out = await self.provider.chat(
            [{"role": "system", "content": "You are an intent classifier."},
             {"role": "user", "content": prompt}]
        )
        out = out.strip().split()[0].rstrip(".,").lower() if out else "free_text"
        valid = {"retrosynthesis", "hazard_check", "impurity_analysis",
                 "condition_optimization", "fto_analysis", "free_text"}
        return out if out in valid else "free_text"

    async def narrate(self, recent_progress: list[str]) -> str:
        bullets = "\n".join(f"- {p}" for p in recent_progress[-5:])
        prompt = ("Narrate the smart brain's recent progress to the chemist in one short paragraph. "
                  "Be concrete; mention what's being verified. Recent:\n" + bullets)
        return await self.provider.chat(
            [{"role": "system", "content": FAST_SYSTEM},
             {"role": "user", "content": prompt}]
        )

    async def reply_stream(self, history: list[dict]) -> AsyncIterator[str]:
        msgs = [{"role": "system", "content": FAST_SYSTEM}, *history]
        async for tok in self.provider.stream(msgs):
            yield tok
