"""
LLM provider abstraction.

Real providers must speak the OpenAI Chat Completions wire format. This covers
nearly every local server (Ollama, llama.cpp's `llama-server`, vLLM, SGLang,
LM Studio, ktransformers' OpenAI server, oobabooga) AND the cloud APIs.

`StubProvider` is a deterministic offline provider used by tests, the demo,
and as the safety fallback when no URL is configured. It produces sensible
fixed responses for the intents the agent issues, so the whole pipeline runs
without any LLM at all.
"""
from __future__ import annotations
import asyncio
import json
import re
from abc import ABC, abstractmethod
from typing import AsyncIterator

from ..config import BrainCfg


class LLMProvider(ABC):
    @abstractmethod
    async def chat(self, messages: list[dict], **kwargs) -> str: ...

    @abstractmethod
    async def stream(self, messages: list[dict], **kwargs) -> AsyncIterator[str]:
        if False:
            yield ""   # type: ignore[unreachable]


class OpenAICompatProvider(LLMProvider):
    def __init__(self, cfg: BrainCfg) -> None:
        if not cfg.url:
            raise ValueError("OpenAICompatProvider needs a URL")
        try:
            import httpx                              # lazy: stub mode doesn't need it
        except ImportError as e:                      # pragma: no cover
            raise RuntimeError("httpx not installed; pip install httpx") from e
        self._httpx = httpx
        self.cfg = cfg
        self._client = httpx.AsyncClient(timeout=cfg.timeout_s)

    async def chat(self, messages: list[dict], **kwargs) -> str:
        body = {
            "model": kwargs.get("model", self.cfg.model),
            "messages": messages,
            "temperature": kwargs.get("temperature", self.cfg.temperature),
            "max_tokens": kwargs.get("max_tokens", self.cfg.max_tokens),
            "stream": False,
        }
        if "tools" in kwargs:
            body["tools"] = kwargs["tools"]
        headers = {"Authorization": f"Bearer {self.cfg.api_key}", "Content-Type": "application/json"}
        r = await self._client.post(f"{self.cfg.url}/chat/completions", json=body, headers=headers)
        r.raise_for_status()
        data = r.json()
        return data["choices"][0]["message"]["content"] or ""

    async def stream(self, messages: list[dict], **kwargs) -> AsyncIterator[str]:
        body = {
            "model": kwargs.get("model", self.cfg.model),
            "messages": messages,
            "temperature": kwargs.get("temperature", self.cfg.temperature),
            "max_tokens": kwargs.get("max_tokens", self.cfg.max_tokens),
            "stream": True,
        }
        headers = {"Authorization": f"Bearer {self.cfg.api_key}", "Content-Type": "application/json"}
        async with self._client.stream("POST", f"{self.cfg.url}/chat/completions",
                                       json=body, headers=headers) as r:
            r.raise_for_status()
            async for line in r.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    return
                try:
                    obj = json.loads(payload)
                    delta = obj["choices"][0]["delta"].get("content")
                    if delta:
                        yield delta
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue


class StubProvider(LLMProvider):
    """
    Deterministic offline provider. Produces canned, reasonable answers based
    on simple cues in the system/user messages. Good enough for the demo and
    for the test suite that drives the orchestrator end-to-end.
    """

    def __init__(self, role: str = "stub") -> None:
        self.role = role

    @staticmethod
    def _last_user(messages: list[dict]) -> str:
        for m in reversed(messages):
            if m.get("role") == "user":
                return m.get("content", "") or ""
        return ""

    async def chat(self, messages: list[dict], **kwargs) -> str:
        user = self._last_user(messages).lower()
        await asyncio.sleep(0.01)  # simulate work
        if "intent" in user and "classify" in user:
            # The prompt lists all intent labels — restrict matching to the
            # user-quoted portion so we don't match the label set itself.
            m = re.search(r"user said:\s*['\"]?(.*?)['\"]?\s*$", user, re.DOTALL)
            target = (m.group(1) if m else user).lower()
            # Order matters: hazard / impurity / FTO are more specific cues than the generic synth verbs.
            ordered = [
                ("hazard", "hazard_check"),
                ("danger", "hazard_check"),
                ("explosi", "hazard_check"),
                ("pyrophor", "hazard_check"),
                ("impurity", "impurity_analysis"),
                ("ich q3", "impurity_analysis"),
                ("residual solvent", "impurity_analysis"),
                ("patent", "fto_analysis"),
                ("infring", "fto_analysis"),
                ("freedom to operate", "fto_analysis"),
                ("yield", "condition_optimization"),
                ("optimi", "condition_optimization"),
                ("condition", "condition_optimization"),
                ("doe", "condition_optimization"),
                ("retrosynth", "retrosynthesis"),
                ("how to make", "retrosynthesis"),
                ("synthesi", "retrosynthesis"),
                ("route to", "retrosynthesis"),
                ("prepare", "retrosynthesis"),
            ]
            for kw, label in ordered:
                if kw in target:
                    return label
            return "free_text"
        if "narrate" in user or "progress" in user:
            return "Smart brain is reasoning about the route. I'll update you as it makes progress."
        if "retrosynth" in user or "synthesi" in user:
            # Plausible scaffold response — the orchestrator will replace with real planner output
            return ("I'll work through this in steps:\n"
                    "1. Identify the target functional groups.\n"
                    "2. Disconnect at the most strategic bond.\n"
                    "3. Verify each step against atom balance and hazard rules.\n"
                    "4. Score routes on yield, cost, FTO, and scalability.")
        if "summari" in user or "summary" in user:
            return "Summary: the agent ran the symbolic verifier on every proposed step; routes failing balance or hazard checks were dropped."
        if "smiles" in user:
            m = re.search(r"[A-Za-z0-9@+\-\[\]\(\)=#./\\]{2,}", user)
            return f"I see a candidate SMILES `{m.group(0) if m else 'CCO'}`. Validating now."
        return f"[{self.role}] Acknowledged. I'll think through this and respond."

    async def stream(self, messages: list[dict], **kwargs) -> AsyncIterator[str]:
        text = await self.chat(messages, **kwargs)
        # Tokenize roughly by word for streaming feel
        for tok in re.findall(r"\S+\s*", text):
            await asyncio.sleep(0.005)
            yield tok


def get_provider(cfg: BrainCfg, role: str = "brain") -> LLMProvider:
    """Pick a real provider if URL is configured, else the stub."""
    if cfg.url:
        try:
            return OpenAICompatProvider(cfg)
        except Exception as e:
            print(f"[brain] WARNING: could not init {cfg.url}: {e}. Using stub.")
    return StubProvider(role=role)
