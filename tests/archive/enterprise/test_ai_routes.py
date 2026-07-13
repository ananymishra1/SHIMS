"""P4 tests: per-feature AI model routing — precedence, validation, permissions."""
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from shared.ai import AIResult
from shared.database import db
from shared.llm_gateway import (
    LLMGateway,
    ensure_gateway_schema,
    get_feature_routes,
    resolve_route,
    set_feature_route,
)
from shims_enterprise.app import app


def test_route_crud_and_seeds():
    ensure_gateway_schema()
    routes = get_feature_routes()
    # Seeded defaults preserve the pre-routing hardcoded models.
    assert routes.get('chemistry', {}).get('model') == 'qwen2.5:14b'
    set_feature_route('documents', 'anthropic', 'claude-sonnet-4-6', updated_by=1)
    assert resolve_route('documents') == ('anthropic', 'claude-sonnet-4-6')
    set_feature_route('documents', '', '', updated_by=1)  # clear → auto
    assert resolve_route('documents') == (None, None)


def test_explicit_model_beats_route(monkeypatch: pytest.MonkeyPatch):
    """Caller-pinned models must never be overridden by the route table."""
    ensure_gateway_schema()
    set_feature_route('general', 'ollama', 'route-model', updated_by=1)
    seen = {}

    class _Spy:
        async def complete(self, prompt, system='', tools=None, model=None):
            seen['model'] = model
            return AIResult(text='ok', provider='ollama', model=model or '', ok=True, route='ollama')

    import shared.ai as ai_mod
    monkeypatch.setattr(ai_mod, 'get_provider', lambda name=None: _Spy())
    import shared.llm_gateway as gw_mod
    monkeypatch.setattr(gw_mod, '_cloud_configured', lambda name: False)

    gw = LLMGateway()
    asyncio.run(gw.complete('q', feature='general', provider='ollama', model='pinned-model'))
    assert seen['model'] == 'pinned-model'
    # Unpinned call uses the route.
    asyncio.run(gw.complete('q', feature='general', provider='ollama'))
    assert seen['model'] == 'route-model'
    set_feature_route('general', '', '', updated_by=1)


def _admin_client() -> TestClient:
    client = TestClient(app, raise_server_exceptions=False)
    client.post('/login', data={'username': 'admin', 'password': 'SHIMS2025!'}, follow_redirects=False)
    return client


def test_routes_api_requires_admin_write():
    client = TestClient(app, raise_server_exceptions=False)
    client.post('/login', data={'username': 'qc', 'password': 'SHIMS2025!'}, follow_redirects=False)
    resp = client.put('/api/admin/ai-routes', json={'feature_key': 'copilot', 'provider': 'ollama', 'model': 'x'})
    assert resp.status_code == 403


def test_routes_api_validates_input():
    client = _admin_client()
    assert client.put('/api/admin/ai-routes', json={'feature_key': 'nonsense', 'provider': 'ollama'}).status_code == 400
    assert client.put('/api/admin/ai-routes', json={'feature_key': 'copilot', 'provider': 'skynet'}).status_code == 400


def test_usage_summary_endpoint():
    ensure_gateway_schema()
    db.execute("INSERT INTO ai_gateway_usage(feature, provider, model, prompt_chars, completion_chars, latency_ms, ok) "
               "VALUES ('copilot', 'ollama', 'test-model', 10, 20, 123.4, 1)")
    client = _admin_client()
    resp = client.get('/api/admin/ai-usage/summary?days=7')
    assert resp.status_code == 200
    data = resp.json()
    assert data['ok'] and any(r['feature'] == 'copilot' for r in data['by_feature'])
