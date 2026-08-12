from fastapi.testclient import TestClient
from backend.app.main import app, _detect_tool_intent, _infer_provider

client = TestClient(app)

def test_health_unified_brain():
    r = client.get('/health')
    assert r.status_code == 200
    data = r.json()
    assert data['capabilities']['image'] is True
    assert data['capabilities']['model_pull'] is True
    assert 'single unified' in data['brain']

def test_tool_intent_image_pdf():
    assert _detect_tool_intent('create an image of a panda relaxing')[0] == 'image'
    assert 'panda' in _detect_tool_intent('create an image of a panda relaxing')[1].lower()
    assert _detect_tool_intent('PDF bana do for qc report')[0] == 'pdf'

def test_provider_routing_claude_not_ollama():
    assert _infer_provider('claude-sonnet-4-6', None) == 'anthropic'
    assert _infer_provider('llama3.2:latest', None) == 'ollama'

def test_media_route_creates_real_file():
    r = client.post('/media/generate', json={'kind': 'pdf', 'prompt': 'Test QC report'})
    assert r.status_code == 200
    data = r.json()
    assert data['ok'] is True
    assert data['type'] == 'pdf'
    assert data['url'].endswith('.pdf')

def test_chat_tool_first_route(monkeypatch):
    """Media requests are routed by the brain model itself: it calls the
    media.create tool and the artifact comes back as a media_result event."""
    async def fake_tool_stream(model, messages, tools, on_delta, **kw):
        # First turn (tools offered, no tool results yet): call media.create.
        if tools and not any("Tool results" in str(m.get("content", "")) for m in messages):
            return {"content": "", "tool_calls": [
                {"function": {"name": "media.create", "arguments": {"kind": "image", "prompt": "a panda relaxing"}}}
            ]}
        # Synthesis turn after the tool result.
        if on_delta:
            await on_delta("Here is your image of a panda relaxing.")
        return {"content": "Here is your image of a panda relaxing.", "tool_calls": []}

    # Native-only routing: provider=ollama resolves to the native engine.
    monkeypatch.setattr("shared.agent_loop._native_chat_stream", fake_tool_stream)
    import backend.app.main as m
    monkeypatch.setattr(m, "_kick_native_load", lambda *a, **k: None)
    with client.stream('POST', '/brain/turn', json={'message': 'create an image of a panda relaxing', 'provider': 'ollama', 'model': 'missing:model'}) as r:
        assert r.status_code == 200
        body = ''.join(r.iter_text())
    assert 'media.create' in body
    assert 'media_result' in body
