"""Unified SHIMS access control: one master password, auto-provisioned bridges.

Before this module every bridge (desktop, enterprise) needed its own token value
installed by hand — SHIMS_BRIDGE_TOKEN, ENTERPRISE_BRIDGE_TOKEN,
SHIMS_DESKTOP_BRIDGE_TOKEN. Now the user sets ONE thing, ``SHIMS_MASTER_PASSWORD``,
and every bridge shares a single secret deterministically derived from it. No
per-bridge tokens to copy around; rotating the password rotates every bridge.

The same password gates the SHIMS UI/API via a signed session cookie (see the
password gate in backend/app/main.py). Everything is backward compatible: with no
master password set, the legacy explicit tokens are used and the gate is OFF.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import time

SESSION_COOKIE_NAME = "shims_session"
_BRIDGE_LABEL = b"shims-bridge-secret-v1"
_SESSION_LABEL = b"shims-session-v1"
_DEFAULT_SESSION_TTL = 7 * 24 * 3600  # one week


def master_password() -> str:
    """The single SHIMS master password (empty when unset)."""
    return (os.getenv("SHIMS_MASTER_PASSWORD") or "").strip()


def is_password_set() -> bool:
    """True when a master password is configured — enables the gate + derivation."""
    return bool(master_password())


def derived_bridge_token() -> str:
    """One secret shared by ALL bridges, derived from the master password.

    Setting the master password auto-provisions every bridge with this token, so
    the user never installs per-bridge tokens. Falls back to the legacy explicit
    token env vars when no master password is set (fully backward compatible)."""
    pw = master_password()
    if pw:
        return hmac.new(pw.encode("utf-8"), _BRIDGE_LABEL, hashlib.sha256).hexdigest()
    return (
        os.getenv("SHIMS_BRIDGE_TOKEN")
        or os.getenv("ENTERPRISE_BRIDGE_TOKEN")
        or os.getenv("SHIMS_DESKTOP_BRIDGE_TOKEN")
        or ""
    ).strip()


def verify_password(candidate: str) -> bool:
    """Constant-time check of a login attempt against the master password."""
    pw = master_password()
    if not pw:
        return False
    return hmac.compare_digest(pw, (candidate or "").strip())


def token_matches(candidate: str) -> bool:
    """Constant-time check that a presented bridge token equals the derived one."""
    expected = derived_bridge_token()
    if not expected or not candidate:
        return False
    return hmac.compare_digest(expected, candidate.strip())


def _secret_key() -> bytes:
    return (os.getenv("SHIMS_SECRET_KEY") or "shims-local").encode("utf-8")


def _sign(payload: str) -> str:
    return hmac.new(_secret_key(), (_SESSION_LABEL.decode() + payload).encode("utf-8"),
                    hashlib.sha256).hexdigest()


def make_session_cookie(ttl_seconds: int = _DEFAULT_SESSION_TTL) -> str:
    """A signed, expiring session token proving the user passed the password gate."""
    expires = str(int(time.time()) + int(ttl_seconds))
    return f"{expires}.{_sign(expires)}"


def verify_session_cookie(value: str) -> bool:
    """Validate a session cookie: signature intact and not expired."""
    try:
        expires, sig = (value or "").split(".", 1)
    except ValueError:
        return False
    if not hmac.compare_digest(sig, _sign(expires)):
        return False
    try:
        return int(expires) > int(time.time())
    except ValueError:
        return False
