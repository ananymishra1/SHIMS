from fastapi.testclient import TestClient

from backend.app.main import app as omni
from shims_enterprise.app import app as enterprise


def test_stale_anthropic_provider_with_local_model_routes_ollama():
    c = TestClient(omni)
    with c.stream('POST', '/brain/turn', json={'message': 'what is 2 plus 2', 'provider': 'anthropic', 'model': 'llama3.2:latest'}) as r:
        body = ''.join(r.iter_text())
    assert '"provider": "ollama"' in body
    assert 'local-model-overrides-stale-provider' in body


def test_image_tool_uses_backend_file_generation_even_with_cloud_model_stale():
    c = TestClient(omni)
    with c.stream('POST', '/brain/turn', json={'message': 'create an image of a panda relaxing', 'provider': 'ollama', 'model': 'claude-sonnet-4-6'}) as r:
        body = ''.join(r.iter_text())
    assert 'tool:image' in body
    assert '/media/files/images/' in body


def test_ppt_generation_route():
    c = TestClient(omni)
    r = c.post('/documents/generate', json={'kind': 'pptx', 'title': 'Launch deck', 'content': 'Overview; Enterprise; Omni'})
    assert r.status_code == 200
    assert r.json()['url'].endswith('.pptx')


def test_enterprise_document_studio_and_gst():
    c = TestClient(enterprise)
    r = c.post('/login', data={'username': 'admin', 'password': 'SHIMS2025!'}, follow_redirects=False)
    assert r.status_code in (302, 303)
    cookie = r.headers.get('set-cookie')
    assert c.get('/documents', headers={'cookie': cookie}).status_code == 200
    gst = c.post('/api/gst/invoice', data={'invoice_no': 'INVTEST', 'buyer_name': 'Customer', 'buyer_gstin': '27BBBBB0000B1Z5', 'item_name': 'API', 'hsn': '2933', 'qty': '2', 'rate': '100', 'gst_rate': '18'}, headers={'cookie': cookie})
    assert gst.status_code == 200
    assert 'application/pdf' in gst.headers.get('content-type', '')
