"""Kimi model name normalization and fallback helpers.

Prevents common user mistakes like typing 'k2.6' instead of 'kimi-k2.6',
and provides automatic fallback chains when a model returns 404.
"""
from __future__ import annotations


# Aliases that users commonly type without the kimi- prefix.
KIMI_MODEL_ALIASES: dict[str, str] = {
    "k2.6": "kimi-k2.6",
    "k2.5": "kimi-k2.5",
    "k2": "kimi-k2-0711-preview",
    "k2-0711": "kimi-k2-0711-preview",
    "k2-0711-preview": "kimi-k2-0711-preview",
    "8k": "moonshot-v1-8k",
    "32k": "moonshot-v1-32k",
    "128k": "moonshot-v1-128k",
    "moonshot-8k": "moonshot-v1-8k",
    "moonshot-32k": "moonshot-v1-32k",
    "moonshot-128k": "moonshot-v1-128k",
}

# Fallback chain when a model returns 404. Each entry is tried in order.
KIMI_FALLBACK_CHAIN: list[str] = [
    "kimi-k2.6",
    "kimi-k2.5",
    "kimi-k2-0711-preview",
    "moonshot-v1-128k",
    "moonshot-v1-32k",
    "moonshot-v1-8k",
]


def normalize_kimi_model(name: str | None) -> str:
    """Return a canonical Kimi model name from user input.

    Expands common shorthand aliases and ensures the 'kimi-' prefix
    for K2.x models.  Unknown names are returned as-is so the API
    can report its own error.
    """
    if not name:
        return "moonshot-v1-8k"
    stripped = name.strip().lower()
    if stripped in KIMI_MODEL_ALIASES:
        return KIMI_MODEL_ALIASES[stripped]
    # If user typed e.g. "k2.6" without the prefix, add it.
    if stripped.startswith("k2") and not stripped.startswith("kimi-"):
        return f"kimi-{stripped}"
    return stripped


def kimi_fallback_chain(start_model: str) -> list[str]:
    """Return a list of fallback models to try on 404.

    The start_model is tried first; if it fails, the remaining
    models in the chain are tried in order.
    """
    canonical = normalize_kimi_model(start_model)
    try:
        idx = KIMI_FALLBACK_CHAIN.index(canonical)
    except ValueError:
        # Unknown model — prepend it so the API error is preserved,
        # then try known fallbacks.
        return [canonical] + KIMI_FALLBACK_CHAIN
    return KIMI_FALLBACK_CHAIN[idx:]
