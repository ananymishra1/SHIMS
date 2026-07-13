"""Bus factory: pick Redis if SHIMS_BUS_URL is set, else in-process."""
from __future__ import annotations

from ..config import get_config
from .inproc import InProcessBus
from .types import Bus


def make_bus() -> Bus:
    cfg = get_config()
    if cfg.bus_url and cfg.bus_url.startswith("redis://"):
        try:
            from .redis_bus import RedisBus
            return RedisBus(cfg.bus_url)
        except RuntimeError as e:
            # Redis module missing — fall back loudly
            print(f"[bus] WARNING: {e}. Falling back to in-process bus.")
    return InProcessBus()
