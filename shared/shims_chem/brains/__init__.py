"""
The dual-brain layer.

  llm.py       — OpenAI-compatible LLM provider (+ deterministic stub for tests)
  fast.py      — fast brain: dialogue, intent classification, narration
  smart.py     — smart brain: deep retrosynthesis + condition reasoning
  dispatcher.py — wires the brains to the bus; runs in two flavors:
                  embedded (single process) or worker (separate process)
"""
from .llm import LLMProvider, OpenAICompatProvider, StubProvider, get_provider
from .fast import FastBrain
from .smart import SmartBrain
from .dispatcher import Dispatcher, classify_intent

__all__ = [
    "LLMProvider", "OpenAICompatProvider", "StubProvider", "get_provider",
    "FastBrain", "SmartBrain", "Dispatcher", "classify_intent",
]
