from __future__ import annotations

import os
from typing import Generator

import pytest

from shared import bridge_auth


@pytest.fixture(autouse=True)
def clean_env() -> Generator[None, None, None]:
    keys = ["SHIMS_MASTER_PASSWORD", "SHIMS_BRIDGE_TOKEN", "ENTERPRISE_BRIDGE_TOKEN",
            "SHIMS_DESKTOP_BRIDGE_TOKEN", "SHIMS_SECRET_KEY"]
    saved = {k: os.environ.get(k) for k in keys}
    for k in keys:
        os.environ.pop(k, None)
    yield
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def test_no_password_falls_back_to_legacy_token() -> None:
    assert not bridge_auth.is_password_set()
    os.environ["SHIMS_BRIDGE_TOKEN"] = "legacy-abc"
    assert bridge_auth.derived_bridge_token() == "legacy-abc"


def test_master_password_derives_one_stable_secret() -> None:
    os.environ["SHIMS_MASTER_PASSWORD"] = "hunter2"
    assert bridge_auth.is_password_set()
    t1 = bridge_auth.derived_bridge_token()
    t2 = bridge_auth.derived_bridge_token()
    assert t1 == t2 and len(t1) == 64  # deterministic sha256 hex
    # A different password yields a different secret; the legacy token is ignored.
    os.environ["SHIMS_BRIDGE_TOKEN"] = "legacy-abc"
    assert bridge_auth.derived_bridge_token() == t1
    os.environ["SHIMS_MASTER_PASSWORD"] = "different"
    assert bridge_auth.derived_bridge_token() != t1


def test_verify_password_constant_time() -> None:
    os.environ["SHIMS_MASTER_PASSWORD"] = "hunter2"
    assert bridge_auth.verify_password("hunter2")
    assert not bridge_auth.verify_password("wrong")
    assert not bridge_auth.verify_password("")


def test_token_matches() -> None:
    os.environ["SHIMS_MASTER_PASSWORD"] = "hunter2"
    good = bridge_auth.derived_bridge_token()
    assert bridge_auth.token_matches(good)
    assert not bridge_auth.token_matches("nope")
    assert not bridge_auth.token_matches("")


def test_session_cookie_roundtrip_and_expiry() -> None:
    os.environ["SHIMS_SECRET_KEY"] = "test-secret"
    good = bridge_auth.make_session_cookie(ttl_seconds=3600)
    assert bridge_auth.verify_session_cookie(good)
    # Tampered signature fails.
    assert not bridge_auth.verify_session_cookie(good[:-1] + ("0" if good[-1] != "0" else "1"))
    # Already-expired cookie fails.
    expired = bridge_auth.make_session_cookie(ttl_seconds=-10)
    assert not bridge_auth.verify_session_cookie(expired)
    # A cookie signed with a different secret key fails.
    os.environ["SHIMS_SECRET_KEY"] = "rotated-secret"
    assert not bridge_auth.verify_session_cookie(good)
