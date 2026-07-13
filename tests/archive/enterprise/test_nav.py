"""Nav v2 regression tests (P2): every nav URL must resolve to a real route,
Home comes first, role filtering works, and login lands on /home."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from shims_enterprise.app import app
from shims_enterprise.core import ROLE_OPTIONS, nav_for


import re as _re


def _route_paths() -> set[str]:
    return {getattr(r, 'path', '') for r in app.routes}


def _resolves(url: str, paths: set[str]) -> bool:
    """True if a concrete nav URL maps to a real route, including templated
    routes such as /workspace/{key} matching /workspace/rd."""
    if url in paths:
        return True
    for p in paths:
        if '{' not in p:
            continue
        rx = '^' + _re.sub(r'\{[^/}]+\}', r'[^/]+', p) + '$'
        if _re.match(rx, url):
            return True
    return False


def _all_nav_urls(user: dict) -> list[str]:
    urls: list[str] = []
    for entry in nav_for(user):
        if entry.get('type') == 'group':
            urls.extend(item['url'] for item in entry['items'])
        else:
            urls.append(entry['url'])
    return urls


def _fake_user(role: str) -> dict:
    return {'id': 1, 'username': role, 'full_name': role.title(), 'role': role,
            'department': role if role not in {'admin', 'executive'} else 'executive', 'active': 1}


@pytest.mark.parametrize('role', ROLE_OPTIONS)
def test_every_nav_url_resolves(role: str):
    paths = _route_paths()
    for url in _all_nav_urls(_fake_user(role)):
        assert _resolves(url, paths), f'nav for {role} points at dead URL {url}'


def test_home_is_first_for_every_role():
    for role in ROLE_OPTIONS:
        nav = nav_for(_fake_user(role))
        assert nav, f'empty nav for {role}'
        assert nav[0].get('url') == '/home', f'{role} nav does not start with Home'


def test_admin_group_hidden_from_non_admin():
    nav = nav_for(_fake_user('qc'))
    group_labels = {e.get('label') for e in nav if e.get('type') == 'group'}
    assert 'Admin' not in group_labels


def test_workspaces_group_is_role_pinned():
    """Decluttered IA v3: the sidebar shows a short, role-pinned Workspaces group;
    a QC user should see the QC hub first."""
    nav = nav_for(_fake_user('qc'))
    ws = next((e for e in nav if e.get('type') == 'group' and e['label'] == 'Workspaces'), None)
    assert ws is not None, 'Workspaces group missing'
    assert ws['items'][0]['url'] == '/workspace/qc', 'QC user should see the QC hub first'
    # Declutter guarantee: no role faces a huge flat sidebar.
    assert len(ws['items']) <= 6, 'too many pinned workspaces — sidebar not decluttered'


def test_login_lands_on_home():
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post('/login', data={'username': 'admin', 'password': 'SHIMS2025!'}, follow_redirects=False)
    assert resp.status_code in {302, 303}
    assert resp.headers['location'] == '/home'
    page = client.get('/home')
    assert page.status_code == 200
    assert 'work queue' in page.text.lower()


def test_legacy_dashboard_redirects():
    client = TestClient(app, raise_server_exceptions=False)
    client.post('/login', data={'username': 'admin', 'password': 'SHIMS2025!'}, follow_redirects=False)
    for dept, target in [('rd', '/rd/process'), ('qc', '/qc/lab'), ('unknown', '/home')]:
        resp = client.get(f'/dashboard/{dept}', follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers['location'] == target


def test_dead_templates_are_gone():
    from shims_enterprise.core import BASE_DIR
    for name in ('executive.html', 'rd.html', 'qc.html', 'warehouse.html', 'production.html', 'procurement.html'):
        assert not (BASE_DIR / 'templates' / name).exists(), f'{name} should have been deleted'
