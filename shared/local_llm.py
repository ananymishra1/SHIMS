"""Feature-level local LLM helper — the single native-only chat path.

The SHIMS native engine (embedded koboldcpp runtime, OpenAI-compatible
loopback, default port 5115) is SHIMS's only local LLM route. Ollama is no
longer called by default anywhere in ``shared/*``; cloud providers remain
untouched and keep their own transports.

``feature_chat`` is the small sync facade used by background "feature" brains
(improvement loop, prompt evolution, desktop planner, swarm orchestrator, …).
It never raises and returns ``""`` on any failure so every caller can fall
back to its heuristic path.
"""
from __future__ import annotations

import os

import httpx

from .native_engine import get_engine


def _default_timeout() -> float:
    try:
        return float(os.getenv("SHIMS_FEATURE_LLM_TIMEOUT_S", "180"))
    except ValueError:
        return 180.0


def _base_url() -> str:
    try:
        port = int(os.getenv("SHIMS_NATIVE_PORT", "5115") or 5115)
    except ValueError:
        port = 5115
    return f"http://127.0.0.1:{port}"


def feature_chat(messages: list[dict], *, model: str | None = None,
                 max_tokens: int = 1024, temperature: float = 0.3,
                 timeout: float | None = None, feature: str = "") -> str:
    """Send an OpenAI-compatible chat request to the native engine loopback.

    ``model`` is a GGUF id / filename stem; when given, it is loaded first via
    ``ensure_loaded``. When ``model`` is ``None`` the currently loaded model is
    used. Returns the assistant content string, or ``""`` on ANY failure
    (engine down, load failed, timeout, bad JSON) — callers have heuristic
    fallbacks. Never raises.
    """
    try:
        engine = get_engine()
        if model:
            engine.ensure_loaded(model)
        health = engine.health()
        if not (health.get("ready") and health.get("running")):
            return ""
        served_model = health.get("model") or model or "native"
        payload = {
            "model": served_model,
            "messages": messages,
            "stream": False,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        with httpx.Client(timeout=timeout or _default_timeout()) as client:
            r = client.post(f"{_base_url()}/v1/chat/completions", json=payload)
            r.raise_for_status()
            data = r.json()
        choice = (data.get("choices") or [{}])[0]
        content = ((choice.get("message") or {}).get("content") or "")
        return str(content).strip()
    except Exception:
        return ""
