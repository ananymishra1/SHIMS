"""Sanity tests for the SHIMS Omni brain chat stream."""
from __future__ import annotations

import asyncio
import json
import os

import pytest


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _patch_env(monkeypatch):
    """Keep tests offline and avoid accidental cloud/model calls."""
    monkeypatch.setenv("SHIMS_OMNIPOTENT_MODE", "false")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:11435")


def test_greeting_fast_path_skips_brain_retrieval(monkeypatch):
    from backend.app.main import _brain_stream, ChatRequest

    called = []
    monkeypatch.setattr(
        "shared.omni_brain.retrieve_context",
        lambda *args, **kwargs: (called.append("retrieve_context") or {"hits": []}),
    )

    async def collect():
        req = ChatRequest(message="hi", session_id="test-greet-fast", conversation_mode=False)
        chunks = []
        async for chunk in _brain_stream(req):
            chunks.append(json.loads(chunk.decode("utf-8")))
            if len(chunks) > 10:
                break
        return chunks

    chunks = _run(collect())
    types = [c["type"] for c in chunks]
    assert "token" in types
    assert any(c.get("route") == "greeting" or c.get("route") == "local:greeting" for c in chunks if c["type"] == "done")
    assert "retrieve_context" not in called, "greeting should not trigger brain retrieval"


def test_greeting_fast_path_under_omnipotent_mode(monkeypatch):
    """Even with omnipotent mode on, a greeting should not block on the agent loop."""
    from backend.app.main import _brain_stream, ChatRequest

    monkeypatch.setenv("SHIMS_OMNIPOTENT_MODE", "true")
    called = []
    monkeypatch.setattr(
        "shared.omni_brain.retrieve_context",
        lambda *args, **kwargs: (called.append("retrieve_context") or {"hits": []}),
    )

    async def collect():
        req = ChatRequest(message="hi", session_id="test-greet-omni", conversation_mode=False)
        chunks = []
        async for chunk in _brain_stream(req):
            chunks.append(json.loads(chunk.decode("utf-8")))
            if len(chunks) > 10:
                break
        return chunks

    chunks = _run(collect())
    types = [c["type"] for c in chunks]
    assert "token" in types
    assert "retrieve_context" not in called
