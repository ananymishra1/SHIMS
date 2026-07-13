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


def _fake_ollama_collector(reply: str):
    async def fake_collect(model, messages, *, realtime=False, max_tokens=None, on_delta=None, first_token_timeout=60.0):
        if on_delta:
            await on_delta(reply)
        return reply

    return fake_collect


def test_greeting_uses_fast_lane_and_skips_brain_retrieval(monkeypatch):
    """Typed "hi" must stream a real LLM reply via the fast lane —
    no canned greeting, no brain retrieval, no agent loop."""
    import backend.app.main as m

    called = []
    monkeypatch.setattr(
        "shared.omni_brain.retrieve_context",
        lambda *args, **kwargs: (called.append("retrieve_context") or {"hits": []}),
    )
    monkeypatch.setattr(m, "_collect_ollama_stream", _fake_ollama_collector("Hey! How can I help you today?"))

    async def collect():
        req = m.ChatRequest(message="hi", session_id="test-greet-fast", provider="ollama", model="llama3.2:latest", conversation_mode=False)
        chunks = []
        async for chunk in m._safe_brain_stream(req):
            chunks.append(json.loads(chunk.decode("utf-8")))
            if len(chunks) > 10:
                break
        return chunks

    chunks = _run(collect())
    types = [c["type"] for c in chunks]
    assert "token" in types
    answer = "".join(c.get("content", "") for c in chunks if c["type"] == "token")
    assert "How can I help you" in answer
    assert any(c.get("route") == "ollama-fast-path" for c in chunks if c["type"] == "done")
    assert "retrieve_context" not in called, "simple chat should not trigger brain retrieval"


def test_fast_lane_ignores_forced_agent_mode(monkeypatch):
    """agent_mode=true from the UI must not push simple chat into the agent loop."""
    import backend.app.main as m

    monkeypatch.setattr(m, "_collect_ollama_stream", _fake_ollama_collector("Doing great, thanks for asking!"))

    async def collect():
        req = m.ChatRequest(message="how are you?", session_id="test-agent-mode-chat", provider="ollama", model="llama3.2:latest", conversation_mode=False, agent_mode=True)
        chunks = []
        async for chunk in m._safe_brain_stream(req):
            chunks.append(json.loads(chunk.decode("utf-8")))
            if len(chunks) > 10:
                break
        return chunks

    chunks = _run(collect())
    assert any(c.get("route") == "ollama-fast-path" for c in chunks if c["type"] == "done")
    answer = "".join(c.get("content", "") for c in chunks if c["type"] == "token")
    assert "Doing great" in answer


def test_greeting_fast_path_under_omnipotent_mode(monkeypatch):
    """Even with omnipotent mode on, a greeting should not block on the agent loop."""
    import backend.app.main as m

    monkeypatch.setenv("SHIMS_OMNIPOTENT_MODE", "true")
    called = []
    monkeypatch.setattr(
        "shared.omni_brain.retrieve_context",
        lambda *args, **kwargs: (called.append("retrieve_context") or {"hits": []}),
    )
    monkeypatch.setattr(m, "_collect_ollama_stream", _fake_ollama_collector("Hello!"))

    async def collect():
        req = m.ChatRequest(message="hi", session_id="test-greet-omni", provider="ollama", model="llama3.2:latest", conversation_mode=False)
        chunks = []
        async for chunk in m._safe_brain_stream(req):
            chunks.append(json.loads(chunk.decode("utf-8")))
            if len(chunks) > 10:
                break
        return chunks

    chunks = _run(collect())
    types = [c["type"] for c in chunks]
    assert "token" in types
    assert "retrieve_context" not in called


def test_voice_wake_ping_gets_instant_local_ack():
    """Voice wake pings still get the instant local ack (now in English)."""
    import backend.app.main as m

    async def collect():
        req = m.ChatRequest(message="are you there", session_id="test-wake-ping", source="voice", provider="ollama", conversation_mode=False)
        chunks = []
        async for chunk in m._safe_brain_stream(req):
            chunks.append(json.loads(chunk.decode("utf-8")))
            if len(chunks) > 10:
                break
        return chunks

    chunks = _run(collect())
    answer = "".join(c.get("content", "") for c in chunks if c["type"] == "token")
    assert "I'm listening" in answer
    assert any(c.get("route") in ("greeting", "local:greeting") for c in chunks if c["type"] == "done")


