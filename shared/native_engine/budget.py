"""Memory ledger for the SHIMS native engine.

Tracks what the loaded model reserves (weights file size + computed KV bytes
for the configured context) against psutil-available free memory. Phase 5's
model orchestrator consumes ``report()`` to decide what else may load.

Idle-unload policy: ``SHIMS_NATIVE_IDLE_UNLOAD_MIN`` (minutes, default 0 =
never unload). When set, ``maybe_idle_unload(engine)`` unloads the model
after that many minutes without a chat call.
"""
from __future__ import annotations

import os
import threading
import time
from typing import Any

_lock = threading.Lock()
_loaded: dict[str, Any] | None = None


def reserve(model: str, weights_bytes: int, kv_bytes: int) -> None:
    """Record a loaded model's reservation. Called by the engine on start."""
    global _loaded
    with _lock:
        _loaded = {
            "model": model,
            "weights_bytes": int(weights_bytes),
            "kv_bytes": int(kv_bytes),
            "loaded_at": time.time(),
            "last_used": time.time(),
        }


def clear() -> None:
    """Drop the reservation. Called by the engine on stop/unload."""
    global _loaded
    with _lock:
        _loaded = None


def touch() -> None:
    """Mark the model as used (called on every chat request)."""
    with _lock:
        if _loaded is not None:
            _loaded["last_used"] = time.time()


def reserved_bytes() -> int:
    """Bytes reserved by the currently loaded model (weights + KV)."""
    with _lock:
        if _loaded is None:
            return 0
        return int(_loaded["weights_bytes"]) + int(_loaded["kv_bytes"])


def headroom_bytes() -> int:
    """psutil-available free memory minus this engine's reservation."""
    import psutil
    return max(0, int(psutil.virtual_memory().available) - reserved_bytes())


def idle_unload_minutes() -> int:
    try:
        return max(0, int(os.getenv("SHIMS_NATIVE_IDLE_UNLOAD_MIN", "0") or 0))
    except ValueError:
        return 0


def idle_seconds() -> float | None:
    with _lock:
        if _loaded is None:
            return None
        return time.time() - float(_loaded["last_used"])


def maybe_idle_unload(engine: Any) -> bool:
    """Unload the engine's model if it has been idle past the policy.

    Phase 5 hook: the orchestrator calls this on its housekeeping tick.
    Returns True when an unload happened. Policy 0 (default) = never unload.
    """
    minutes = idle_unload_minutes()
    if minutes <= 0:
        return False
    idle = idle_seconds()
    if idle is None or idle < minutes * 60:
        return False
    engine.unload()
    return True


def report() -> dict[str, Any]:
    """Point-in-time ledger snapshot for /api/native-engine/status."""
    import psutil
    vm = psutil.virtual_memory()
    with _lock:
        loaded = dict(_loaded) if _loaded is not None else None
    return {
        "model": loaded["model"] if loaded else "",
        "reserved_bytes": reserved_bytes(),
        "weights_bytes": int(loaded["weights_bytes"]) if loaded else 0,
        "kv_bytes": int(loaded["kv_bytes"]) if loaded else 0,
        "available_bytes": int(vm.available),
        "total_bytes": int(vm.total),
        "headroom_bytes": headroom_bytes(),
        "idle_unload_minutes": idle_unload_minutes(),
        "idle_seconds": round(idle_seconds() or 0.0, 1) if loaded else None,
    }
