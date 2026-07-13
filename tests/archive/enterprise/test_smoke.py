from fastapi.testclient import TestClient

from shims_omni.app import app as omni_app
from shims_enterprise.app import app as enterprise_app


def test_omni_health_and_chat():
    with TestClient(omni_app) as client:
        r = client.get('/health')
        assert r.status_code == 200
        assert r.json()['independent'] is True
        r = client.post('/api/chat', json={'message': 'hello', 'provider': 'fallback'})
        assert r.status_code == 200
        assert 'answer' in r.json()


def test_enterprise_login_and_dashboard():
    with TestClient(enterprise_app) as client:
        r = client.post('/login', data={'username': 'admin', 'password': 'SHIMS2025!'}, follow_redirects=False)
        assert r.status_code == 303
        r = client.get('/dashboard/executive')
        assert r.status_code == 200


def test_bridge_disabled_by_default():
    with TestClient(enterprise_app) as client:
        r = client.post('/api/bridge/command', json={'command': 'summary', 'payload': {}}, headers={'X-Bridge-Token': 'change-me-bridge-token'})
        assert r.status_code in (403, 401)
