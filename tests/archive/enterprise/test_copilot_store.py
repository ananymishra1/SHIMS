"""P3 tests: persistent per-user Shims chat history with strict ownership."""
from __future__ import annotations

from fastapi.testclient import TestClient

from shims_enterprise import copilot_store as store
from shims_enterprise.app import app


def test_conversation_crud_and_titling():
    store.ensure_copilot_schema()
    cid = store.create_conversation(101, title='', page='/qms')
    assert cid > 0
    store.append_message(cid, 101, 'user', 'Draft a CAPA for the mixer deviation')
    store.append_message(cid, 101, 'assistant', 'Here is a CAPA outline...', events={'route': 'copilot'})
    convo = store.get_conversation(cid, 101)
    assert convo is not None
    # First user message becomes the title (it started as 'New chat').
    assert convo['title'].startswith('Draft a CAPA')
    msgs = store.get_messages(cid, 101)
    assert [m['role'] for m in msgs] == ['user', 'assistant']
    turns = store.recent_turns(cid, 101, limit=6)
    assert turns[-1]['role'] == 'assistant'


def test_ownership_is_enforced():
    store.ensure_copilot_schema()
    cid = store.create_conversation(201, title='secret chat')
    store.append_message(cid, 201, 'user', 'private question')
    # Another user can't read, append, or delete.
    assert store.get_conversation(cid, 202) is None
    assert store.get_messages(cid, 202) == []
    assert store.append_message(cid, 202, 'user', 'intruder') is None
    assert store.delete_conversation(cid, 202) is False
    # Owner can delete.
    assert store.delete_conversation(cid, 201) is True
    assert store.get_conversation(cid, 201) is None


def test_conversation_endpoints_require_login_and_scope():
    client = TestClient(app, raise_server_exceptions=False)
    assert client.get('/api/copilot/conversations').status_code == 401

    client.post('/login', data={'username': 'admin', 'password': 'SHIMS2025!'}, follow_redirects=False)
    resp = client.get('/api/copilot/conversations')
    assert resp.status_code == 200 and resp.json()['ok']
    # Nonexistent conversation → 404, not someone else's data.
    assert client.get('/api/copilot/conversations/99999999').status_code == 404


def test_ingest_rejects_raw_paths_for_non_admin():
    client = TestClient(app, raise_server_exceptions=False)
    client.post('/login', data={'username': 'qc', 'password': 'SHIMS2025!'}, follow_redirects=False)
    resp = client.post('/api/copilot/ingest', json={'file_path': 'C:/Windows/win.ini'})
    assert resp.status_code == 403
