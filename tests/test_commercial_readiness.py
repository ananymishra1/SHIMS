"""Tests for commercial-readiness additions:

- The public landing / sales page front door and its routes.
- Additive guardian hardening helpers (filename sanitization, rate limiting,
  audit logging, secret validation alias).

These cover net-new surface area only; existing behaviour is untouched.
"""
from __future__ import annotations

import pytest

from shared.guardians import (
    RateLimiter,
    audit_log,
    sanitize_filename,
    sanitize_shell_arg,
    validate_all_secrets,
)


@pytest.fixture(scope="module")
def client():
    """TestClient for the full app.

    The full app pulls in heavy optional dependencies (audio/ML stacks). In
    minimal environments where those aren't installed, the landing-route tests
    skip rather than error, while the guardian tests below always run.
    """
    try:
        from fastapi.testclient import TestClient
        from backend.app.main import app
    except Exception as exc:  # pragma: no cover - env-dependent
        pytest.skip(f"full app not importable in this environment: {exc}")
    return TestClient(app)


# --------------------------------------------------------------------------- #
# Landing / sales surface
# --------------------------------------------------------------------------- #

class TestLandingPage:
    def test_root_serves_landing(self, client):
        r = client.get("/")
        assert r.status_code == 200
        body = r.text
        # Marketing front door, not the raw app shell.
        assert "SHIMS" in body
        assert "Launch" in body  # CTA into the app
        assert "Pricing" in body or "pricing" in body

    def test_welcome_and_landing_aliases(self, client):
        for path in ("/welcome", "/landing"):
            r = client.get(path)
            assert r.status_code == 200
            assert "SHIMS" in r.text

    def test_app_still_available(self, client):
        # The application itself must remain reachable at /app.
        r = client.get("/app")
        assert r.status_code == 200


# --------------------------------------------------------------------------- #
# Guardian hardening helpers
# --------------------------------------------------------------------------- #

class TestSanitization:
    def test_sanitize_filename_basic(self):
        assert sanitize_filename("hello world.txt") == "hello world.txt"

    def test_sanitize_filename_strips_dangerous(self):
        assert sanitize_filename('file<>:"/\\|?*.txt') == "file_________.txt"

    def test_sanitize_filename_trims_dots(self):
        assert sanitize_filename("...hidden") == "hidden"

    def test_sanitize_filename_bounds_length(self):
        assert len(sanitize_filename("a" * 500)) <= 200

    def test_sanitize_filename_never_empty(self):
        assert sanitize_filename("...") == "unnamed"

    def test_sanitize_shell_arg_removes_control_chars(self):
        assert sanitize_shell_arg("ls\x00 -la\x07") == "ls -la"


class TestRateLimiter:
    def test_allows_then_blocks(self):
        rl = RateLimiter(max_requests=3, window_seconds=60)
        assert rl.is_allowed("u1")
        assert rl.is_allowed("u1")
        assert rl.is_allowed("u1")
        assert not rl.is_allowed("u1")

    def test_independent_keys(self):
        rl = RateLimiter(max_requests=1, window_seconds=60)
        assert rl.is_allowed("a")
        assert not rl.is_allowed("a")
        assert rl.is_allowed("b")

    def test_remaining_and_reset(self):
        rl = RateLimiter(max_requests=2, window_seconds=60)
        rl.is_allowed("x")
        assert rl.remaining("x") == 1
        rl.reset("x")
        assert rl.remaining("x") == 2


class TestSecretsAndAudit:
    def test_validate_all_secrets_returns_mapping(self):
        report = validate_all_secrets()
        assert isinstance(report, dict)

    def test_audit_log_does_not_raise(self):
        # Smoke: all severity levels are safe to call.
        audit_log("test_event", {"k": "v"})
        audit_log("warn_event", {"k": "v"}, level="warning")
        audit_log("err_event", level="error")
