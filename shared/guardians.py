"""SHIMS Guardians — foundational security layer.

Provides secret validation, safe path resolution, authentication helpers,
and CORS hardening. Imported by backend, enterprise, and self-evolver.
"""
from __future__ import annotations

import functools
import hashlib
import hmac
import logging
import os
import re
import secrets
import time
import warnings
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger("shims.guardians")

ROOT_DIR = Path(__file__).resolve().parents[1]

# Paths that must never be targeted by file tools or self-evolution.
FORBIDDEN_PATH_PARTS = {
    ".env", ".venv", "storage", "__pycache__", ".git", "node_modules",
    "dist", "build", "site-packages", ".android-sdk", ".gradle", ".gradle-cache",
    ".gradle-dist", "data", "logs",
}

WEAK_SECRETS = {
    "SHIMS_SECRET_KEY": {
        "default": "change-me-local-secret",
        "fallbacks": {"dev-secret-change-me", "change-me-local-secret", "", "secret"},
    },
    "SHIMS_BRIDGE_TOKEN": {
        "default": "change-me-bridge-token",
        "fallbacks": {"change-this-bridge-token", "change-me-bridge-token", "shims-desktop-bridge-token", "", "token"},
    },
    "ENTERPRISE_BRIDGE_TOKEN": {
        "default": "change-this-bridge-token",
        "fallbacks": {"change-this-bridge-token", "change-me-bridge-token", "", "token"},
    },
}


def is_weak_secret(name: str, value: str | None) -> bool:
    """Return True if a secret is missing, default, or otherwise weak."""
    if not value:
        return True
    entry = WEAK_SECRETS.get(name, {})
    if value == entry.get("default"):
        return True
    if value.lower() in {s.lower() for s in entry.get("fallbacks", set())}:
        return True
    if len(value) < 32:
        return True
    return False


def generate_secret() -> str:
    """Generate a cryptographically strong secret."""
    return secrets.token_urlsafe(48)


def ensure_env_secrets_strong() -> dict[str, str]:
    """Check env secrets and return a dict of warnings/recommendations."""
    report: dict[str, str] = {}
    for name, entry in WEAK_SECRETS.items():
        value = os.getenv(name, "")
        if is_weak_secret(name, value):
            report[name] = "WEAK — set a strong unique value in .env"
    return report


def raise_if_weak_secrets() -> None:
    """Raise a runtime warning (not exception) if secrets are weak."""
    weak = ensure_env_secrets_strong()
    for name, msg in weak.items():
        warnings.warn(f"{name}: {msg}", RuntimeWarning, stacklevel=3)


def safe_relative_path(raw: str | Path, base: Path = ROOT_DIR) -> Path:
    """Resolve a user-supplied path strictly under base. Raises ValueError on escape."""
    raw_str = str(raw).replace("\\", "/").strip("/")
    # Reject obvious traversal attempts before resolution.
    if ".." in Path(raw_str).parts:
        raise ValueError(f"Path traversal attempt: {raw}")
    candidate = (base / raw_str).resolve()
    base_resolved = base.resolve()
    try:
        candidate.relative_to(base_resolved)
    except ValueError as exc:
        raise ValueError(f"Path escapes allowed root: {raw}") from exc
    return candidate


def is_allowed_target(path: Path | str, allowed_roots: set[str] | None = None) -> tuple[bool, str]:
    """Check whether a path is an allowed self-evolution / file tool target."""
    try:
        rel = safe_relative_path(path).relative_to(ROOT_DIR.resolve())
    except ValueError as exc:
        return False, f"path_not_allowed:{exc}"
    parts = rel.parts
    if not parts:
        return False, "empty_path"
    if allowed_roots and parts[0] not in allowed_roots:
        return False, f"root_not_allowed:{parts[0]}"
    if set(parts) & FORBIDDEN_PATH_PARTS:
        return False, "blocked_path_component"
    return True, "ok"


def create_session_token(subject: str, secret: str, ttl_seconds: int = 86400) -> str:
    """Create a signed, expiring session token: ``subject:exp:sig``."""
    exp = int(time.time()) + int(ttl_seconds)
    payload = f"{subject}:{exp}"
    sig = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{payload}:{sig}"


def verify_session_token(token: str, secret: str) -> str | None:
    """Verify a session token; return the subject if valid and unexpired."""
    parts = (token or "").split(":")
    if len(parts) != 3:
        return None
    subject, exp_str, sig = parts
    try:
        if int(exp_str) < time.time():
            return None
    except ValueError:
        return None
    expected = hmac.new(secret.encode(), f"{subject}:{exp_str}".encode(), hashlib.sha256).hexdigest()[:32]
    return subject if hmac.compare_digest(sig, expected) else None


