from __future__ import annotations

import os
import time
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from .config import settings


class InMemoryRateLimiter:
    def __init__(self, max_requests: int, window_seconds: int) -> None:
        self.max_requests = max_requests
        self.window = window_seconds
        self._store: dict[str, list[float]] = {}

    def is_allowed(self, key: str) -> bool:
        now = time.time()
        window_start = now - self.window
        timestamps = self._store.get(key, [])
        # Keep only timestamps within the current window
        timestamps = [t for t in timestamps if t > window_start]
        allowed = len(timestamps) < self.max_requests
        if allowed:
            timestamps.append(now)
        self._store[key] = timestamps
        return allowed


_limiter = InMemoryRateLimiter(
    max_requests=settings.rate_limit_requests,
    window_seconds=settings.rate_limit_window,
)


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Any) -> Any:
        # Rate limiting is disabled completely.
        return await call_next(request)
