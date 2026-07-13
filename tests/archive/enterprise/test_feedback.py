"""P5 tests: feedback capture → learned preferences → prompt injection."""
from __future__ import annotations

from fastapi.testclient import TestClient

from shared.database import db
from shared.enterprise_memory import learned_preferences, record_feedback_memory
from shims_enterprise.app import app
from shims_enterprise.copilot_store import ensure_copilot_schema, record_feedback


def test_record_feedback_row_and_memory():
    ensure_copilot_schema()
    fid = record_feedback(301, rating=-1, feature='copilot', comment='too verbose')
    row = db.one('SELECT * FROM ai_feedback WHERE id=?', (fid,))
    assert row and row['rating'] == -1 and row['comment'] == 'too verbose'

    result = record_feedback_memory(301, 'qc', rating=-1,
                                    message='Summarize the open deviations',
                                    comment='Wanted a table, got prose')
    assert result.get('ok')
    prefs = learned_preferences(301, 'qc')
    assert prefs and prefs[0]['memory_type'] == 'anti_pattern'
    assert 'Wanted a table' in prefs[0]['value']


def test_positive_feedback_becomes_preference():
    record_feedback_memory(302, 'rd', rating=1, message='Compare yields against corpus average')
    prefs = learned_preferences(302, 'rd')
    assert prefs and prefs[0]['memory_type'] == 'learned_preference'


def test_feedback_endpoint():
    client = TestClient(app, raise_server_exceptions=False)
    assert client.post('/api/copilot/feedback', json={'rating': 1}).status_code == 401
    client.post('/login', data={'username': 'admin', 'password': 'SHIMS2025!'}, follow_redirects=False)
    resp = client.post('/api/copilot/feedback', json={
        'rating': -1, 'feature': 'copilot', 'comment': 'wrong product',
        'message': 'yield for Fluconazole', 'answer': 'The yield for Ketoconazole...',
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data['ok'] and data['feedback_id'] > 0
