"""Model registry — metadata database for all supported AI models."""
from __future__ import annotations

from typing import Any, Optional

from . import HardwareProfile, ModelCapability, ModelInfo

# Registry of known models with hardware requirements and capabilities.
# This is NOT a hardcoded limit — users can add models dynamically.
_REGISTRY: list[ModelInfo] = []


def _seed_registry() -> None:
    """Seed with known models. This is called once at import."""
    global _REGISTRY
    if _REGISTRY:
        return

    # Ollama local models (GGUF)
    _REGISTRY.extend([
        ModelInfo("gemma3:1b", "ollama", 1.0, "Q4_K_M", 1.0, 2.0, ModelCapability(text=True, code=True, reasoning=True, speed_rating=5, quality_rating=2, offline_capable=True), aliases=["gemma3-1b", "gemma-1b"]),
        ModelInfo("gemma3:4b", "ollama", 4.0, "Q4_K_M", 3.0, 5.0, ModelCapability(text=True, code=True, reasoning=True, vision=True, speed_rating=4, quality_rating=3, offline_capable=True), aliases=["gemma3-4b", "gemma-4b"]),
        ModelInfo("gemma3:12b", "ollama", 12.0, "Q4_K_M", 8.0, 12.0, ModelCapability(text=True, code=True, reasoning=True, vision=True, speed_rating=3, quality_rating=4, offline_capable=True), aliases=["gemma3-12b", "gemma-12b"]),
        ModelInfo("gemma3:27b", "ollama", 27.0, "Q4_K_M", 18.0, 28.0, ModelCapability(text=True, code=True, reasoning=True, vision=True, speed_rating=3, quality_rating=5, offline_capable=True), aliases=["gemma3-27b", "gemma-27b"]),
        ModelInfo("qwen3:8b", "ollama", 8.0, "Q4_K_M", 5.5, 11.0, ModelCapability(text=True, code=True, reasoning=True, speed_rating=3, quality_rating=4, offline_capable=True)),
        ModelInfo("qwen3:14b", "ollama", 14.0, "Q4_K_M", 9.5, 19.0, ModelCapability(text=True, code=True, reasoning=True, speed_rating=2, quality_rating=5, offline_capable=True)),
        ModelInfo("qwen3:32b", "ollama", 32.0, "Q4_K_M", 20.0, 40.0, ModelCapability(text=True, code=True, reasoning=True, speed_rating=2, quality_rating=5, offline_capable=True)),
        ModelInfo("llama3.3:8b", "ollama", 8.0, "Q4_K_M", 5.5, 11.0, ModelCapability(text=True, code=True, reasoning=True, speed_rating=3, quality_rating=4, offline_capable=True)),
        ModelInfo("llama3.3:70b", "ollama", 70.0, "Q4_K_M", 44.0, 88.0, ModelCapability(text=True, code=True, reasoning=True, speed_rating=2, quality_rating=5, offline_capable=True)),
        ModelInfo("mistral:7b", "ollama", 7.0, "Q4_K_M", 5.0, 10.0, ModelCapability(text=True, code=True, reasoning=True, speed_rating=3, quality_rating=4, offline_capable=True)),
        ModelInfo("codellama:7b", "ollama", 7.0, "Q4_K_M", 5.0, 10.0, ModelCapability(text=True, code=True, reasoning=True, speed_rating=3, quality_rating=4, offline_capable=True)),
        ModelInfo("phi4:latest", "ollama", 14.0, "Q4_K_M", 9.0, 18.0, ModelCapability(text=True, code=True, reasoning=True, speed_rating=3, quality_rating=4, offline_capable=True)),
        ModelInfo("deepseek-r1:1.5b", "ollama", 1.5, "Q4_K_M", 1.2, 3.0, ModelCapability(text=True, code=True, reasoning=True, speed_rating=4, quality_rating=3, offline_capable=True)),
        ModelInfo("deepseek-r1:7b", "ollama", 7.0, "Q4_K_M", 5.0, 10.0, ModelCapability(text=True, code=True, reasoning=True, speed_rating=3, quality_rating=4, offline_capable=True)),
        ModelInfo("deepseek-r1:14b", "ollama", 14.0, "Q4_K_M", 9.0, 18.0, ModelCapability(text=True, code=True, reasoning=True, speed_rating=2, quality_rating=5, offline_capable=True)),
        ModelInfo("deepseek-coder-v2:16b", "ollama", 16.0, "Q4_K_M", 11.0, 22.0, ModelCapability(text=True, code=True, reasoning=True, speed_rating=3, quality_rating=5, offline_capable=True)),
        ModelInfo("command-r-plus:104b", "ollama", 104.0, "Q4_K_M", 65.0, 130.0, ModelCapability(text=True, code=True, reasoning=True, speed_rating=2, quality_rating=5, offline_capable=True)),
        ModelInfo("liquid-lfm2.5-230m", "ollama", 0.23, "Q4_0", 0.4, 1.0, ModelCapability(text=True, code=True, reasoning=True, speed_rating=5, quality_rating=3, offline_capable=True), aliases=["liquid-230m", "lfm-230m"]),
        ModelInfo("liquid-lfm2.5-1.2b", "ollama", 1.2, "Q4_0", 0.9, 2.0, ModelCapability(text=True, code=True, reasoning=True, speed_rating=5, quality_rating=4, offline_capable=True), aliases=["liquid-1.2b", "lfm-1.2b"]),
        ModelInfo("nomic-embed-text", "ollama", 0.14, "Q4_K_M", 0.3, 1.0, ModelCapability(text=True, speed_rating=5, quality_rating=3, offline_capable=True), aliases=["nomic-embed"]),
        # HuggingFace endpoint models (served locally via TGI/vLLM/llama.cpp server)
        ModelInfo("meta-llama/Llama-3.1-8B-Instruct", "huggingface", 8.0, "fp16", 6.0, 10.0, ModelCapability(text=True, code=True, reasoning=True, speed_rating=3, quality_rating=4, offline_capable=True), aliases=["llama-3.1-8b-instruct", "llama3.1-8b-hf"]),
        ModelInfo("Qwen/Qwen3-1.7B-Instruct", "huggingface", 1.7, "fp16", 1.5, 3.0, ModelCapability(text=True, code=True, reasoning=True, speed_rating=4, quality_rating=3, offline_capable=True), aliases=["qwen3-1.7b-instruct"]),
        ModelInfo("Qwen/Qwen3-7B-Instruct", "huggingface", 7.0, "fp16", 5.0, 10.0, ModelCapability(text=True, code=True, reasoning=True, speed_rating=3, quality_rating=4, offline_capable=True), aliases=["qwen3-7b-instruct-hf"]),
        ModelInfo("Qwen/Qwen3-14B-Instruct", "huggingface", 14.0, "fp16", 9.0, 18.0, ModelCapability(text=True, code=True, reasoning=True, speed_rating=2, quality_rating=5, offline_capable=True), aliases=["qwen3-14b-instruct-hf"]),
        ModelInfo("microsoft/Phi-4-mini-instruct", "huggingface", 3.8, "fp16", 3.0, 6.0, ModelCapability(text=True, code=True, reasoning=True, speed_rating=4, quality_rating=3, offline_capable=True), aliases=["phi-4-mini"]),
        ModelInfo("gemma-4-12b-it-abliterated", "transformers", 12.0, "Q4_K_M", 8.0, 12.0, ModelCapability(text=True, code=True, reasoning=True, speed_rating=2, quality_rating=4, offline_capable=True), aliases=["gemma-4-12b", "gemma4-full"]),
        ModelInfo("gemma-4-12b-abliterated", "ollama", 12.0, "Q3_K_M", 5.5, 10.0, ModelCapability(text=True, code=True, reasoning=True, speed_rating=2, quality_rating=4, offline_capable=True), aliases=["gemma4", "gemma-4", "gemma-4-12b"]),
    ])

    # Cloud models
    _REGISTRY.extend([
        ModelInfo("gpt-4o-mini", "openai", 8.0, "fp16", 0.0, 0.0, ModelCapability(text=True, code=True, reasoning=True, vision=True, speed_rating=4, quality_rating=4, offline_capable=False), cost_per_1k_tokens=0.00015),
        ModelInfo("gpt-4o", "openai", 80.0, "fp16", 0.0, 0.0, ModelCapability(text=True, code=True, reasoning=True, vision=True, multimodal=True, speed_rating=3, quality_rating=5, offline_capable=False), cost_per_1k_tokens=0.005),
        ModelInfo("gpt-4.1", "openai", 80.0, "fp16", 0.0, 0.0, ModelCapability(text=True, code=True, reasoning=True, vision=True, multimodal=True, speed_rating=3, quality_rating=5, offline_capable=False), cost_per_1k_tokens=0.005),
        ModelInfo("gpt-4.5-preview", "openai", 120.0, "fp16", 0.0, 0.0, ModelCapability(text=True, code=True, reasoning=True, vision=True, multimodal=True, speed_rating=2, quality_rating=5, offline_capable=False), cost_per_1k_tokens=0.075),
        ModelInfo("o3-mini", "openai", 30.0, "fp16", 0.0, 0.0, ModelCapability(text=True, code=True, reasoning=True, speed_rating=2, quality_rating=5, offline_capable=False), cost_per_1k_tokens=0.0011),
        ModelInfo("o4-mini", "openai", 30.0, "fp16", 0.0, 0.0, ModelCapability(text=True, code=True, reasoning=True, speed_rating=2, quality_rating=5, offline_capable=False), cost_per_1k_tokens=0.0015),
        ModelInfo("o1", "openai", 100.0, "fp16", 0.0, 0.0, ModelCapability(text=True, code=True, reasoning=True, speed_rating=2, quality_rating=5, offline_capable=False), cost_per_1k_tokens=0.015),
        ModelInfo("gemini-2.5-flash", "gemini", 25.0, "fp16", 0.0, 0.0, ModelCapability(text=True, code=True, reasoning=True, vision=True, audio=True, multimodal=True, speed_rating=5, quality_rating=4, offline_capable=False), cost_per_1k_tokens=0.00015),
        ModelInfo("gemini-2.5-pro", "gemini", 100.0, "fp16", 0.0, 0.0, ModelCapability(text=True, code=True, reasoning=True, vision=True, audio=True, multimodal=True, speed_rating=3, quality_rating=5, offline_capable=False), cost_per_1k_tokens=0.00125),
        ModelInfo("claude-sonnet-4-6", "anthropic", 70.0, "fp16", 0.0, 0.0, ModelCapability(text=True, code=True, reasoning=True, vision=True, speed_rating=3, quality_rating=5, offline_capable=False), cost_per_1k_tokens=0.003),
        ModelInfo("claude-opus-4-6", "anthropic", 100.0, "fp16", 0.0, 0.0, ModelCapability(text=True, code=True, reasoning=True, vision=True, speed_rating=2, quality_rating=5, offline_capable=False), cost_per_1k_tokens=0.015),
        ModelInfo("claude-haiku-4-6", "anthropic", 15.0, "fp16", 0.0, 0.0, ModelCapability(text=True, code=True, reasoning=True, speed_rating=5, quality_rating=3, offline_capable=False), cost_per_1k_tokens=0.00025),
        ModelInfo("deepseek-chat", "deepseek", 30.0, "fp16", 0.0, 0.0, ModelCapability(text=True, code=True, reasoning=True, speed_rating=4, quality_rating=4, offline_capable=False), cost_per_1k_tokens=0.00014),
        ModelInfo("kimi-k2.6", "kimi", 30.0, "fp16", 0.0, 0.0, ModelCapability(text=True, code=True, reasoning=True, speed_rating=3, quality_rating=4, offline_capable=False), cost_per_1k_tokens=0.00015),
        ModelInfo("kimi-k2.7", "kimi", 30.0, "fp16", 0.0, 0.0, ModelCapability(text=True, code=True, reasoning=True, speed_rating=3, quality_rating=5, offline_capable=False), cost_per_1k_tokens=0.0002),
    ])

    # Mobile / special
    _REGISTRY.extend([
        ModelInfo("gemma-nano", "mediapipe", 2.0, "int4", 1.5, 2.5, ModelCapability(text=True, code=True, speed_rating=4, quality_rating=2, offline_capable=True)),
        ModelInfo("llama-3.2-mobile", "llamacpp", 3.0, "Q4_0", 2.0, 4.0, ModelCapability(text=True, code=True, speed_rating=4, quality_rating=3, offline_capable=True)),
    ])


