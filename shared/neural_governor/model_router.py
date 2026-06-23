"""Hardware-aware model router — selects optimal provider/model for each task."""
from __future__ import annotations

import time
from typing import Any, Optional

from . import HardwareProfile, IntentCategory, ModelCapability, RoutingDecision
from .hardware_profiler import profile_hardware
from .model_registry import find_model, get_registry, list_compatible_models

# Intent -> preferred capabilities mapping
INTENT_CAPABILITY_PRIORITY: dict[IntentCategory, list[str]] = {
    IntentCategory.CODE_GENERATION: ["code", "reasoning", "text"],
    IntentCategory.DOCUMENT_FORMAT: ["text", "creativity", "reasoning"],
    IntentCategory.DOCUMENT_INGEST: ["text", "speed_rating"],
    IntentCategory.DATA_ANALYSIS: ["reasoning", "code", "text"],
    IntentCategory.MANUFACTURING_QUERY: ["text", "reasoning"],
    IntentCategory.EQUIPMENT_QUERY: ["text", "reasoning"],
    IntentCategory.QUALITY_CONTROL: ["text", "reasoning"],
    IntentCategory.MULTIMODAL: ["multimodal", "vision", "audio"],
    IntentCategory.SYSTEM_COMMAND: ["code", "text", "speed_rating"],
    IntentCategory.RESEARCH: ["reasoning", "text", "creativity"],
    IntentCategory.ADMIN: ["text", "reasoning"],
    IntentCategory.CONVERSATION: ["text", "speed_rating"],
    IntentCategory.UNKNOWN: ["text"],
}

# Cached hardware profile (refreshed every 60s)
_cached_hw: Optional[HardwareProfile] = None
_cached_at: float = 0.0


def _get_hardware() -> HardwareProfile:
    global _cached_hw, _cached_at
    now = time.time()
    if _cached_hw is None or now - _cached_at > 60:
        _cached_hw = profile_hardware()
        _cached_at = now
    return _cached_hw


def _score_model_for_task(
    model: Any,  # ModelInfo
    intent: IntentCategory,
    hw: HardwareProfile,
    prefer_free: bool = True,
    prefer_speed: bool = False,
) -> float:
    """Score a model for a task. Higher is better."""
    cap: ModelCapability = model.capabilities
    score = 0.0

    # Capability match
    priorities = INTENT_CAPABILITY_PRIORITY.get(intent, ["text"])
    for idx, cap_name in enumerate(priorities):
        weight = 1.0 - (idx * 0.15)
        if cap_name == "code" and cap.code:
            score += weight
        elif cap_name == "reasoning" and cap.reasoning:
            score += weight
        elif cap_name == "creativity" and cap.creativity:
            score += weight
        elif cap_name == "vision" and cap.vision:
            score += weight
        elif cap_name == "audio" and cap.audio:
            score += weight
        elif cap_name == "multimodal" and cap.multimodal:
            score += weight
        elif cap_name == "text" and cap.text:
            score += weight
        elif cap_name == "speed_rating":
            score += (cap.speed_rating / 5.0) * weight

    # Cost preference
    if prefer_free and model.cost_per_1k_tokens == 0.0:
        score += 0.5
    elif not prefer_free and model.cost_per_1k_tokens > 0:
        score += 0.3  # willing to pay for quality

    # Speed preference
    if prefer_speed:
        score += (cap.speed_rating / 5.0) * 0.4

    # Quality for non-speed intents
    if intent not in {IntentCategory.CONVERSATION, IntentCategory.DOCUMENT_INGEST}:
        score += (cap.quality_rating / 5.0) * 0.3

    # Hardware fit bonus
    if hw.vram_gb > 0 and model.vram_required_gb > 0:
        utilization = model.vram_required_gb / hw.vram_gb
        if 0.2 <= utilization <= 0.7:
            score += 0.2  # sweet spot
        elif utilization > 0.9:
            score -= 0.3  # too tight
    elif hw.vram_gb == 0 and model.ram_required_gb > 0:
        utilization = model.ram_required_gb / hw.total_ram_gb
        if utilization > 0.8:
            score -= 0.3

    # Offline requirement
    if not hw.internet_available and not cap.offline_capable:
        score -= 10.0  # heavily penalize

    return score


