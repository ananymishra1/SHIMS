"""Runtime mutable site state persisted outside of environment variables.

The .env file provides factory defaults. Site state (e.g. setup vs GMP phase)
can be changed at runtime by an administrator and survives restarts via a small
JSON file in SHIMS_STORAGE_DIR.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
STORAGE_DIR = Path(os.getenv('SHIMS_STORAGE_DIR', ROOT_DIR / 'storage')).resolve()

_STATE_PATH: Path = STORAGE_DIR / 'site_state.json'


def _load() -> dict[str, Any]:
    if _STATE_PATH.exists():
        try:
            with open(_STATE_PATH, 'r', encoding='utf-8') as fh:
                data = json.load(fh)
                if isinstance(data, dict):
                    return data
        except Exception:
            pass
    return {}


def _save(data: dict[str, Any]) -> None:
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    with open(_STATE_PATH, 'w', encoding='utf-8') as fh:
        json.dump(data, fh, indent=2, sort_keys=True)


def get(key: str, default: Any = None) -> Any:
    return _load().get(key, default)


def set_value(key: str, value: Any) -> None:
    data = _load()
    data[key] = value
    _save(data)


def all_values() -> dict[str, Any]:
    return _load().copy()


def current_site_phase() -> str:
    valid = {'setup', 'gmp'}
    phase = str(get('site_phase') or os.getenv('SHIMS_SITE_PHASE', 'setup')).strip().lower()
    return phase if phase in valid else 'setup'


def set_site_phase(phase: str) -> str:
    phase = str(phase).strip().lower()
    if phase not in {'setup', 'gmp'}:
        raise ValueError('phase must be setup or gmp')
    set_value('site_phase', phase)
    return phase


def current_manufacturing_mode() -> str:
    valid = {'api_only', 'formulation'}
    mode = str(get('manufacturing_mode') or os.getenv('SHIMS_MANUFACTURING_MODE', 'api_only')).strip().lower()
    return mode if mode in valid else 'api_only'


def set_manufacturing_mode(mode: str) -> str:
    mode = str(mode).strip().lower()
    if mode not in {'api_only', 'formulation'}:
        raise ValueError('mode must be api_only or formulation')
    set_value('manufacturing_mode', mode)
    return mode


def bridge_enabled() -> bool:
    """Return True if bridge should be enabled. Auto-enabled when a real bridge token is set."""
    explicit = get('bridge_enabled')
    if isinstance(explicit, bool):
        return explicit
    if isinstance(explicit, str):
        return explicit.lower() in {'1', 'true', 'yes', 'on'}
    # Auto-enable if a non-default bridge token is configured
    token = os.getenv('SHIMS_BRIDGE_TOKEN', '')
    return bool(token) and token != 'change-me-bridge-token'


def set_bridge_enabled(enabled: bool) -> None:
    set_value('bridge_enabled', bool(enabled))
