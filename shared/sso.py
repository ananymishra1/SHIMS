"""SHIMS SSO — enterprise single sign-on via OpenID Connect (and a SAML hook).

A real OIDC Authorization Code + PKCE client. It works with any standards-
compliant IdP (Google Workspace, Okta, Azure AD, Auth0, Keycloak) configured by
environment variables — no IdP-specific code. The deterministic pieces (auth-URL
construction, PKCE, state/nonce, claim mapping, session issuance) are pure and
unit-tested; the token exchange + userinfo are thin httpx calls.

Configuration (env):
  SHIMS_OIDC_ISSUER            e.g. https://accounts.google.com
  SHIMS_OIDC_CLIENT_ID
  SHIMS_OIDC_CLIENT_SECRET     (omit for public PKCE clients)
  SHIMS_OIDC_REDIRECT_URI      e.g. http://127.0.0.1:8010/auth/sso/callback
  SHIMS_OIDC_AUTH_ENDPOINT     (optional; else issuer + /authorize)
  SHIMS_OIDC_TOKEN_ENDPOINT    (optional; else issuer + /token)
  SHIMS_OIDC_USERINFO_ENDPOINT (optional; else issuer + /userinfo)
  SHIMS_OIDC_SCOPES            (default "openid email profile")
  SHIMS_SSO_ALLOWED_DOMAINS    (optional comma list, e.g. "acme.com")
"""
from __future__ import annotations

import base64
import hashlib
import os
import secrets
import time
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import urlencode

from . import guardians


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #

@dataclass
class OIDCConfig:
    issuer: str = ""
    client_id: str = ""
    client_secret: str = ""
    redirect_uri: str = ""
    auth_endpoint: str = ""
    token_endpoint: str = ""
    userinfo_endpoint: str = ""
    scopes: str = "openid email profile"

    @property
    def enabled(self) -> bool:
        return bool(self.issuer and self.client_id and self.redirect_uri)


def load_config() -> OIDCConfig:
    issuer = os.getenv("SHIMS_OIDC_ISSUER", "").rstrip("/")
    return OIDCConfig(
        issuer=issuer,
        client_id=os.getenv("SHIMS_OIDC_CLIENT_ID", ""),
        client_secret=os.getenv("SHIMS_OIDC_CLIENT_SECRET", ""),
        redirect_uri=os.getenv("SHIMS_OIDC_REDIRECT_URI", ""),
        auth_endpoint=os.getenv("SHIMS_OIDC_AUTH_ENDPOINT", f"{issuer}/authorize" if issuer else ""),
        token_endpoint=os.getenv("SHIMS_OIDC_TOKEN_ENDPOINT", f"{issuer}/token" if issuer else ""),
        userinfo_endpoint=os.getenv("SHIMS_OIDC_USERINFO_ENDPOINT", f"{issuer}/userinfo" if issuer else ""),
        scopes=os.getenv("SHIMS_OIDC_SCOPES", "openid email profile"),
    )


# --------------------------------------------------------------------------- #
# PKCE + state
# --------------------------------------------------------------------------- #

