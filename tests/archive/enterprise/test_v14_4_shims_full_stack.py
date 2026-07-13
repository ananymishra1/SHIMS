from fastapi.testclient import TestClient
from backend.app.main import app as omni_app
from shims_enterprise.app import app as enterprise_app


def test_v14_4_shims_health_includes_full_omni_capabilities():
    c = TestClient(omni_app)
    data = c.get('/health').json()
    assert data['ok'] is True
    caps = data['capabilities']
    for key in ['chat','voice','tts','image','pdf','ppt','audio','video','model_pull','web_search','realtime_kernel','voice_profiles']:
        assert caps[key] is True


def test_v14_4_hi_never_triggers_web_search_even_if_web_mode_enabled(monkeypatch):
    import backend.app.main as m
    async def forbidden_search(*args, **kwargs):
        raise AssertionError('web search must not run for greeting')
    monkeypatch.setattr(m, '_web_search', forbidden_search)
    c = TestClient(omni_app)
    with c.stream('POST', '/brain/turn', json={'message':'hi', 'web_mode': True, 'provider':'ollama', 'source':'typed'}) as r:
        body = ''.join(r.iter_text())
    # "hi" now gets a real LLM reply (fast lane) instead of a canned greeting;
    # the guarantee under test is only that no web search fires (the patched
    # _web_search raises if called). Check chunk routes, not raw substrings —
    # retrieved memory excerpts can legitimately contain "tool:web_search".
    import json as _json
    chunks = [_json.loads(line) for line in body.splitlines() if line.strip()]
    assert any(c.get('type') == 'done' for c in chunks)
    assert all(c.get('route') != 'tool:web_search' for c in chunks)


def test_v14_4_shims_tool_first_media_outputs_are_real_files():
    c = TestClient(omni_app)
    img = c.post('/media/generate', json={'kind':'image', 'prompt':'test panda relaxing'}).json()
    assert img['ok'] is True and img['file_url'].startswith('/media/files/')
    pdf = c.post('/documents/generate', json={'kind':'pdf', 'title':'v14.4 test pdf', 'content':'validated'}).json()
    assert pdf['ok'] is True and pdf['file_url'].endswith('.pdf')
    ppt = c.post('/documents/generate', json={'kind':'pptx', 'title':'v14.4 deck', 'content':'one;two'}).json()
    assert ppt['ok'] is True and ppt['file_url'].endswith('.pptx')


def test_v14_4_voice_profile_and_tts_routes_exist_without_cloud():
    c = TestClient(omni_app)
    assert c.get('/voice/config').status_code == 200
    assert c.get('/voice/profiles').json()['ok'] is True
    # Do not force a pyttsx3 synthesis in CI; browser TTS is runtime-dependent.
    # The route is covered manually on Windows, and the frontend uses speechSynthesis first.


def test_v14_4_enterprise_still_has_gmp_powerhouse_after_shims_fixes():
    c = TestClient(enterprise_app)
    r = c.post('/login', data={'username':'admin','password':'SHIMS2025!'}, follow_redirects=False)
    assert r.status_code in (302, 303)
    for path in ['/executive/control','/production/equipment','/rd/tech-transfer','/api/v14.2/enterprise/gmp-powerhouse']:
        res = c.get(path)
        assert res.status_code == 200, path
