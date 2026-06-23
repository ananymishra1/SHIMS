"""Bridge regression tests for SHIMS Desktop and Enterprise bridges."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient

import backend.app.main as omni_app
import shared.agent_tools as agent_tools
import shims_enterprise.app as enterprise_app


# ── Desktop Bridge ───────────────────────────────────────────────────────────

class _FakeBridge:
    def __init__(self, responses: dict[str, Any] | None = None) -> None:
        self._responses = responses or {}

    async def shell(self, command: str, cwd: str | None = None, timeout: int = 60) -> dict[str, Any]:
        return self._responses.get("shell", {"ok": True, "stdout": command})

    async def screenshot(self) -> dict[str, Any]:
        return self._responses.get("screenshot", {"ok": True, "format": "png", "data": "b64"})

    async def system_info(self) -> dict[str, Any]:
        return self._responses.get("system_info", {"ok": True, "platform": "test"})

    async def find_file(self, name: str, root: str = "C:\\") -> dict[str, Any]:
        return self._responses.get("find_file", {"ok": True, "matches": [root + "\\" + name]})

    async def read_file(self, path: str) -> dict[str, Any]:
        return self._responses.get("read_file", {"ok": True, "content": f"content of {path}"})

    async def write_file(self, path: str, content: str) -> dict[str, Any]:
        return self._responses.get("write_file", {"ok": True, "size": len(content)})

    async def ping(self) -> dict[str, Any]:
        return self._responses.get("ping", {"ok": True, "type": "pong", "time": 0})


def _fake_bridge_client(responses: dict[str, Any] | None = None):
    async def _client():
        return _FakeBridge(responses)
    return _client


def test_desktop_bridge_status_without_token():
    with TestClient(omni_app.app) as client:
        # Ensure module global token is empty for this test.
        original = omni_app._BRIDGE_TOKEN
        omni_app._BRIDGE_TOKEN = ""
        try:
            r = client.get("/api/desktop/bridge/status")
            assert r.status_code == 200
            data = r.json()
            assert data["ok"] is False
            assert data["connected"] is False
        finally:
            omni_app._BRIDGE_TOKEN = original


def test_desktop_bridge_command_unknown_type(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(omni_app, "_bridge_client", _fake_bridge_client())
    with TestClient(omni_app.app) as client:
        r = client.post("/api/desktop/bridge/command", json={"type": "not_a_command"})
        assert r.status_code == 200
        assert r.json() == {"ok": False, "error": "Unknown bridge command: not_a_command"}


def test_desktop_bridge_command_shell(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(omni_app, "_bridge_client", _fake_bridge_client({"shell": {"ok": True, "stdout": "hello"}}))
    with TestClient(omni_app.app) as client:
        r = client.post("/api/desktop/bridge/command", json={"type": "shell", "command": "echo hello"})
        assert r.status_code == 200
        assert r.json()["ok"] is True


def test_desktop_bridge_command_read_write_file(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(omni_app, "_bridge_client", _fake_bridge_client())
    with TestClient(omni_app.app) as client:
        r = client.post("/api/desktop/bridge/command", json={"type": "read_file", "path": "C:\\test.txt"})
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert "content of C:\\test.txt" in data["content"]

        w = client.post("/api/desktop/bridge/command", json={"type": "write_file", "path": "C:\\test.txt", "content": "hi"})
        assert w.status_code == 200
        assert w.json()["ok"] is True


# ── Enterprise Bridge ────────────────────────────────────────────────────────

class _FakeHttpxClient:
    def __init__(self, response: dict[str, Any], status: int = 200) -> None:
        self._response = response
        self._status = status

    def post(self, *args, **kwargs):
        class _Resp:
            status_code = 200
            def json(inner):  # noqa: N805
                return self._response
            def raise_for_status(inner):  # noqa: N805
                if self._status >= 400:
                    raise RuntimeError("HTTP error")
        return _Resp()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


def test_enterprise_command_normalizes_status_ok(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(agent_tools, "settings", SimpleNamespace(enterprise_url="http://127.0.0.1:8020", bridge_token="token"))
    monkeypatch.setattr(httpx, "Client", lambda *a, **k: _FakeHttpxClient({"status": "ok", "summary": "factory"}))
    result = agent_tools._run_enterprise_command({"command": "summary", "payload": {}})
    assert result["ok"] is True
    assert result["summary"] == "factory"


def test_enterprise_command_normalizes_status_error(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(agent_tools, "settings", SimpleNamespace(enterprise_url="http://127.0.0.1:8020", bridge_token="token"))
    monkeypatch.setattr(httpx, "Client", lambda *a, **k: _FakeHttpxClient({"status": "error", "message": "bad"}))
    result = agent_tools._run_enterprise_command({"command": "bad", "payload": {}})
    assert result["ok"] is False
    assert result["message"] == "bad"


def test_enterprise_bridge_disabled_by_default():
    with TestClient(enterprise_app.app) as client:
        r = client.post(
            "/api/bridge/command",
            json={"command": "summary", "payload": {}},
            headers={"X-Bridge-Token": "change-me-bridge-token"},
        )
        assert r.status_code in (401, 403)