def test_approval_yes_bypasses_fast_lane():
    """A bare "yes" must reach the approval router, never the fast LLM lane."""
    import backend.app.main as m

    async def collect():
        req = m.ChatRequest(message="yes", session_id="test-approval-yes", provider="ollama", conversation_mode=False)
        chunks = []
        async for chunk in m._safe_brain_stream(req):
            chunks.append(json.loads(chunk.decode("utf-8")))
            if len(chunks) > 10:
                break
        return chunks

    chunks = _run(collect())
    routes = [c.get("route", "") for c in chunks]
    assert any(str(r).startswith("approval:") for r in routes)


def _fake_cloud_stream(reply: str, *, reasoning: str = ""):
    async def fake_stream(*args, **kwargs):
        if reasoning:
            yield (reasoning, True)
        yield (reply, False)

    return fake_stream


def test_cloud_provider_uses_fast_lane_and_streams_tokens(monkeypatch):
    """Simple chat with an explicit cloud provider (e.g. Anthropic) must also
    take the fast lane and stream real tokens — not block for a full
    completion through the slow RAG/plan pipeline."""
    import backend.app.main as m

    called = []
    monkeypatch.setattr(
        "shared.omni_brain.retrieve_context",
        lambda *args, **kwargs: (called.append("retrieve_context") or {"hits": []}),
    )
    monkeypatch.setattr(m, "_anthropic_chat_stream", _fake_cloud_stream("Hello from Claude!", reasoning="thinking..."))

    async def collect():
        req = m.ChatRequest(message="hi", session_id="test-cloud-fast", provider="anthropic", model="claude-sonnet-4-6", conversation_mode=False)
        chunks = []
        async for chunk in m._safe_brain_stream(req):
            chunks.append(json.loads(chunk.decode("utf-8")))
            if len(chunks) > 15:
                break
        return chunks

    chunks = _run(collect())
    answer = "".join(c.get("content", "") for c in chunks if c["type"] == "token")
    assert "Hello from Claude!" in answer
    assert any(c.get("route") == "anthropic-fast-path" for c in chunks if c["type"] == "done")
    assert "retrieve_context" not in called, "cloud fast lane should also skip brain retrieval"


def test_cloud_fast_lane_reroutes_high_sensitivity_text_to_local(monkeypatch):
    """A message with proprietary/GxP content must never reach an explicitly
    chosen cloud provider, even through the fast lane."""
    import backend.app.main as m

    async def forbidden_cloud_stream(*args, **kwargs):
        raise AssertionError("high-sensitivity text must not reach the cloud provider")
        yield  # pragma: no cover - make this an async generator

    async def fake_local_default():
        return "ollama", "llama3.2:latest"

    monkeypatch.setattr(m, "_anthropic_chat_stream", forbidden_cloud_stream)
    monkeypatch.setattr(m, "_local_default_provider_model", fake_local_default)
    monkeypatch.setattr(m, "_collect_ollama_stream", _fake_ollama_collector("Routed to local instead."))

    async def collect():
        req = m.ChatRequest(message="what is the batch number for this COA?", session_id="test-cloud-privacy", provider="anthropic", conversation_mode=False)
        chunks = []
        async for chunk in m._safe_brain_stream(req):
            chunks.append(json.loads(chunk.decode("utf-8")))
            if len(chunks) > 15:
                break
        return chunks

    chunks = _run(collect())
    answer = "".join(c.get("content", "") for c in chunks if c["type"] == "token")
    assert "Routed to local instead." in answer
    assert any(c.get("route") == "ollama-fast-path" for c in chunks if c["type"] == "done")


def test_model_list_cache_avoids_redundant_http_calls(monkeypatch):
    """_ollama_models_raw hits a real HTTP endpoint; back-to-back turns should
    not each pay that round trip — this is the routing/agent-model-selection
    path that used to fire 2-4 times per chat turn."""
    import backend.app.main as m

    m._invalidate_model_list_cache("ollama")
    calls = []

    class _FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"models": [{"name": "llama3.2:latest", "details": {}}]}

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, *args, **kwargs):
            calls.append(1)
            return _FakeResponse()

    monkeypatch.setattr(m.httpx, "AsyncClient", lambda *args, **kwargs: _FakeClient())

    async def collect():
        first = await m._ollama_models_raw()
        second = await m._ollama_models_raw()
        return first, second

    first, second = _run(collect())
    assert len(calls) == 1, "second call within the TTL window should hit the cache, not the network"
    assert first == second
