"""SHIMS native engine — SHIMS-owned GGUF inference (provider ``native``).

SHIMS embeds the koboldcpp runtime as its internal inference core: spawned
as a SHIMS-owned child process with SHIMS-computed flags, talked to only over
an internal loopback OpenAI-compatible endpoint. No LM Studio/Ollama in the
default chat path when a native model is loaded.

Modules:
- ``discovery`` — GGUF discovery + pure-Python header parsing.
- ``tuning``    — hardware-aware launch plans (backend/layers/ctx/threads).
- ``runtime``   — koboldcpp child-process ownership (spawn/health/restart/kill).
- ``engine``    — NativeEngine singleton facade (chat raw/stream, health).
- ``budget``    — memory ledger + idle-unload policy hook for the orchestrator.
"""
from __future__ import annotations

from .discovery import discover_models, find_model, parse_gguf_header, pick_default_model
from .tuning import LaunchPlan, compute_launch_plan
from .runtime import NativeRuntime, build_launch_command, locate_runtime_command, probe_help
from .engine import NativeEngine, get_engine
from . import budget

__all__ = [
    "discover_models",
    "find_model",
    "parse_gguf_header",
    "pick_default_model",
    "LaunchPlan",
    "compute_launch_plan",
    "NativeRuntime",
    "build_launch_command",
    "locate_runtime_command",
    "probe_help",
    "NativeEngine",
    "get_engine",
    "budget",
]
