"""STT transcript correction — use a small LLM to fix misheard / repetitive speech.

The corrector runs in parallel with normal brain processing. The brain turn handler
can await the result with a short timeout and substitute the corrected text before
the main model starts generating.
"""
from __future__ import annotations

import os
from typing import Any

from .ai import ask_ai, extract_json_maybe


def _default_model() -> tuple[str | None, str | None]:
    """Return (provider, model) defaults for the STT corrector.

    Prefer a small local model when available. The caller can override via env vars.
    """
    provider = os.getenv("SHIMS_STT_CORRECTOR_PROVIDER") or None
    model = os.getenv("SHIMS_STT_CORRECTOR_MODEL") or None
    return provider, model


_CORRECTION_SYSTEM = (
    "You are an expert speech-to-text post-correction model. "
    "Your only job is to clean up a raw ASR transcript. "
    "Fix misheard words, repetitive/stuttered words, filler words, and obvious nonsense. "
    "Preserve the speaker's original language and meaning. "
    "Do NOT add information the speaker did not imply. "
    "Return ONLY a JSON object with keys: corrected (string), changed (bool), confidence (float 0-1), explanation (short string)."
)


def _build_prompt(raw: str, context: str = "", language: str = "") -> str:
    parts = [f'Raw ASR transcript: "{raw}"']
    if language and language.lower() not in {"auto", "unknown", ""}:
        parts.append(f"Detected language: {language}")
    if context:
        parts.append(f"Recent conversation context:\n{context}")
    parts.append(
        "Return JSON only. Example: {\"corrected\":\"...\", \"changed\":true, \"confidence\":0.92, \"explanation\":\"Fixed misheard 'stupid' to 'setup' and removed repetition.\"}"
    )
    return "\n\n".join(parts)


async def correct_transcript(
    raw: str,
    *,
    context: str = "",
    language: str = "",
    provider: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Ask an LLM to correct a raw transcript. Returns a dict with corrected/changed/confidence/explanation."""
    raw = (raw or "").strip()
    if not raw:
        return {"ok": True, "corrected": "", "changed": False, "confidence": 0.0, "explanation": "empty input"}

    prov, mod = _default_model()
    provider = provider or prov
    model = model or mod

    try:
        result = await ask_ai(
            _build_prompt(raw, context=context, language=language),
            system=_CORRECTION_SYSTEM,
            provider=provider,
            model=model,
        )
    except Exception as exc:
        return {"ok": False, "corrected": raw, "changed": False, "confidence": 0.0, "explanation": f"corrector unavailable: {exc}"}

    text = (result.text or "").strip()
    parsed = extract_json_maybe(text)
    if parsed is None:
        # Some local models wrap JSON in markdown or extra chatter; try a forgiving fallback.
        text = text.strip("`").removeprefix("json").strip()
        parsed = extract_json_maybe(text)

    if not isinstance(parsed, dict):
        return {"ok": False, "corrected": raw, "changed": False, "confidence": 0.0, "explanation": "corrector did not return valid JSON"}

    corrected = str(parsed.get("corrected") or raw).strip()
    if not corrected:
        corrected = raw

    changed = bool(parsed.get("changed")) or corrected.lower() != raw.lower()
    try:
        confidence = max(0.0, min(1.0, float(parsed.get("confidence", 0.0))))
    except Exception:
        confidence = 0.0

    return {
        "ok": True,
        "corrected": corrected,
        "changed": changed,
        "confidence": confidence,
        "explanation": str(parsed.get("explanation") or "").strip(),
        "provider": result.provider,
        "model": result.model,
    }
