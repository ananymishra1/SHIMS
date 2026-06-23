"""Local Factory instance (Instance B) configuration.

This module centralises model selection for the isolated local SHIMS instance:
- fast/default: Qwen 2.5 3B (CPU-friendly)
- heavy / research: Qwen 2.5 7B
- chemistry specialist: ChemDFM via Ollama
- code: Qwen 2.5 Coder 14B

It is safe to import from Instance A; when not running as the factory instance
it simply returns the regular defaults.
"""
from __future__ import annotations

import os
from pathlib import Path

from .config import ROOT_DIR, settings, STORAGE_DIR

INSTANCE_ID = (os.getenv("SHIMS_INSTANCE_ID") or "").strip().lower()
FACTORY_MODE = os.getenv("SHIMS_FACTORY_MODE", "").strip().lower() in {"1", "true", "yes", "on"}


def is_factory_instance() -> bool:
    """True if this process is the isolated local factory instance."""
    return INSTANCE_ID == "local" or FACTORY_MODE


# ── model defaults ───────────────────────────────────────────────────────────
_FACTORY_DEFAULT_MODEL = "qwen2.5:3b"
_FACTORY_HEAVY_MODEL = "qwen2.5:7b"
_FACTORY_CHEMISTRY_MODEL = "chemdfm"
_FACTORY_CODER_MODEL = "qwen2.5-coder:14b"


def default_model() -> str:
    """Default conversational/model for Instance B."""
    if not is_factory_instance():
        return os.getenv("SHIMS_OLLAMA_MODEL", settings.ollama_model)
    return os.getenv("SHIMS_FACTORY_DEFAULT_MODEL", _FACTORY_DEFAULT_MODEL)


def heavy_model() -> str:
    """Larger model for reasoning/research when latency permits."""
    if not is_factory_instance():
        return os.getenv("SHIMS_HEAVY_MODEL", default_model())
    return os.getenv("SHIMS_FACTORY_HEAVY_MODEL", _FACTORY_HEAVY_MODEL)


def chemistry_model() -> str:
    """ChemDFM tag for chemistry questions."""
    if not is_factory_instance():
        return os.getenv("CHEMDFM_OLLAMA_TAG", "chemdfm")
    return os.getenv("SHIMS_FACTORY_CHEMISTRY_MODEL", _FACTORY_CHEMISTRY_MODEL)


def coder_model() -> str:
    """Code-generation model for Instance B."""
    if not is_factory_instance():
        return os.getenv("SHIMS_CODER_MODEL", settings.self_evolution_model)
    return os.getenv("SHIMS_FACTORY_CODER_MODEL", _FACTORY_CODER_MODEL)


def router_model() -> str:
    """Wave-planning router for Instance B."""
    if not is_factory_instance():
        return os.getenv("SHIMS_ROUTER_MODEL", default_model())
    return os.getenv("SHIMS_ROUTER_MODEL", default_model())


def resolve_role_model(role: str) -> str:
    """Pick a concrete Ollama tag for an agent role."""
    role = (role or "smart").lower().strip()
    mapping = {
        "router": router_model,
        "fast": default_model,
        "smart": default_model,
        "research": heavy_model,
        "heavy": heavy_model,
        "chemistry": chemistry_model,
        "chem": chemistry_model,
        "coder": coder_model,
        "code": coder_model,
    }
    return mapping.get(role, default_model)()


# ── storage layout ───────────────────────────────────────────────────────────
def factory_storage() -> Path:
    """Root storage for the factory instance (isolated from main storage)."""
    return Path(os.getenv("SHIMS_STORAGE_DIR", STORAGE_DIR)).resolve()


def corpus_dir() -> Path:
    d = factory_storage() / "corpus"
    d.mkdir(parents=True, exist_ok=True)
    return d


def training_dir() -> Path:
    d = factory_storage() / "training"
    d.mkdir(parents=True, exist_ok=True)
    return d


def models_dir() -> Path:
    d = factory_storage() / "models"
    d.mkdir(parents=True, exist_ok=True)
    return d


def evolution_dir() -> Path:
    d = factory_storage() / "evolution"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── peers ────────────────────────────────────────────────────────────────────
def peers_file() -> Path:
    """Shared peer configuration file (used by both instances)."""
    path = Path(os.getenv("SHIMS_PEERS_FILE", ROOT_DIR / "config" / "peers.json")).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def inter_instance_token() -> str:
    """Token used to authenticate peer-instance requests."""
    return os.getenv("INTER_INSTANCE_TOKEN", settings.bridge_token or "")
