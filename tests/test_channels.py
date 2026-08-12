"""Tests for the inbound channel relay (WhatsApp bridge landing zone)."""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

import shared.channels as ch
from backend.app.main import app


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(ch, "CHANNELS_DB", tmp_path / "channels.sqlite3")
    return tmp_path


# --------------------------------------------------------------------------- #
# Store
# --------------------------------------------------------------------------- #

def test_records_and_reads_back(db):
    res = ch.record_inbound("whatsapp", "m1", body="hello there",
                            sender_id="4477@s.whatsapp.net", sender_name="Sam")
    assert res["ok"] and res["stored"] is True

    out = ch.recent("whatsapp")
    assert out["connected"] is True
    assert out["count"] == 1
    assert out["messages"][0]["text"] == "hello there"
    assert out["messages"][0]["sender_name"] == "Sam"


def test_duplicate_relay_is_success_not_error(db):
    """The bridge retries on failure and WhatsApp redelivers. A duplicate must
    not surface as an error, or the relay retries harder against a full DB."""
    ch.record_inbound("whatsapp", "same-id", body="first")
    again = ch.record_inbound("whatsapp", "same-id", body="first")

    assert again["ok"] is True
    assert again["stored"] is False
    assert again["duplicate"] is True
    assert ch.recent("whatsapp")["count"] == 1


def test_newest_first(db):
    now = time.time()
    ch.record_inbound("whatsapp", "old", body="older", received_at=now - 100)
    ch.record_inbound("whatsapp", "new", body="newer", received_at=now)
    texts = [m["text"] for m in ch.recent("whatsapp")["messages"]]
    assert texts == ["newer", "older"]


def test_channels_are_isolated(db):
    ch.record_inbound("whatsapp", "w1", body="wa")
    ch.record_inbound("signal", "s1", body="sig")
    assert ch.recent("whatsapp")["count"] == 1
    assert ch.recent("signal")["messages"][0]["text"] == "sig"


def test_retention_is_bounded(db, monkeypatch):
    """An always-on relay must not grow the DB without limit."""
    monkeypatch.setattr(ch, "MAX_RETAINED", 5)
    for i in range(12):
        ch.record_inbound("whatsapp", f"m{i}", body=str(i), received_at=time.time() + i)
    out = ch.recent("whatsapp", limit=50)
    assert out["count"] == 5
    assert out["messages"][0]["text"] == "11"  # newest survived


def test_body_is_truncated(db, monkeypatch):
    monkeypatch.setattr(ch, "MAX_BODY", 20)
    ch.record_inbound("whatsapp", "long", body="x" * 500)
    assert len(ch.recent("whatsapp")["messages"][0]["text"]) == 20


def test_requires_channel_and_message_id(db):
    assert ch.record_inbound("", "m1")["ok"] is False
    assert ch.record_inbound("whatsapp", "")["ok"] is False


def test_empty_channel_reports_not_connected(db):
    out = ch.recent("whatsapp")
    assert out["connected"] is False
    assert out["messages"] == []


# --------------------------------------------------------------------------- #
# Token gate
# --------------------------------------------------------------------------- #

def test_token_fails_closed_when_unset(monkeypatch):
    """No configured secret must reject, not accept. This endpoint ingests
    message content from another process."""
    monkeypatch.delenv("SHIMS_BRIDGE_TOKEN", raising=False)
    assert ch.token_ok("") is False
    assert ch.token_ok("anything") is False


def test_token_rejects_short_secret(monkeypatch):
    monkeypatch.setenv("SHIMS_BRIDGE_TOKEN", "short")
    assert ch.token_ok("short") is False


def test_token_accepts_matching_secret(monkeypatch):
    secret = "k" * 32
    monkeypatch.setenv("SHIMS_BRIDGE_TOKEN", secret)
    assert ch.token_ok(secret) is True
    assert ch.token_ok(secret + "x") is False


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #

def test_inbound_endpoint_rejects_bad_token(db, monkeypatch):
    monkeypatch.setenv("SHIMS_BRIDGE_TOKEN", "t" * 32)
    client = TestClient(app)
    r = client.post("/api/channels/inbound",
                    json={"channel": "whatsapp", "message_id": "x", "body": "hi"},
                    headers={"x-bridge-token": "wrong"})
    assert r.status_code == 401
    assert ch.recent("whatsapp")["count"] == 0


def test_inbound_endpoint_stores_with_valid_token(db, monkeypatch):
    secret = "t" * 32
    monkeypatch.setenv("SHIMS_BRIDGE_TOKEN", secret)
    client = TestClient(app)
    r = client.post("/api/channels/inbound",
                    json={"channel": "whatsapp", "message_id": "abc",
                          "body": "from the bridge", "sender_name": "Ana"},
                    headers={"x-bridge-token": secret})
    assert r.status_code == 200 and r.json()["stored"] is True

    got = client.get("/api/channels/whatsapp/recent?limit=5").json()
    assert got["connected"] is True
    assert got["messages"][0]["text"] == "from the bridge"
