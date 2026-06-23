"""Model capability detection for SHIMS.

Centralises which models are known to support tool calling, so the UI can
filter out chat-only models and only present agent-loop-safe choices.
"""
from __future__ import annotations

import os
import re
from typing import Any


# Model name substrings known to support native / reliable tool calling.
# Keep lower-case.  A model is considered tool-capable if its lower-cased name
# contains any of these patterns, or if a caller explicitly sets
# ``tool_capable=True`` in the model dict.
TOOL_CAPABLE_PATTERNS: tuple[str, ...] = (
    # Qwen family (strong local tool calling)
    "qwen2.5-coder",
    "qwen2.5",
    "qwen3",
    # Llama family (3.1/3.2 have native tool support)
    "llama3.1",
    "llama3.2",
    # Mistral family
    "mistral-nemo",
    "mistral-small",
    "mixtral",
    # Cohere / Microsoft
    "command-r",
    "phi4",
    # Major cloud families (all current API versions support tools)
    "claude",
    "gpt-",
    "o1",
    "o3",
    "gemini",
    "deepseek-chat",
    "deepseek-reasoner",
    "qwen-max",
    "qwen-plus",
    "qwen-turbo",
    "qwen-coder",
    "moonshot",
    "kimi-k2",
    "kimi-k2.7",
    "glm-5",
    "glm-4",
    # Chemistry specialist endpoints are *not* general tool callers; they are
    # handled separately via their own routers, so we do NOT include ChemDFM.
)

# Regex compiled once for speed.
_TOOL_CAPABLE_RE = re.compile(
    "|".join(re.escape(p) for p in TOOL_CAPABLE_PATTERNS), re.IGNORECASE
)


def is_tool_capable(name: str | None) -> bool:
    """Return True if the given model id/tag is known to support tool calling."""
    if not name:
        return False
    # Environment escape hatch: show every model regardless of capability.
    if os.getenv("SHIMS_SHOW_ALL_MODELS", "").strip().lower() in {"1", "true", "yes", "on"}:
        return True
    return bool(_TOOL_CAPABLE_RE.search(name))


def mark_tool_capable(models: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Annotate a list of model dicts with ``tool_capable: bool``.

    Existing ``tool_capable`` values are preserved.
    """
    for m in models:
        if "tool_capable" not in m:
            m["tool_capable"] = is_tool_capable(m.get("name") or m.get("model") or m.get("id"))
    return models


def filter_tool_capable(models: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return only the models known to be tool-capable."""
    return [m for m in mark_tool_capable(models) if m.get("tool_capable")]


def tool_capable_badge(name: str) -> str:
    """A short human-readable tag for a model's capability class."""
    low = (name or "").lower()
    if is_tool_capable(name):
        return "🛠 tool"
    if any(x in low for x in ("vision", "llava", "bakllava", "moondream")):
        return "👁 vision"
    if any(x in low for x in ("embed", "nomic", "minilm")):
        return "📎 embedding"
    return "💬 chat"
