"""SHIMS Licensing — offline, tamper-evident entitlements.

Monetization without cloud lock-in: license keys are short, HMAC-signed tokens
that encode a tier and expiry. They can be verified completely offline, so a
local-first product can still gate Pro/Enterprise features. A billing backend
(Stripe, Paddle, a reseller portal, …) only needs to *issue* keys with the
shared signing secret — none of that lives here.

Key format (URL-safe base64 of a compact JSON payload + truncated HMAC):

    SHIMS-<base64url(payload)>.<sig>

Tiers and their feature entitlements are the single source of truth the rest of
the app reads via ``is_entitled`` / ``current_entitlements``.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional


class Tier(str, Enum):
    COMMUNITY = "community"
    PRO = "pro"
    ENTERPRISE = "enterprise"


# Feature → minimum tier that unlocks it.
FEATURES: dict[str, Tier] = {
    # Community (always on)
    "core_agent": Tier.COMMUNITY,
    "local_models": Tier.COMMUNITY,
    "skills": Tier.COMMUNITY,
    "voice": Tier.COMMUNITY,
    "desktop_bridge": Tier.COMMUNITY,
    # Pro
    "priority_routing": Tier.PRO,
    "team_skill_library": Tier.PRO,
    "session_export": Tier.PRO,
    "skill_marketplace_publish": Tier.PRO,
    "behavior_autopilot": Tier.PRO,
    # Enterprise
    "sso": Tier.ENTERPRISE,
    "audit_export": Tier.ENTERPRISE,
    "rls_multitenant": Tier.ENTERPRISE,
    "air_gapped_deploy": Tier.ENTERPRISE,
    "gmp_modules": Tier.ENTERPRISE,
}

_TIER_RANK = {Tier.COMMUNITY: 0, Tier.PRO: 1, Tier.ENTERPRISE: 2}
_PREFIX = "SHIMS-"


def _secret() -> bytes:
    """Signing secret. Set SHIMS_LICENSE_SECRET in production."""
    return os.getenv("SHIMS_LICENSE_SECRET", "shims-license-dev-secret").encode()


def _license_file():
    """Path to the durable activated-license store (survives restarts)."""
    from pathlib import Path
    try:
        from .config import ROOT_DIR
        base = Path(ROOT_DIR)
    except Exception:
        base = Path(".")
    d = base / "data" / "state"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return d / "license.key"


def save_license_key(key: str) -> bool:
    """Persist an activated key so it survives restarts (env var still wins)."""
    try:
        _license_file().write_text((key or "").strip(), encoding="utf-8")
        return True
    except Exception:
        return False


def _stored_key() -> str:
    try:
        p = _license_file()
        return p.read_text(encoding="utf-8").strip() if p.exists() else ""
    except Exception:
        return ""


def _sign(payload_b64: str) -> str:
    sig = hmac.new(_secret(), payload_b64.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(sig).decode().rstrip("=")[:24]


@dataclass
class License:
    tier: Tier
    issued_to: str = ""
    issued_at: float = 0.0
    expires_at: Optional[float] = None  # None = perpetual
    seats: int = 1

    @property
    def is_expired(self) -> bool:
        return self.expires_at is not None and time.time() > self.expires_at

    @property
    def valid(self) -> bool:
        return not self.is_expired

    def to_dict(self) -> dict[str, Any]:
        return {
            "tier": self.tier.value,
            "issued_to": self.issued_to,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "seats": self.seats,
            "valid": self.valid,
            "expired": self.is_expired,
        }


def issue_license(tier: Tier | str, issued_to: str = "", valid_days: Optional[int] = None,
                  seats: int = 1) -> str:
    """Create a signed license key. (A billing backend calls this.)"""
    tier = Tier(tier) if not isinstance(tier, Tier) else tier
    now = time.time()
    payload = {
        "t": tier.value,
        "to": issued_to,
        "ia": int(now),
        "ea": int(now + valid_days * 86400) if valid_days else None,
        "s": seats,
    }
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    payload_b64 = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    return f"{_PREFIX}{payload_b64}.{_sign(payload_b64)}"


def verify_license(key: str) -> Optional[License]:
    """Verify a key offline. Returns a License or None if invalid/tampered."""
    if not key or not key.startswith(_PREFIX):
        return None
    body = key[len(_PREFIX):]
    if "." not in body:
        return None
    payload_b64, sig = body.rsplit(".", 1)
    if not hmac.compare_digest(sig, _sign(payload_b64)):
        return None
    try:
        pad = "=" * (-len(payload_b64) % 4)
        raw = base64.urlsafe_b64decode(payload_b64 + pad)
        p = json.loads(raw)
        return License(
            tier=Tier(p["t"]),
            issued_to=p.get("to", ""),
            issued_at=float(p.get("ia", 0)),
            expires_at=(float(p["ea"]) if p.get("ea") else None),
            seats=int(p.get("s", 1)),
        )
    except Exception:
        return None


def current_license() -> License:
    """Resolve the active license from env (preferred) or the durable store."""
    key = os.getenv("SHIMS_LICENSE_KEY", "").strip() or _stored_key()
    if key:
        lic = verify_license(key)
        if lic and lic.valid:
            return lic
    return License(tier=Tier.COMMUNITY, issued_to="local", issued_at=time.time())


def current_tier() -> Tier:
    return current_license().tier


def is_entitled(feature: str, license: Optional[License] = None) -> bool:
    """Whether the active (or given) license unlocks a feature."""
    required = FEATURES.get(feature, Tier.ENTERPRISE)
    lic = license or current_license()
    if not lic.valid:
        lic = License(tier=Tier.COMMUNITY)
    return _TIER_RANK[lic.tier] >= _TIER_RANK[required]


def current_entitlements() -> dict[str, Any]:
    """Full snapshot for the UI / API: tier, validity, and per-feature unlocks."""
    lic = current_license()
    return {
        "ok": True,
        "license": lic.to_dict(),
        "tier": lic.tier.value,
        "features": {name: is_entitled(name, lic) for name in FEATURES},
        "tiers": {
            t.value: [f for f, req in FEATURES.items() if _TIER_RANK[req] <= _TIER_RANK[t]]
            for t in Tier
        },
    }


def require(feature: str) -> tuple[bool, dict[str, Any]]:
    """Helper for endpoints: returns (allowed, upsell_payload)."""
    if is_entitled(feature):
        return True, {}
    required = FEATURES.get(feature, Tier.ENTERPRISE)
    return False, {
        "ok": False,
        "error": "feature_locked",
        "feature": feature,
        "required_tier": required.value,
        "current_tier": current_tier().value,
        "upgrade_url": "/welcome#pricing",
    }
