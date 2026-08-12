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
    # A Settings-saved brain model (.env SHIMS_CHAT_*) must not leak into
    # these tests — it changes provider/model resolution by design.
    monkeypatch.delenv("SHIMS_CHAT_PROVIDER", raising=False)
    monkeypatch.delenv("SHIMS_CHAT_MODEL", raising=False)


def _fake_native_tool_stream(reply: str):
    """Fake for shared.agent_loop._native_chat_stream — the tool-aware
    streamer the unified pipeline uses for the native engine (SHIMS is
    native-only for local inference; Ollama is no longer a route)."""

    async def fake_stream(model, messages, tools, on_delta, **_):
        if on_delta:
            await on_delta(reply)
        return {"content": reply, "tool_calls": []}

    return fake_stream


class _FakeSSEStream:
    """Stands in for the lite lane's direct httpx SSE stream to the engine."""
    status_code = 200
    reason_phrase = "OK"

    def __init__(self, reply: str):
        self._reply = reply

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def raise_for_status(self):
        pass

    async def aiter_lines(self):
        yield 'data: {"choices": [{"delta": {"content": ' + json.dumps(self._reply) + '}}]}'
        yield "data: [DONE]"


def _patch_native_lanes(monkeypatch, m, reply: str):
    """Hermetic native engine for unified-lane tests: covers both the full
    lane (agent_loop._native_chat_stream) and the lite lane (direct httpx
    SSE), and stops any real engine load attempt."""
    import httpx
    monkeypatch.setattr("shared.agent_loop._native_chat_stream", _fake_native_tool_stream(reply))
    monkeypatch.setattr(m, "_kick_native_load", lambda *a, **k: None)
    monkeypatch.setattr(httpx.AsyncClient, "stream", lambda self, method, url, **kw: _FakeSSEStream(reply))


def test_greeting_streams_real_llm_reply_via_unified_lane(monkeypatch):
    """Typed "hi" must stream a real LLM reply through the unified lane —
    no canned greeting, no heavyweight agent loop."""
    import backend.app.main as m

    _patch_native_lanes(monkeypatch, m, "Hey! How can I help you today?")

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
    # Native-only routing: provider=ollama resolves to the native engine.
    assert any(str(c.get("route", "")).startswith("native-unified") for c in chunks if c["type"] == "done")


def test_unified_lane_ignores_forced_agent_mode(monkeypatch):
    """agent_mode=true from the UI must not push simple chat into the agent loop."""
    import backend.app.main as m

    _patch_native_lanes(monkeypatch, m, "Doing great, thanks for asking!")

    async def collect():
        req = m.ChatRequest(message="how are you?", session_id="test-agent-mode-chat", provider="ollama", model="llama3.2:latest", conversation_mode=False, agent_mode=True)
        chunks = []
        async for chunk in m._safe_brain_stream(req):
            chunks.append(json.loads(chunk.decode("utf-8")))
            if len(chunks) > 10:
                break
        return chunks

    chunks = _run(collect())
    assert any(str(c.get("route", "")).startswith("native-unified") for c in chunks if c["type"] == "done")
    answer = "".join(c.get("content", "") for c in chunks if c["type"] == "token")
    assert "Doing great" in answer


def test_greeting_unified_lane_under_omnipotent_mode(monkeypatch):
    """Even with omnipotent mode on, a greeting should not block on the agent loop."""
    import backend.app.main as m

    monkeypatch.setenv("SHIMS_OMNIPOTENT_MODE", "true")
    _patch_native_lanes(monkeypatch, m, "Hello!")

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


def test_approval_yes_bypasses_model_turn(tmp_path, monkeypatch):
    """A bare "yes" must reach the deterministic approval router, never the model —
    but only when a pending action was actually surfaced in THIS session (Phase 3
    guard: stale/cross-session/unsurfaced approvals fall through to the model)."""
    import backend.app.main as m

    pending_dir = tmp_path / "pending_actions"
    pending_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(m, "PENDING_ACTION_DIR", pending_dir)
    monkeypatch.setattr(
        m, "record_action",
        lambda *a, **k: {"action_id": "ledger-test", "ledger_hash": "hash-test", "action": {}},
    )
    action = m._create_pending_action(
        action_type="agent_tool",
        title="Run desktop.bridge ping",
        summary="Run desktop.bridge ping on the paired desktop",
        payload={"tool": "desktop.bridge", "args": {"action": "ping"}},
        session_id="test-approval-yes",
        risk="desktop_access",
    )
    m._surface_pending_action(action, "test-approval-yes")

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


def test_cloud_provider_uses_unified_lane_and_streams_tokens(monkeypatch):
    """Simple chat with an explicit cloud provider (e.g. Anthropic) must also
    stream real tokens live through the unified lane — not block for a full
    completion."""
    import backend.app.main as m

    # Keep the explicit cloud choice pinned even when no API key is configured
    # in the test environment (otherwise resolution falls back to a local model).
    monkeypatch.setattr(m, "_provider_configured", lambda provider: True)
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
    assert any(str(c.get("route", "")).startswith("anthropic-unified") for c in chunks if c["type"] == "done")


def test_unified_lane_reroutes_high_sensitivity_text_to_local(monkeypatch):
    """A message with proprietary/GxP content must never reach an explicitly
    chosen cloud provider."""
    import backend.app.main as m

    async def forbidden_cloud_stream(*args, **kwargs):
        raise AssertionError("high-sensitivity text must not reach the cloud provider")
        yield  # pragma: no cover - make this an async generator

    monkeypatch.setattr(m, "_anthropic_chat_stream", forbidden_cloud_stream)
    _patch_native_lanes(monkeypatch, m, "Routed to local instead.")

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
    assert any(str(c.get("route", "")).startswith("native-unified") for c in chunks if c["type"] == "done")


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