def get_registry() -> list[ModelInfo]:
    _seed_registry()
    return list(_REGISTRY)


def find_model(name: str) -> Optional[ModelInfo]:
    """Find model by exact name or alias."""
    _seed_registry()
    name_lower = name.lower().strip()
    for m in _REGISTRY:
        if m.name.lower() == name_lower or name_lower in [a.lower() for a in m.aliases]:
            return m
    return None


def list_compatible_models(hardware: HardwareProfile, require_offline: bool = False) -> list[ModelInfo]:
    """Return models that can run on this hardware."""
    _seed_registry()
    compatible = []
    for m in _REGISTRY:
        if require_offline and not m.capabilities.offline_capable:
            continue
        if not hardware.internet_available and not m.capabilities.offline_capable:
            continue
        if hardware.vram_gb > 0 and m.vram_required_gb > hardware.vram_gb * 1.1:
            continue
        if hardware.vram_gb == 0 and m.ram_required_gb > hardware.total_ram_gb * 0.8:
            continue
        compatible.append(m)
    return compatible


def add_model(info: ModelInfo) -> None:
    """Dynamically add a model to the registry at runtime."""
    _seed_registry()
    _REGISTRY.append(info)


def get_models_by_provider(provider: str) -> list[ModelInfo]:
    _seed_registry()
    return [m for m in _REGISTRY if m.provider.lower() == provider.lower()]


def to_dict_list() -> list[dict[str, Any]]:
    return [m.to_dict() for m in get_registry()]
