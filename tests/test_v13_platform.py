from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.main import app as omni
from shims_enterprise.app import app as enterprise
from shared.autonomy import check_autonomy


def test_v13_mcp_and_autonomy_policy_available():
    c = TestClient(omni)
    m = c.get('/api/v13/mcp/manifest')
    assert m.status_code == 200
    assert m.json()['security']['human_approval_required_for_source_patch'] is True
    p = c.get('/api/v13/autonomy/policy')
    assert p.status_code == 200
    assert 'batch_release' in p.json()['never_autonomous_actions']


def test_gxp_never_autonomous_gate():
    d = check_autonomy('batch_release', 'L4')
    assert d['allowed'] is False
    assert d['requires_human_approval'] is True
    assert d['effective_level'] == 'L1'


def test_self_evolution_real_propose_validate_apply_pipeline():
    c = TestClient(omni)
    rel = 'shared/generated_skills/v13_self_evolution_smoke.py'
    content = 'VALUE = "v13-applied"\n\ndef hello():\n    return VALUE\n'
    r = c.post('/evolution/propose', json={'relative_path': rel, 'new_content': content, 'reason': 'pytest verifies proposals are actually applied', 'scope': 'skill', 'author': 'pytest'})
    assert r.status_code == 200, r.text
    proposal_id = r.json()['proposal_id']
    v = c.post(f'/evolution/validate/{proposal_id}', json={})
    assert v.status_code == 200, v.text
    assert v.json()['ok'] is True
    bad = c.post(f'/evolution/apply/{proposal_id}', json={'approved_by': 'pytest'})
    assert bad.status_code == 200
    assert bad.json()['ok'] is False
    a = c.post(f'/evolution/apply/{proposal_id}', json={'approved_by': 'pytest', 'approval_phrase': 'I_APPROVE_SHIMS_PATCH'})
    assert a.status_code == 200, a.text
    assert a.json()['ok'] is True
    target = Path(__file__).resolve().parents[1] / rel
    assert target.exists()
    assert 'v13-applied' in target.read_text(encoding='utf-8')


def test_v13_chat_alias_still_uses_unified_brain():
    c = TestClient(omni)
    with c.stream('POST', '/api/v13/chat/turn', json={'message': 'hey shims', 'provider': 'ollama', 'model': 'llama3.2:latest', 'session_id': 'v13-greeting'}) as r:
        body = ''.join(r.iter_text())
    assert 'unified-v13' in body
    assert 'local:greeting' in body


def test_enterprise_v13_stack_and_autonomy():
    c = TestClient(enterprise)
    r = c.get('/api/v13/stack')
    assert r.status_code == 200
    assert 'QMS' in r.json()['stack']
    d = c.post('/api/v13/autonomy/check', json={'action': 'capa_closure', 'requested_level': 'L4'})
    assert d.status_code == 200
    assert d.json()['allowed'] is False