def route_model(
    intent: IntentCategory,
    provider_preference: Optional[str] = None,
    model_preference: Optional[str] = None,
    allowed_providers: Optional[list[str]] = None,
    prefer_free: bool = True,
    prefer_speed: bool = False,
    force_local: bool = False,
) -> RoutingDecision:
    """Select the best model for a task given current hardware and preferences."""
    hw = _get_hardware()

    # If user has a specific preference and it's compatible, honor it
    if model_preference:
        m = find_model(model_preference)
        if m:
            if allowed_providers and m.provider not in allowed_providers:
                pass  # not allowed, continue
            elif force_local and not m.capabilities.offline_capable:
                pass
            elif not hw.internet_available and not m.capabilities.offline_capable:
                pass
            else:
                return RoutingDecision(
                    provider=m.provider,
                    model=m.name,
                    reason="user_preference",
                    fallback_chain=_build_fallbacks(intent, hw, allowed_providers, prefer_free),
                    confidence=1.0,
                )

    # If provider preference only
    if provider_preference and not model_preference:
        candidates = [m for m in get_registry() if m.provider.lower() == provider_preference.lower()]
        candidates = _filter_candidates(candidates, hw, allowed_providers, force_local)
        if candidates:
            best = max(candidates, key=lambda m: _score_model_for_task(m, intent, hw, prefer_free, prefer_speed))
            return RoutingDecision(
                provider=best.provider,
                model=best.name,
                reason=f"provider_preference:{provider_preference}",
                fallback_chain=_build_fallbacks(intent, hw, allowed_providers, prefer_free),
                confidence=0.9,
            )

    # Auto-select from compatible models
    candidates = list_compatible_models(hw, require_offline=force_local or not hw.internet_available)
    candidates = _filter_candidates(candidates, hw, allowed_providers, force_local)

    if not candidates:
        # Emergency fallback
        return RoutingDecision(
            provider="ollama",
            model="gemma3:1b",
            reason="emergency_fallback_no_compatible_models",
            fallback_chain=[],
            confidence=0.1,
        )

    best = max(candidates, key=lambda m: _score_model_for_task(m, intent, hw, prefer_free, prefer_speed))
    return RoutingDecision(
        provider=best.provider,
        model=best.name,
        reason=f"auto_routed:{intent.value}",
        fallback_chain=_build_fallbacks(intent, hw, allowed_providers, prefer_free, exclude=best.name),
        confidence=0.85,
    )


def _filter_candidates(
    candidates: list[Any],
    hw: HardwareProfile,
    allowed_providers: Optional[list[str]],
    force_local: bool,
) -> list[Any]:
    if allowed_providers:
        candidates = [m for m in candidates if m.provider in allowed_providers]
    if force_local:
        candidates = [m for m in candidates if m.capabilities.offline_capable]
    if not hw.internet_available:
        candidates = [m for m in candidates if m.capabilities.offline_capable]
    return candidates


def _build_fallbacks(
    intent: IntentCategory,
    hw: HardwareProfile,
    allowed_providers: Optional[list[str]],
    prefer_free: bool,
    exclude: Optional[str] = None,
) -> list[dict[str, str]]:
    """Build ordered fallback chain (provider, model)."""
    candidates = list_compatible_models(hw)
    candidates = _filter_candidates(candidates, hw, allowed_providers, force_local=False)
    if exclude:
        candidates = [m for m in candidates if m.name != exclude]
    candidates.sort(key=lambda m: _score_model_for_task(m, intent, hw, prefer_free, prefer_speed=False), reverse=True)
    return [{"provider": m.provider, "model": m.name} for m in candidates[:3]]


def get_router_status() -> dict[str, Any]:
    """Diagnostic status for the dashboard."""
    hw = _get_hardware()
    compatible = list_compatible_models(hw)
    return {
        "hardware": hw.to_dict(),
        "compatible_models": len(compatible),
        "total_models": len(get_registry()),
        "cached_at": _cached_at,
    }
