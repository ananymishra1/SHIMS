"""P6 tests: multi-line GRN receipt via bracket-field form encoding, and
warehouse-role access to the GRN endpoint that lives on their page."""
from __future__ import annotations

from fastapi.testclient import TestClient

from shared.database import db
from shims_enterprise.app import app


def _login(username: str) -> TestClient:
    client = TestClient(app, raise_server_exceptions=False)
    client.post('/login', data={'username': username, 'password': 'SHIMS2025!'}, follow_redirects=False)
    return client


def _grn_form(n_lines: int) -> dict[str, str]:
    vendor = db.one('SELECT id FROM vendors LIMIT 1')
    form = {
        'vendor_id': str(vendor['id'] if vendor else 1),
        'receipt_date': '2026-06-12',
        'notes': 'multi-line test',
    }
    for i in range(n_lines):
        form[f'line_items[{i}][material_name]'] = f'GRN Test Material {i}'
        form[f'line_items[{i}][quantity]'] = str(10 + i)
        form[f'line_items[{i}][unit]'] = 'kg'
        form[f'line_items[{i}][batch_no]'] = f'VB-{i:03d}'
        form[f'line_items[{i}][expiry_date]'] = '2027-06-12'
    return form


def test_three_line_grn_creates_receipt():
    client = _login('admin')
    resp = client.post('/api/procurement/grn', data=_grn_form(3),
                       headers={'Accept': 'application/json'})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data['ok'] and data['id']
    grn = db.one('SELECT * FROM grn_receipts WHERE id=?', (data['id'],))
    assert grn is not None
    items = db.query('SELECT * FROM grn_line_items WHERE grn_id=?', (data['id'],))
    assert len(items) == 3, f'expected 3 GRN line items, got {len(items)}'


def test_warehouse_role_can_post_grn():
    client = _login('warehouse')
    resp = client.post('/api/procurement/grn', data=_grn_form(1),
                       headers={'Accept': 'application/json'})
    assert resp.status_code == 200, resp.text
    assert resp.json()['ok']
