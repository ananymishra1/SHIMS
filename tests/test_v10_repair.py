from fastapi.testclient import TestClient
from backend.app.main import app, _clean_secret
from apps.shims_enterprise.main import app as enterprise_app

omni = TestClient(app)
enterprise = TestClient(enterprise_app)

def test_silence_is_ignored_not_spoken():
    with omni.stream('POST', '/brain/turn', json={'message':'   ', 'source':'voice'}) as r:
        body = ''.join(r.iter_text())
    assert 'empty_or_silence' in body or 'silence_or_duplicate' in body
    assert 'I heard silence' not in body
    assert "Say 'Hey SHIMS'" not in body


def test_clean_secret_removes_bearer_and_quotes():
    assert _clean_secret('  "Bearer sk-ant-test"  ') == 'sk-ant-test'


def test_provider_keys_shape_and_save():
    r = omni.post('/system/provider-keys', json={'provider':'anthropic', 'api_key':'Bearer sk-ant-dummy', 'model':'claude-sonnet-4-6'})
    assert r.status_code == 200
    data = r.json()
    assert data['configured'] is True
    r2 = omni.get('/system/provider-keys')
    assert r2.status_code == 200
    assert 'anthropic' in r2.json()['providers']


def test_enterprise_has_login_and_dashboards():
    r = enterprise.get('/')
    assert r.status_code == 200
    assert 'Login' in r.text or 'password' in r.text.lower()
    r2 = enterprise.get('/health')
    assert r2.status_code == 200
    assert r2.json()['ok'] is True


def test_enterprise_admin_login_redirects():
    r = enterprise.post('/login', data={'username':'admin','password':'SHIMS2025!'}, follow_redirects=False)
    assert r.status_code in (302, 303)
    assert 'shims_enterprise_user' in r.headers.get('set-cookie','')
