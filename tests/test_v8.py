
from fastapi.testclient import TestClient
from backend.app.main import app, _detect_tool_intent

client = TestClient(app)


def test_media_intent_create_an_image():
    result = _detect_tool_intent('create an image of a panda relaxing')
    assert result is not None
    assert result[0] == 'image'
    assert 'panda' in result[1]


def test_pdf_intent():
    result = _detect_tool_intent('PDF bana do for qc report')
    assert result is not None
    assert result[0] == 'pdf'


def test_health():
    r = client.get('/health')
    assert r.status_code == 200
    assert r.json()['capabilities']['image'] is True
