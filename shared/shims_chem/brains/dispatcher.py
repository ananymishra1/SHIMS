"""
The orchestrator.

Two modes:

  * embedded() — single-process: fast brain handles each turn; if the work is
    deep, it submits a task to the bus and a background task on the SAME
    process picks it up via the smart brain. Used by the demo and dev loop.

  * worker_loop() — separate process: a smart-brain worker pulls from the
    bus indefinitely. Run with `shims-chem smart-worker`.

The fast brain narrates by subscribing to the progress stream of the dispatched
task and ferrying events back to the UI.
"""
from __future__ import annotations
import asyncio
import uuid
from dataclasses import dataclass
from typing import AsyncIterator

from ..bus import Bus, ProgressEvent, TaskRequest, TaskResult
from ..config import get_config
from ..verifier import run_tool
from .fast import FastBrain
from .smart import SmartBrain
from .llm import get_provider


_FAST_INTENTS_NO_DISPATCH = {"hazard_check"}   # fast brain can handle these alone
_NEEDS_SMART = {"retrosynthesis", "fto_analysis", "condition_optimization", "impurity_analysis"}


def classify_intent_heuristic(user_text: str) -> str:
    """Deterministic fallback classifier — works without any LLM."""
    s = user_text.lower()
    if any(k in s for k in ("hazard", "danger", "explosi", "pyrophor", "toxic")):
        return "hazard_check"
    if any(k in s for k in ("impurity", "ich q3", "residual solvent")):
        return "impurity_analysis"
    if any(k in s for k in ("patent", "fto", "freedom to operate", "infring")):
        return "fto_analysis"
    if any(k in s for k in ("optimi", "yield", "condition", "doe")):
        return "condition_optimization"
    if any(k in s for k in ("retrosynth", "how to make", "how do i synthesize",
                             "route to", "make the molecule", "synthesize",
                             "synthesis of", "prepare")):
        return "retrosynthesis"
    return "free_text"


async def classify_intent(fast: FastBrain, user_text: str) -> str:
    """Try LLM classifier; fall back to heuristic on failure / empty result."""
    try:
        out = await asyncio.wait_for(fast.classify_intent(user_text), timeout=5.0)
        return out
    except Exception:
        return classify_intent_heuristic(user_text)


@dataclass
class TurnResult:
    user: str
    intent: str
    reply: str
    task_id: str | None = None       # set if dispatched to smart brain
    progress: list[str] | None = None


class Dispatcher:
    def __init__(self, bus: Bus) -> None:
        cfg = get_config()
        self.bus = bus
        self.fast = FastBrain(get_provider(cfg.fast, role="fast"))
        self.smart = SmartBrain(get_provider(cfg.smart, role="smart"))
        self._smart_worker_task: asyncio.Task | None = None

    async def start_embedded_smart_worker(self) -> None:
        """Run the smart-brain consumer loop inside this process."""
        if self._smart_worker_task is not None:
            return
        self._smart_worker_task = asyncio.create_task(self._smart_loop())

    async def _smart_loop(self) -> None:
        async for req in self.bus.consume_tasks(consumer=f"embedded-{uuid.uuid4().hex[:8]}"):
            result = await self.smart.handle(req, self.bus)
            await self.bus.publish_result(result)

    async def stop(self) -> None:
        if self._smart_worker_task is not None:
            self._smart_worker_task.cancel()
            try:
                await self._smart_worker_task
            except asyncio.CancelledError:
                pass
        await self.bus.close()

    async def handle_turn(self, user_text: str,
                          context: dict | None = None) -> TurnResult:
        """One full user turn — classify, route, return."""
        intent = await classify_intent(self.fast, user_text)
        ctx = context or {}

        # Direct, fast-brain-only intents
        if intent in _FAST_INTENTS_NO_DISPATCH:
            smiles = ctx.get("smiles") or _maybe_extract_smiles(user_text)
            if smiles:
                tr = run_tool("flag_hazards", smiles=smiles)
                reply = _format_hazard_reply(smiles, tr)
                return TurnResult(user_text, intent, reply)

        # Deep intents → dispatch to smart brain
        if intent in _NEEDS_SMART:
            task_id = uuid.uuid4().hex
            req = TaskRequest(
                task_id=task_id,
                user_text=user_text,
                intent=intent,
                context={**ctx, "target_smiles": ctx.get("smiles") or _maybe_extract_smiles(user_text)},
            )
            await self.bus.submit_task(req)
            reply = (f"Dispatched to the smart brain as task `{task_id}`. "
                     "I'll narrate progress as it works.")
            return TurnResult(user_text, intent, reply, task_id=task_id)

        # Free-text — fast brain answers directly
        full = ""
        async for tok in self.fast.reply_stream(
            [{"role": "user", "content": user_text}]
        ):
            full += tok
        return TurnResult(user_text, intent, full.strip() or "(no response)")

    async def stream_progress(self, task_id: str) -> AsyncIterator[ProgressEvent]:
        async for ev in self.bus.subscribe_progress(task_id):
            yield ev

    async def wait_result(self, task_id: str, timeout_s: float = 120.0) -> TaskResult | None:
        return await self.bus.get_result(task_id, timeout_s=timeout_s)


# ----- helpers --------------------------------------------------------------

import re as _re

_SMILES_RE = _re.compile(
    r"(?<![A-Za-z0-9])([A-Za-z0-9@+\-\[\]\(\)=#./\\%]{2,})(?![A-Za-z0-9])"
)


def _maybe_extract_smiles(text: str) -> str | None:
    """Very loose SMILES sniffing — meant to catch user-quoted SMILES."""
    # Prefer back-tick or quote-fenced strings
    for m in _re.finditer(r"`([^`]+)`|\"([^\"]+)\"|'([^']+)'", text):
        for g in m.groups():
            if g and any(ch.isupper() for ch in g) and any(ch.isalpha() for ch in g):
                return g.strip()
    # Fallback: words containing parentheses or =
    for m in _SMILES_RE.finditer(text):
        s = m.group(1)
        if any(c in s for c in "()=#[]") and any(c.isalpha() for c in s):
            return s
    return None


def _format_hazard_reply(smiles: str, tr) -> str:
    if not tr.issues:
        return f"`{smiles}` — no hazard rules fired."
    lines = [f"Hazard screen for `{smiles}`:"]
    for i in tr.issues:
        lines.append(f"  • [{i.severity.upper()}] {i.code}: {i.message}")
        adv = i.detail.get("advice") if isinstance(i.detail, dict) else None
        if adv:
            lines.append(f"      ↳ {adv}")
    return "\n".join(lines)