def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def make_pkce() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) for PKCE S256."""
    verifier = _b64url(secrets.token_bytes(40))
    challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
    return verifier, challenge


# In-memory pending-auth store (state -> {verifier, nonce, ts}). Swap for Redis
# in multi-process deployments.
_pending: dict[str, dict[str, Any]] = {}
_PENDING_TTL = 600


def _gc_pending() -> None:
    now = time.time()
    for s in [k for k, v in _pending.items() if now - v.get("ts", 0) > _PENDING_TTL]:
        _pending.pop(s, None)


def begin_login(config: Optional[OIDCConfig] = None) -> dict[str, Any]:
    """Create an authorization URL + persist PKCE/state. Returns {url, state}."""
    cfg = config or load_config()
    if not cfg.enabled:
        return {"ok": False, "error": "sso_not_configured"}
    _gc_pending()
    state = _b64url(secrets.token_bytes(16))
    nonce = _b64url(secrets.token_bytes(16))
    verifier, challenge = make_pkce()
    _pending[state] = {"verifier": verifier, "nonce": nonce, "ts": time.time()}
    params = {
        "response_type": "code",
        "client_id": cfg.client_id,
        "redirect_uri": cfg.redirect_uri,
        "scope": cfg.scopes,
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return {"ok": True, "url": f"{cfg.auth_endpoint}?{urlencode(params)}", "state": state}


def _domain_allowed(email: str) -> bool:
    allowed = os.getenv("SHIMS_SSO_ALLOWED_DOMAINS", "").strip()
    if not allowed:
        return True
    domain = email.rsplit("@", 1)[-1].lower()
    return domain in {d.strip().lower() for d in allowed.split(",") if d.strip()}


def map_claims(claims: dict[str, Any]) -> dict[str, Any]:
    """Normalize IdP claims into a SHIMS user record."""
    email = (claims.get("email") or "").lower().strip()
    return {
        "email": email,
        "name": claims.get("name") or claims.get("preferred_username") or email.split("@")[0],
        "sub": claims.get("sub", ""),
        "email_verified": bool(claims.get("email_verified", False)),
        "picture": claims.get("picture", ""),
    }


async def complete_login(code: str, state: str, config: Optional[OIDCConfig] = None) -> dict[str, Any]:
    """Exchange the auth code for tokens, fetch userinfo, issue a SHIMS session.

    Returns {ok, user, session_token} or {ok: False, error}.
    """
    cfg = config or load_config()
    if not cfg.enabled:
        return {"ok": False, "error": "sso_not_configured"}
    pend = _pending.pop(state, None)
    if not pend:
        return {"ok": False, "error": "invalid_state"}

    import httpx
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": cfg.redirect_uri,
        "client_id": cfg.client_id,
        "code_verifier": pend["verifier"],
    }
    if cfg.client_secret:
        data["client_secret"] = cfg.client_secret
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            tok = await client.post(cfg.token_endpoint, data=data,
                                    headers={"Accept": "application/json"})
            tok.raise_for_status()
            tokens = tok.json()
            access = tokens.get("access_token", "")
            ui = await client.get(cfg.userinfo_endpoint,
                                  headers={"Authorization": f"Bearer {access}"})
            ui.raise_for_status()
            claims = ui.json()
    except Exception as exc:  # pragma: no cover - network path
        return {"ok": False, "error": f"token_exchange_failed: {str(exc)[:160]}"}

    user = map_claims(claims)
    if not user["email"]:
        return {"ok": False, "error": "no_email_in_claims"}
    if not _domain_allowed(user["email"]):
        guardians.audit_log("sso_domain_denied", {"email": user["email"]}, level="warning")
        return {"ok": False, "error": "email_domain_not_allowed"}

    from .config import settings
    session_token = guardians.create_session_token(user["email"], settings.secret_key) \
        if hasattr(guardians, "create_session_token") else secrets.token_urlsafe(32)
    guardians.audit_log("sso_login", {"email": user["email"], "sub": user["sub"]})
    return {"ok": True, "user": user, "session_token": session_token}


def status() -> dict[str, Any]:
    cfg = load_config()
    return {
        "ok": True,
        "oidc_enabled": cfg.enabled,
        "issuer": cfg.issuer,
        "redirect_uri": cfg.redirect_uri,
        "scopes": cfg.scopes,
        "allowed_domains": os.getenv("SHIMS_SSO_ALLOWED_DOMAINS", "") or "(any)",
        "saml_enabled": bool(os.getenv("SHIMS_SAML_METADATA_URL", "")),
    }


# --------------------------------------------------------------------------- #
# SAML hook (metadata-driven; wire python3-saml in enterprise builds)
# --------------------------------------------------------------------------- #

def saml_available() -> bool:
    try:
        import onelogin.saml2  # type: ignore  # noqa: F401
        return bool(os.getenv("SHIMS_SAML_METADATA_URL", ""))
    except Exception:
        return False
