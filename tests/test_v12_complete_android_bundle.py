from fastapi.testclient import TestClient
from backend.app.main import app as omni
from shims_enterprise.app import app as enterprise
from shared.enterprise_documents import create_ewaybill_draft, pin_distance_check
from shared.provider_registry import decide_provider
from shared.telemetry import ledger_document, verify_document


def test_provider_registry_stale_cloud_goes_local_when_local_selected(monkeypatch):
    monkeypatch.delenv('ANTHROPIC_API_KEY', raising=False)
    d = decide_provider('anthropic', 'llama3.2:latest', installed_local=['llama3.2:latest'], default_local='llama3.2:latest')
    assert d.provider == 'ollama'


def test_telemetry_ledger_detects_modified_file(tmp_path):
    f = tmp_path / 'ledger_test.txt'
    f.write_text('original', encoding='utf-8')
    assert ledger_document(f, 'test')['ok'] is True
    assert verify_document(f)['ok'] is True
    f.write_text('changed', encoding='utf-8')
    assert verify_document(f)['ok'] is False


def test_evolution_status_and_reflection_endpoint():
    c = TestClient(omni)
    r = c.post('/evolution/reflect')
    assert r.status_code == 200
    assert r.json()['ok'] is True
    s = c.get('/evolution/status')
    assert s.status_code == 200
    assert 'daily_lessons' in s.json()


def test_generated_media_is_ledgered():
    c = TestClient(omni)
    r = c.post('/media/generate', json={'kind': 'pdf', 'prompt': 'Ledgered QC test document'})
    assert r.status_code == 200
    data = r.json()
    assert data['ok'] is True
    assert data.get('verified') is True
    assert data.get('sha256')


def test_ewaybill_distance_and_payload():
    check = pin_distance_check('456664', '560066', 1200)
    assert check['ok'] is True
    assert 'within_tolerance' in check
    result = create_ewaybill_draft({'doc_no': 'INV13', 'to_pin': '560066', 'distance': '1200', 'vehicle_no': 'MP13AB1234'})
    assert result['ok'] is True
    assert result['payload']['EwayBillDtls']['docNo'] == 'INV13'


def test_enterprise_ewaybill_endpoint_after_login():
    c = TestClient(enterprise)
    login = c.post('/login', data={'username': 'admin', 'password': 'SHIMS2025!'}, follow_redirects=False)
    assert login.status_code in (302, 303)
    cookie = login.headers.get('set-cookie')
    r = c.post('/api/gst/ewaybill-json', json={'doc_no': 'INV-EWB-1', 'to_pin': '560066', 'distance': '1200', 'vehicle_no': 'MP13AB1234'}, headers={'cookie': cookie})
    assert r.status_code == 200
    assert r.json()['ok'] is True
