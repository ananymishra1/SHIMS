"""Resolve an agent role to a concrete (provider, model) pair.

SHIMS agents declare a ``preferred_model_role`` (e.g. ``coder``,
``chemistry``).  This module turns that role into an actual model/provider
using, in order:

1. A role-specific environment variable (``SHIMS_CODER_MODEL``, etc.).
2. The neural governor registry / model router, if available.
3. The global default provider/model.
"""
from __future__ import annotations

import os
from typing import Any

from .config import settings

# Role -> env var name.  A value like ``qwen2.5-coder:14b`` implies Ollama;
# a value like ``gpt-4o-mini`` implies OpenAI; prefixes like ``ollama:``,
# ``openai:``, ``anthropic:``, ``gemini:``, ``huggingface:`` can be used to
# disambiguate.
ROLE_ENV_VARS: dict[str, str] = {
    "router": "SHIMS_ROUTER_MODEL",
    "fast": "SHIMS_FAST_MODEL",
    "smart": "SHIMS_SMART_MODEL",
    "coder": "SHIMS_CODER_MODEL",
    "creative": "SHIMS_CREATIVE_MODEL",
    "chemistry": "SHIMS_CHEMISTRY_MODEL",
    "research": "SHIMS_RESEARCH_MODEL",
}

# Provider inference from model name prefixes / substrings.
_PROVIDER_HINTS: list[tuple[str, str]] = [
    ("anthropic", "claude-"),
    ("openai", "gpt-"),
    ("openai", "o1"),
    ("openai", "o3"),
    ("google", "gemini-"),
    ("kimi", "moonshot-"),
    ("kimi", "kimi-"),
    ("deepseek", "deepseek-"),
    ("qwen", "qwen-"),
    ("chemdfm", "chemdfm"),
    ("ollama", ":"),
    ("huggingface", "/"),
]


def _provider_from_model(model: str) -> str:
    low = model.lower()
    for provider, hint in _PROVIDER_HINTS:
        if hint in low:
            return provider
    return "ollama"


def _parse_model_env(value: str) -> tuple[str, str]:
    """Parse ``provider:model`` or plain ``model`` into (provider, model)."""
    value = value.strip()
    if ":" in value and not value.lower().startswith(("http:", "https:")):
        provider, _, model = value.partition(":")
        if provider.lower() in {
            "ollama", "openai", "anthropic", "gemini", "google",
            "kimi", "deepseek", "qwen", "huggingface", "chemdfm",
        }:
            return provider.lower(), model.strip()
    return _provider_from_model(value), value


def _default_model() -> tuple[str, str]:
    provider = os.getenv("SHIMS_AI_PROVIDER", settings.ai_provider or "ollama").strip()
    if provider == "ollama":
        return provider, os.getenv("SHIMS_OLLAMA_MODEL", settings.ollama_model or "llama3.2:latest")
    default = getattr(settings, f"{provider}_model", None)
    if not default:
        default = settings.ollama_model
    return provider, default or "llama3.2:latest"


def resolve_agent(agent_id: str) -> tuple[str, str, str]:
    """Resolve a registered SHIMS agent to a concrete model/provider."""
    from . import agent_registry
    agent = agent_registry.AGENTS.get(agent_id)
    if agent is None:
        return _default_model() + ("unknown_agent",)
    if agent.specialist_model_env:
        value = os.getenv(agent.specialist_model_env, "").strip()
        if value:
            provider, model = _parse_model_env(value)
            return provider, model, f"env:{agent.specialist_model_env}"
    return resolve_role(agent.preferred_model_role)


def resolve_role(role: str) -> tuple[str, str, str]:
    """Return ``(provider, model, reason)`` for a role.

    Roles are the strings stored in ``ShimsAgent.preferred_model_role`` or
    specialist names such as ``router`` / ``planner`` / ``coder``.
    """
    role = (role or "smart").lower().strip()
    env_var = ROLE_ENV_VARS.get(role)

    # 1. Role-specific env var.
    if env_var:
        value = os.getenv(env_var, "").strip()
        if value:
            provider, model = _parse_model_env(value)
            return provider, model, f"env:{env_var}"

    # 2. Global default (respect SHIMS_AI_PROVIDER before neural governor).
    provider, model = _default_model()
    if provider != "ollama":
        return provider, model, "default"

    # 3. Neural governor registry (best-effort) only when no explicit cloud default.
    try:
        from .neural_governor.model_router import IntentCategory, route_model
        intent_map: dict[str, IntentCategory] = {
            "coder": IntentCategory.CODE_GENERATION,
            "creative": IntentCategory.DOCUMENT_FORMAT,
            "smart": IntentCategory.RESEARCH,
            "research": IntentCategory.RESEARCH,
            "fast": IntentCategory.CONVERSATION,
            "router": IntentCategory.CONVERSATION,
            "chemistry": IntentCategory.RESEARCH,
        }
        decision = route_model(intent_map.get(role, IntentCategory.RESEARCH), prefer_free=True)
        return decision.provider, decision.model, "neural_governor"
    except Exception:
        pass

    # 4. Fall back to Ollama default.
    return provider, model, "default"
