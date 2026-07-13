from fastapi.testclient import TestClient
from backend.app.main import app

client = TestClient(app)


def test_health():
    r = client.get('/health')
    assert r.status_code == 200
    data = r.json()
    assert data['capabilities']['chat'] is True
    assert data['capabilities']['image'] is True


def test_media_pdf():
    r = client.post('/media/generate', json={'kind': 'pdf', 'prompt': 'Test PDF content'})
    assert r.status_code == 200
    assert r.json()['kind'] == 'pdf'


def test_models():
    r = client.get('/chat/models')
    assert r.status_code == 200
    assert 'models' in r.json()
