"""Unit tests for the resilient LLM gateway (P1 stabilization).

Covers: provider fallback order, fast-failure retry, circuit breaker, the
LLMUnavailable surface used by the agent loop, and usage logging — all with
mocked providers/transports (no network).
"""
from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from shared.ai import AIResult
from shared.database import db
from shared.llm_gateway import LLMGateway, LLMUnavailable, ensure_gateway_schema


class _FakeProvider:
    def __init__(self, name: str, script: list[AIResult | Exception]) -> None:
        self.name = name
        self.script = list(script)
        self.calls = 0

    async def complete(self, prompt: str, system: str = '', tools: Any = None, model: Any = None) -> AIResult:
        self.calls += 1
        step = self.script.pop(0) if self.script else self.script_default()
        if isinstance(step, Exception):
            raise step
        return step

    def script_default(self) -> AIResult:
        return AIResult(text='default', provider=self.name, ok=True, route=self.name)


def _ok(provider: str) -> AIResult:
    return AIResult(text=f'answer from {provider}', provider=provider, ok=True, route=provider)


def _fail(provider: str, error: str = 'connection refused') -> AIResult:
    return AIResult(text='', provider=provider, ok=False, error=error, route=f'{provider}:fallback')


def _patch_providers(monkeypatch: pytest.MonkeyPatch, providers: dict[str, _FakeProvider]) -> None:
    import shared.ai as ai_mod

    def fake_get_provider(name=None):
        return providers[(name or 'ollama').lower().strip()]

    monkeypatch.setattr(ai_mod, 'get_provider', fake_get_provider)
    # Keep the chain deterministic regardless of what's in .env / the DB.
    import shared.llm_gateway as gw_mod
    monkeypatch.setattr(gw_mod, '_cloud_configured', lambda name: False)


def test_fallback_to_next_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr('shared.llm_gateway._RETRY_BACKOFF', 0.01)
    providers = {
        'anthropic': _FakeProvider('anthropic', [_fail('anthropic', 'HTTP 503'), _fail('anthropic', 'HTTP 503')]),
        'ollama': _FakeProvider('ollama', [_ok('ollama')]),
    }
    _patch_providers(monkeypatch, providers)
    gw = LLMGateway()
    result = asyncio.run(gw.complete('hi', feature='test', provider='anthropic'))
    assert result.ok and result.provider == 'ollama'


def test_retry_on_fast_transient_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr('shared.llm_gateway._RETRY_BACKOFF', 0.01)
    providers = {
        'ollama': _FakeProvider('ollama', [_fail('ollama', 'connect timeout'), _ok('ollama')]),
    }
    _patch_providers(monkeypatch, providers)
    gw = LLMGateway()
    result = asyncio.run(gw.complete('hi', feature='test', provider='ollama'))
    assert result.ok and providers['ollama'].calls == 2


def test_circuit_breaker_opens_and_skips(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr('shared.llm_gateway._RETRY_BACKOFF', 0.01)
    providers = {
        'anthropic': _FakeProvider('anthropic', [_fail('anthropic', 'HTTP 500 x')] * 10),
        'ollama': _FakeProvider('ollama', [_ok('ollama')] * 10),
    }
    _patch_providers(monkeypatch, providers)
    gw = LLMGateway()
    for _ in range(3):
        asyncio.run(gw.complete('hi', feature='test', provider='anthropic'))
    assert gw.breaker_open('anthropic')
    calls_before = providers['anthropic'].calls
    result = asyncio.run(gw.complete('hi', feature='test', provider='anthropic'))
    assert result.ok and result.provider == 'ollama'
    assert providers['anthropic'].calls == calls_before  # breaker skipped it


def test_never_raises_returns_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr('shared.llm_gateway._RETRY_BACKOFF', 0.01)
    providers = {
        'ollama': _FakeProvider('ollama', [RuntimeError('boom')] * 5),
    }
    _patch_providers(monkeypatch, providers)
    gw = LLMGateway()
    result = asyncio.run(gw.complete('hi', feature='test', provider='ollama'))
    assert isinstance(result, AIResult)  # degraded, but no exception


def test_chat_messages_raises_llm_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    import shared.agent_loop as loop_mod

    async def dead_transport(model, messages, tools, timeout=120.0):
        raise httpx.ConnectError('connection refused')

    monkeypatch.setattr(loop_mod, '_ollama_chat_raw', dead_transport)
    monkeypatch.setattr('shared.llm_gateway._RETRY_BACKOFF', 0.01)
    gw = LLMGateway()
    with pytest.raises(LLMUnavailable) as exc_info:
        asyncio.run(gw.chat_messages('ollama', 'llama3.2', [{'role': 'user', 'content': 'hi'}], []))
    assert exc_info.value.code == 'unreachable'
    assert exc_info.value.provider == 'ollama'


def test_chat_messages_circuit_open(monkeypatch: pytest.MonkeyPatch) -> None:
    gw = LLMGateway()
    gw._breaker['ollama'] = {'fails': 3, 'open_until': 9e12}
    with pytest.raises(LLMUnavailable) as exc_info:
        asyncio.run(gw.chat_messages('ollama', 'llama3.2', [{'role': 'user', 'content': 'hi'}], []))
    assert exc_info.value.code == 'circuit_open'


def test_usage_logged(monkeypatch: pytest.MonkeyPatch) -> None:
    ensure_gateway_schema()
    providers = {'ollama': _FakeProvider('ollama', [_ok('ollama')])}
    _patch_providers(monkeypatch, providers)
    gw = LLMGateway()
    asyncio.run(gw.complete('hello usage', feature='usage-test', provider='ollama'))
    rows = db.query("SELECT * FROM ai_gateway_usage WHERE feature='usage-test' ORDER BY id DESC LIMIT 1")
    assert rows and rows[0]['ok'] == 1 and rows[0]['provider'] == 'ollama'
    assert rows[0]['prompt_chars'] == len('hello usage')