def constant_time_compare(a: str | bytes | None, b: str | bytes | None) -> bool:
    """Constant-time comparison for tokens/passwords."""
    if not a or not b:
        return False
    a_bytes = a.encode() if isinstance(a, str) else a
    b_bytes = b.encode() if isinstance(b, str) else b
    return hmac.compare_digest(a_bytes, b_bytes)


def bridge_token_ok(token: str | None) -> bool:
    """Validate bridge token against configured value."""
    from .config import settings
    return constant_time_compare(token or "", settings.bridge_token)


def is_localhost_request(host: str | None) -> bool:
    """Return True if the request host is localhost/loopback."""
    if not host:
        return False
    host = host.split(":")[0].lower()
    return host in {"127.0.0.1", "localhost", "::1"}


def restricted_cors_origins() -> list[str]:
    """Return safe CORS origins based on environment."""
    override = os.getenv("SHIMS_CORS_ORIGINS", "").strip()
    if override:
        return [o.strip() for o in override.split(",") if o.strip()]
    env = os.getenv("SHIMS_ENV", "local").lower()
    if env in {"production", "prod", "plant"}:
        return []
    return ["http://localhost:8010", "http://127.0.0.1:8010",
            "http://localhost:8020", "http://127.0.0.1:8020"]


class SecurityHeadersMiddleware:
    """ASGI middleware adding security headers."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> Any:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def wrapped_send(message: Any) -> None:
            if message.get("type") == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((b"x-content-type-options", b"nosniff"))
                headers.append((b"x-frame-options", b"DENY"))
                headers.append((b"referrer-policy", b"strict-origin-when-cross-origin"))
                headers.append((b"content-security-policy", b"default-src 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval' cdn.jsdelivr.net cdnjs.cloudflare.com fonts.googleapis.com; style-src 'self' 'unsafe-inline' fonts.googleapis.com; img-src 'self' data: blob:; media-src 'self' blob:"))
                message["headers"] = headers
            await send(message)

        return await self.app(scope, receive, wrapped_send)


# --------------------------------------------------------------------------- #
# Input sanitization
# --------------------------------------------------------------------------- #

def sanitize_filename(name: str, max_length: int = 200) -> str:
    """Sanitize an arbitrary string for safe use as a filename.

    Strips path separators, control characters and reserved symbols, trims
    leading/trailing dots and spaces, and bounds the length. Never returns
    an empty string.
    """
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(name))
    safe = safe.strip(". ")
    if len(safe) > max_length:
        safe = safe[:max_length]
    return safe or "unnamed"


def sanitize_shell_arg(arg: str) -> str:
    """Strip null bytes and control characters from a shell argument.

    Prefer ``shlex.quote`` for actual shell interpolation; this is a defensive
    pre-filter for logging and display.
    """
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(arg))


# Backwards/forwards-compatible alias used by newer call sites.
def validate_all_secrets() -> dict[str, str]:
    """Alias of :func:`ensure_env_secrets_strong` for newer call sites."""
    return ensure_env_secrets_strong()


# --------------------------------------------------------------------------- #
# Rate limiting
# --------------------------------------------------------------------------- #

class RateLimiter:
    """Simple in-memory sliding-window rate limiter.

    Suitable for single-process deployments; back with Redis for multi-worker
    setups. Keyed by an arbitrary string (e.g. client IP or session id).
    """

    def __init__(self, max_requests: int = 60, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._requests: dict[str, list[float]] = {}

    def _prune(self, key: str, now: float) -> list[float]:
        cutoff = now - self.window_seconds
        kept = [t for t in self._requests.get(key, []) if t > cutoff]
        self._requests[key] = kept
        return kept

    def is_allowed(self, key: str) -> bool:
        """Record an attempt and return whether it is within the limit."""
        now = time.time()
        kept = self._prune(key, now)
        if len(kept) >= self.max_requests:
            return False
        kept.append(now)
        return True

    def remaining(self, key: str) -> int:
        """Requests still allowed in the current window for ``key``."""
        now = time.time()
        kept = self._prune(key, now)
        return max(0, self.max_requests - len(kept))

    def reset(self, key: str) -> None:
        """Clear all recorded attempts for ``key``."""
        self._requests.pop(key, None)


# --------------------------------------------------------------------------- #
# Audit logging
# --------------------------------------------------------------------------- #

def audit_log(event: str, details: dict[str, Any] | None = None, level: str = "info") -> None:
    """Emit a structured, security-relevant audit event to the logger."""
    msg = f"SECURITY_AUDIT: {event}"
    if details:
        msg += f" | {details}"
    if level == "warning":
        logger.warning(msg)
    elif level == "error":
        logger.error(msg)
    else:
        logger.info(msg)


def require_strong_secrets(func: Callable[..., Any]) -> Callable[..., Any]:
    """Decorator that warns if secrets are weak before running a function."""
    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        raise_if_weak_secrets()
        return func(*args, **kwargs)
    return wrapper
