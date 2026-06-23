"""
Central configuration. Everything is env-overridable; defaults work zero-conf.

The design rule: any backend (LLM, bus, vector store) has an in-process fallback
so the scaffold runs without Redis, without an LLM, without RDKit. Production
runs flip env vars to point at the real services.
"""
from __future__ import annotations
import os
from dataclasses import dataclass, field
from pathlib import Path

# Hook into SHIMS central config for path + LLM resolution
from shared.config import GENERATED_DIR, settings


def _env(key: str, default: str | None = None) -> str | None:
    v = os.environ.get(key)
    return v if v is not None and v != "" else default


def _envb(key: str, default: bool = False) -> bool:
    v = os.environ.get(key, "").lower()
    if v in {"1", "true", "yes", "on"}:
        return True
    if v in {"0", "false", "no", "off"}:
        return False
    return default


def _envi(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key, default))
    except (TypeError, ValueError):
        return default


@dataclass
class BrainCfg:
    """One LLM provider's connection settings."""
    url: str | None = None         # OpenAI-compatible base URL, e.g. http://localhost:11434/v1
    model: str | None = None       # e.g. "qwen2.5:14b-instruct-q4_K_M"
    api_key: str = "not-needed"    # local servers ignore this; cloud uses real key
    timeout_s: float = 120.0
    max_tokens: int = 2048
    temperature: float = 0.2


@dataclass
class Config:
    # --- workspace ---------------------------------------------------------
    workspace: Path = field(default_factory=lambda: GENERATED_DIR / "shims_chem")
    # --- brains ------------------------------------------------------------
    fast: BrainCfg = field(default_factory=lambda: BrainCfg(
        url=_env("SHIMS_FAST_BRAIN_URL") or (settings.ollama_base_url.rstrip("/") + "/v1" if settings.ai_provider == "ollama" else None),
        model=_env("SHIMS_FAST_BRAIN_MODEL", settings.ollama_model),
        api_key=_env("SHIMS_FAST_BRAIN_KEY", "not-needed"),
        max_tokens=_envi("SHIMS_FAST_MAX_TOKENS", 1024),
        temperature=float(_env("SHIMS_FAST_TEMP", "0.2")),
    ))
    smart: BrainCfg = field(default_factory=lambda: BrainCfg(
        url=_env("SHIMS_SMART_BRAIN_URL") or (settings.ollama_base_url.rstrip("/") + "/v1" if settings.ai_provider == "ollama" else None),
        model=_env("SHIMS_SMART_BRAIN_MODEL", settings.ollama_model),
        api_key=_env("SHIMS_SMART_BRAIN_KEY", "not-needed"),
        max_tokens=_envi("SHIMS_SMART_MAX_TOKENS", 4096),
        temperature=float(_env("SHIMS_SMART_TEMP", "0.3")),
    ))
    # --- bus ---------------------------------------------------------------
    bus_url: str | None = field(default_factory=lambda: _env("SHIMS_BUS_URL"))  # redis://...
    # --- features ----------------------------------------------------------
    allow_unverified_output: bool = field(default_factory=lambda: _envb("SHIMS_ALLOW_UNVERIFIED", False))
    enable_smart_brain: bool = field(default_factory=lambda: _envb("SHIMS_ENABLE_SMART", True))
    # --- server ------------------------------------------------------------
    host: str = field(default_factory=lambda: _env("SHIMS_HOST", "127.0.0.1") or "127.0.0.1")
    port: int = field(default_factory=lambda: _envi("SHIMS_PORT", 8765))

    def ensure_dirs(self) -> None:
        self.workspace.mkdir(parents=True, exist_ok=True)
        (self.workspace / "memory").mkdir(exist_ok=True)
        (self.workspace / "rag").mkdir(exist_ok=True)
        (self.workspace / "fto").mkdir(exist_ok=True)
        (self.workspace / "logs").mkdir(exist_ok=True)
        (self.workspace / "evolution").mkdir(exist_ok=True)
        (self.workspace / "edge").mkdir(exist_ok=True)


_cfg: Config | None = None


def get_config() -> Config:
    """Singleton so all modules see the same settings within a process."""
    global _cfg
    if _cfg is None:
        _cfg = Config()
        _cfg.ensure_dirs()
    return _cfg


def set_config(cfg: Config) -> None:
    """For tests."""
    global _cfg
    _cfg = cfg
    _cfg.ensure_dirs()
