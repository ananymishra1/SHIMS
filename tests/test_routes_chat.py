"""Smoke tests for chat routes after extraction to backend/app/routes_chat.py."""
from __future__ import annotations

import json
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.main import ChatRequest

client = TestClient(app)


def _empty_stream():
    async def gen():
        yield b'{"type":"done","route":"test"}\n'
    return gen()


def test_brain_turn_exists():
    with patch("backend.app.routes_chat._safe_brain_stream", return_value=_empty_stream()):
        resp = client.post("/brain/turn", json={"message": "hi"})
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/x-ndjson"


def test_chat_stream_exists():
    with patch("backend.app.routes_chat._safe_brain_stream", return_value=_empty_stream()):
        resp = client.post("/chat/stream", json={"message": "hi"})
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/x-ndjson"


def test_chat_converse_sets_conversation_mode():
    captured: ChatRequest | None = None

    async def fake_stream(req: ChatRequest):
        nonlocal captured
        captured = req
        yield b'{"type":"done","route":"test"}\n'

    with patch("backend.app.routes_chat._safe_brain_stream", side_effect=fake_stream):
        resp = client.post("/chat/converse", json={"message": "hi"})
    assert resp.status_code == 200
    assert captured is not None
    assert captured.conversation_mode is True


def test_api_chat_non_streaming():
    async def fake_stream(req: ChatRequest):
        yield b'{"type":"token","content":"Hello"}\n'
        yield b'{"type":"done","route":"test","provider":"ollama","model":"llama3.2:latest"}\n'

    with patch("backend.app.routes_chat._safe_brain_stream", side_effect=fake_stream):
        resp = client.post("/api/chat", json={"message": "hi"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert "Hello" in body["answer"]


def test_agent_run_forces_agent_mode():
    captured: ChatRequest | None = None

    async def fake_stream(req: ChatRequest):
        nonlocal captured
        captured = req
        yield b'{"type":"done","route":"test"}\n'

    with patch("backend.app.routes_chat._safe_brain_stream", side_effect=fake_stream):
        resp = client.post("/agent/run", json={"message": "list files"})
    assert resp.status_code == 200
    assert captured is not None
    assert captured.agent_mode is True
