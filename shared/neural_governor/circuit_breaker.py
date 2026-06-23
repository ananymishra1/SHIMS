"""Circuit breaker — auto-disable failing providers."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Optional

from .event_bus import publish


@dataclass
class CircuitState:
    provider: str
    status: str = "closed"  # closed, open, half_open
    failures: int = 0
    successes: int = 0
    last_failure: float = 0.0
    last_success: float = 0.0
    opened_at: Optional[float] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "status": self.status,
            "failures": self.failures,
            "successes": self.successes,
            "last_failure": self.last_failure,
            "last_success": self.last_success,
            "opened_at": self.opened_at,
        }


# In-memory circuit states
_circuits: dict[str, CircuitState] = {}

FAILURE_THRESHOLD = 5
SUCCESS_THRESHOLD = 3
HALF_OPEN_TIMEOUT = 300  # 5 minutes


def _get_circuit(provider: str) -> CircuitState:
    provider = provider.lower().strip()
    if provider not in _circuits:
        _circuits[provider] = CircuitState(provider=provider)
    return _circuits[provider]


def record_success(provider: str) -> None:
    c = _get_circuit(provider)
    c.successes += 1
    c.last_success = time.time()
    if c.status == "half_open" and c.successes >= SUCCESS_THRESHOLD:
        c.status = "closed"
        c.failures = 0
        publish("circuit_breaker.closed", {"provider": provider})


def record_failure(provider: str) -> None:
    c = _get_circuit(provider)
    c.failures += 1
    c.last_failure = time.time()
    if c.status == "half_open":
        c.status = "open"
        c.opened_at = time.time()
        publish("circuit_breaker.opened", {"provider": provider})
    elif c.status == "closed" and c.failures >= FAILURE_THRESHOLD:
        c.status = "open"
        c.opened_at = time.time()
        publish("circuit_breaker.opened", {"provider": provider})


def can_use(provider: str) -> bool:
    c = _get_circuit(provider)
    if c.status == "closed":
        return True
    if c.status == "open":
        if c.opened_at and time.time() - c.opened_at > HALF_OPEN_TIMEOUT:
            c.status = "half_open"
            c.successes = 0
            publish("circuit_breaker.half_open", {"provider": provider})
            return True
        return False
    return True  # half_open allows one through


def get_all_circuits() -> list[dict[str, Any]]:
    return [c.to_dict() for c in _circuits.values()]


def reset_circuit(provider: str) -> None:
    if provider in _circuits:
        _circuits[provider] = CircuitState(provider=provider)
