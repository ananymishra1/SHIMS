from fastapi.testclient import TestClient
from backend.app.main import app, _detect_search_intent, _format_search_answer


def test_v14_search_intent_detection():
    # Temporal keywords like "current" are now kept — they matter for freshness.
    assert _detect_search_intent('search the web for current GST e invoice rules') == 'current GST e invoice rules'
    assert _detect_search_intent('normal chat please') is None
    assert _detect_search_intent('normal chat please', web_mode=True) is None
    assert _detect_search_intent('hi', web_mode=True) is None
    assert _detect_search_intent('research fluconazole patent route', web_mode=True) == 'fluconazole patent route'


def test_v14_web_health_and_search_endpoint_no_crash(monkeypatch):
    c = TestClient(app)
    r = c.get('/web/health')
    assert r.status_code == 200
    assert 'status' in r.json()
    async def fake_search(query, max_results=6, provider=None):
        return {'ok': True, 'query': query, 'provider': 'test', 'results': [{'title': 'One', 'url': 'https://example.com', 'snippet': 'Example result'}]}
    import backend.app.main as m
    monkeypatch.setattr(m, '_web_search', fake_search)
    r2 = c.post('/web/search', json={'query': 'shims test', 'max_results': 1})
    assert r2.status_code == 200
    assert r2.json()['results'][0]['title'] == 'One'


def test_v14_brain_web_mode_routes_to_search(monkeypatch):
    """Web-intent turns are routed by the brain model itself: it calls the
    web.search tool and results stream back as a search event."""
    c = TestClient(app)
    async def fake_search(query, max_results=6, provider=None, planned_query=None):
        return {'ok': True, 'query': query, 'provider': 'test', 'results': [{'title': 'One', 'url': 'https://example.com', 'snippet': 'Example result'}]}
    async def fake_tool_stream(model, messages, tools, on_delta, **kw):
        if tools and not any("Tool results" in str(m.get("content", "")) for m in messages):
            return {"content": "", "tool_calls": [
                {"function": {"name": "web.search", "arguments": {"query": "current AI news"}}}
            ]}
        if on_delta:
            await on_delta("Here is the current AI news.")
        return {"content": "Here is the current AI news.", "tool_calls": []}
    import backend.app.main as m
    monkeypatch.setattr(m, '_web_search', fake_search)
    # Native-only routing: provider=ollama resolves to the native engine, so
    # the unified full lane streams via agent_loop._native_chat_stream.
    monkeypatch.setattr("shared.agent_loop._native_chat_stream", fake_tool_stream)
    monkeypatch.setattr(m, "_kick_native_load", lambda *a, **k: None)
    with c.stream('POST', '/brain/turn', json={'message': 'what is current AI news', 'web_mode': True, 'source': 'typed', 'provider': 'ollama'}) as r:
        body = ''.join(r.iter_text())
    assert 'web.search' in body
    assert 'Example result' in body


def test_v14_realtime_and_agents_endpoints():
    c = TestClient(app)
    assert c.get('/realtime/status').json()['features']['half_duplex'] is True
    agents = c.get('/agents/list').json()['agents']
    assert any(a['id'] == 'search' for a in agents)


def test_v14_format_search_answer():
    ans = _format_search_answer({'ok': True, 'query': 'x', 'provider': 'test', 'results': [{'title':'T','url':'U','snippet':'S'}]})
    assert 'Provider: test' in ans
    assert 'T' in ans and 'U' in ans


def test_v14_3_web_mode_does_not_search_greetings(monkeypatch):
    c = TestClient(app)
    async def fake_search(query, max_results=6, provider=None):
        raise AssertionError('search should not be called for greetings')
    import backend.app.main as m
    monkeypatch.setattr(m, '_web_search', fake_search)
    with c.stream('POST', '/brain/turn', json={'message': 'hi', 'web_mode': True, 'source': 'typed', 'provider': 'ollama'}) as r:
        body = ''.join(r.iter_text())
    # "hi" now gets a real LLM reply (fast lane) instead of a canned greeting;
    # the guarantee under test is only that no web search fires (the patched
    # _web_search raises if called). Check chunk routes, not raw substrings —
    # retrieved memory excerpts can legitimately contain "tool:web_search".
    import json as _json
    chunks = [_json.loads(line) for line in body.splitlines() if line.strip()]
    assert any(c.get('type') == 'done' for c in chunks)
    assert all(c.get('route') != 'tool:web_search' for c in chunks)
