"""Tests for enterprise/growth modules: teams, SSO (OIDC), skill registry,
and session-token guardian helpers. All run without the heavy app import.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

import shared.teams as teams
import shared.sso as sso
import shared.skill_registry as reg
import shared.skills as sk
import shared.skill_marketplace as mk
import shared.guardians as guardians
import shared.licensing as licensing


# --------------------------------------------------------------------------- #
# Teams
# --------------------------------------------------------------------------- #

class TestTeams:
    def setup_method(self):
        teams._TEAMS_DIR = Path(tempfile.mkdtemp())
        os.environ.pop("SHIMS_LICENSE_KEY", None)

    def test_create_and_roles(self):
        t = teams.create_team("Acme", "owner@acme.com")
        assert t.get_member("owner@acme.com").role == teams.Role.OWNER
        assert t.can_manage("owner@acme.com")

    def test_seat_limit_enforced_on_community(self):
        # Community license = 1 seat; owner fills it.
        t = teams.create_team("Solo", "owner@x.com")
        with pytest.raises(teams.TeamError):
            t.invite("second@x.com")

    def test_seats_scale_with_license(self):
        os.environ["SHIMS_LICENSE_KEY"] = licensing.issue_license("pro", seats=5)
        t = teams.create_team("Team", "owner@x.com")
        inv = t.invite("a@x.com")
        assert inv.email == "a@x.com"
        m = t.accept_invite(inv.token)
        assert m.status == "active"
        assert t.seats_used == 2
        del os.environ["SHIMS_LICENSE_KEY"]

    def test_cannot_remove_only_owner(self):
        t = teams.create_team("Solo", "owner@x.com")
        with pytest.raises(teams.TeamError):
            t.remove_member("owner@x.com")

    def test_persistence(self):
        t = teams.create_team("Persist", "o@x.com", team_id="t_persist")
        t2 = teams.get_team("t_persist")
        assert t2 is not None and t2.name == "Persist"


# --------------------------------------------------------------------------- #
# SSO (deterministic, network-free pieces)
# --------------------------------------------------------------------------- #

class TestSSO:
    def teardown_method(self):
        for k in ("SHIMS_OIDC_ISSUER", "SHIMS_OIDC_CLIENT_ID", "SHIMS_OIDC_REDIRECT_URI",
                  "SHIMS_SSO_ALLOWED_DOMAINS"):
            os.environ.pop(k, None)

    def test_disabled_without_config(self):
        assert not sso.load_config().enabled
        assert sso.begin_login()["ok"] is False

    def test_pkce_pair(self):
        v, c = sso.make_pkce()
        assert v and c and v != c

    def test_begin_login_builds_url(self):
        os.environ["SHIMS_OIDC_ISSUER"] = "https://idp.example.com"
        os.environ["SHIMS_OIDC_CLIENT_ID"] = "abc"
        os.environ["SHIMS_OIDC_REDIRECT_URI"] = "http://127.0.0.1:8010/auth/sso/callback"
        res = sso.begin_login()
        assert res["ok"]
        assert "code_challenge=" in res["url"] and "state=" in res["url"]
        assert res["state"] in sso._pending

    def test_claim_mapping_and_domain_filter(self):
        user = sso.map_claims({"email": "Jane@Acme.com", "name": "Jane", "sub": "1"})
        assert user["email"] == "jane@acme.com"
        os.environ["SHIMS_SSO_ALLOWED_DOMAINS"] = "acme.com"
        assert sso._domain_allowed("jane@acme.com")
        assert not sso._domain_allowed("jane@evil.com")

    def test_invalid_state_rejected(self):
        import asyncio
        os.environ["SHIMS_OIDC_ISSUER"] = "https://idp.example.com"
        os.environ["SHIMS_OIDC_CLIENT_ID"] = "abc"
        os.environ["SHIMS_OIDC_REDIRECT_URI"] = "http://127.0.0.1:8010/cb"
        out = asyncio.get_event_loop().run_until_complete(
            sso.complete_login("code", "bogus-state"))
        assert out["ok"] is False and out["error"] == "invalid_state"


# --------------------------------------------------------------------------- #
# Skill registry
# --------------------------------------------------------------------------- #

class TestRegistry:
    def setup_method(self):
        reg._REG_DIR = Path(tempfile.mkdtemp())
        reg._PUBLISHED = reg._REG_DIR / "published.json"
        sk.SKILLS_DIR = Path(tempfile.mkdtemp())
        os.environ.pop("SHIMS_REGISTRY_URL", None)

    def test_publish_and_serve(self):
        r = reg.publish_local("Team Style", "house writing style", body="be terse")
        assert r["ok"]
        cat = reg.published_catalog()
        assert any(e["name"] == "Team Style" for e in cat)

    def test_marketplace_merges_published(self):
        reg.publish_local("Registry Skill", "from registry", body="x")
        items = mk.list_catalog()
        assert any(i.get("name") == "Registry Skill" for i in items)

    def test_install_registry_skill(self):
        reg.publish_local("Installable", "install me", body="do it")
        slug = reg.published_catalog()[0]["slug"]
        res = mk.install(slug)
        assert res["ok"]
        assert any(s["name"] == "Installable" for s in sk.list_skills())

    def test_remote_not_configured(self):
        assert reg.fetch_remote_catalog()["ok"] is False
        assert reg.status()["remote_configured"] is False


# --------------------------------------------------------------------------- #
# Session tokens
# --------------------------------------------------------------------------- #

class TestSessionTokens:
    def test_round_trip(self):
        tok = guardians.create_session_token("jane@x.com", "secret-key")
        assert guardians.verify_session_token(tok, "secret-key") == "jane@x.com"

    def test_wrong_secret_rejected(self):
        tok = guardians.create_session_token("jane@x.com", "secret-key")
        assert guardians.verify_session_token(tok, "other-key") is None

    def test_expired_rejected(self):
        tok = guardians.create_session_token("jane@x.com", "secret-key", ttl_seconds=-1)
        assert guardians.verify_session_token(tok, "secret-key") is None
