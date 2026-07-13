import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault('SHIMS_DB_PATH', ':memory:')

import pytest
from fastapi.testclient import TestClient

from shared.database import db
from shims_enterprise.app import app
from shared.rd_lab import ensure_rd_lab_schema
from shared.security import hash_password, sign_value


@pytest.fixture
def client():
    ensure_rd_lab_schema()
    pwd_hash = hash_password('testpass')
    db.execute("DELETE FROM users WHERE username='testrd'")
    db.execute(
        "INSERT INTO users(username, full_name, password_hash, role, department, active) VALUES (?, ?, ?, ?, ?, ?)",
        ('testrd', 'Test RD', pwd_hash, 'rd_lead', 'rd', 1),
    )
    user = db.one("SELECT id FROM users WHERE username='testrd'")
    with TestClient(app) as c:
        c.cookies.set('shims_enterprise_user', sign_value(str(user['id'])))
        yield c


def test_create_blank_experiment(client):
    r = client.post('/api/rd/v2/experiments', json={
        'product_name': 'Testol', 'route_name': 'Route A', 'raw_materials': [], 'solvents': []
    })
    assert r.status_code == 200
    data = r.json()
    assert data['ok'] and data['id'] > 0


def test_experiment_crud(client):
    r = client.post('/api/rd/v2/experiments', json={
        'product_name': 'Testol', 'route_name': 'Route A', 'ksm': 'A', 'solvent': 'B', 'catalyst': 'C',
        'raw_materials': [{'name': 'RM-A', 'quantity': 100, 'unit': 'g', 'unit_type': 'mass'}],
        'solvents': []
    })
    eid = r.json()['id']

    r = client.get(f'/api/rd/v2/experiments/{eid}')
    assert r.status_code == 200
    data = r.json()
    assert data['experiment']['product_name'] == 'Testol'
    assert len(data['raw_materials']) == 1

    r = client.put(f'/api/rd/v2/experiments/{eid}', json={'status': 'active', 'route_name': 'Route B'})
    assert r.status_code == 200

    r = client.get(f'/api/rd/v2/experiments/{eid}')
    assert r.json()['experiment']['status'] == 'active'

    r = client.delete(f'/api/rd/v2/experiments/{eid}')
    assert r.status_code == 200
    r = client.get(f'/api/rd/v2/experiments/{eid}')
    assert r.status_code == 404


def test_stage_and_stage_rm_logging(client):
    r = client.post('/api/rd/v2/experiments', json={
        'product_name': 'Testol', 'route_name': '',
        'raw_materials': [{'name': 'Aspirin', 'quantity': 100, 'unit': 'g', 'unit_type': 'mass', 'molecular_weight_g_mol': 180}],
        'solvents': []
    })
    eid = r.json()['id']
    r = client.post(f'/api/rd/v2/experiments/{eid}/stages', json={'stage_no': 1, 'stage_name': 'Mix', 'output_qty': 80})
    sid = r.json()['id']
    rm = client.get(f'/api/rd/v2/experiments/{eid}').json()['raw_materials'][0]
    r = client.post(f'/api/rd/v2/stages/{sid}/raw-materials', json={
        'experiment_id': eid,
        'experiment_raw_material_id': rm['id'],
        'rm_name': 'Aspirin',
        'planned_qty': 100,
        'planned_unit': 'g',
        'actual_qty_used': 100,
        'actual_unit': 'g',
        'wastage_qty': 0,
        'recovery_qty': 0,
    })
    assert r.status_code == 200
    data = client.get(f'/api/rd/v2/experiments/{eid}').json()
    assert data['material_balance']['total_actual_rm_used_kg_eq'] == pytest.approx(0.1, abs=1e-6)
    assert data['ledger']['raw_materials'][0]['remaining_qty'] == 0


def test_measurements_tests_notes(client):
    r = client.post('/api/rd/v2/experiments', json={'product_name': 'Testol', 'route_name': '', 'raw_materials': [], 'solvents': []})
    eid = r.json()['id']
    r = client.post(f'/api/rd/v2/experiments/{eid}/stages', json={'stage_no': 1, 'stage_name': 'Mix'})
    sid = r.json()['id']

    r = client.post(f'/api/rd/v2/experiments/{eid}/measurements', json={
        'stage_id': sid, 'measurement_type': 'pH', 'value': 6.5, 'unit': '', 'target_min': 5.5, 'target_max': 7.5
    })
    assert r.status_code == 200
    r = client.post(f'/api/rd/v2/experiments/{eid}/tests', json={
        'stage_id': sid, 'test_name': 'HPLC purity', 'result_value': '99.2', 'result_unit': '%', 'pass_fail': 'pass'
    })
    assert r.status_code == 200
    r = client.post(f'/api/rd/v2/experiments/{eid}/notes', json={
        'stage_id': sid, 'note_type': 'observation', 'content': 'Reaction turned clear.'
    })
    assert r.status_code == 200

    data = client.get(f'/api/rd/v2/experiments/{eid}').json()
    assert len(data['measurements']) == 1
    assert data['measurements'][0]['is_within_spec'] == 1
    assert len(data['tests']) == 1
    assert len(data['notes']) == 1


def test_impurities(client):
    r = client.post('/api/rd/v2/experiments', json={'product_name': 'Testol', 'route_name': '', 'raw_materials': [], 'solvents': []})
    eid = r.json()['id']
    r = client.post(f'/api/rd/v2/experiments/{eid}/impurities', json={
        'impurity_name': 'Imp A', 'value_pct': 0.15, 'impurity_type': 'known'
    })
    assert r.status_code == 200
    data = client.get(f'/api/rd/v2/experiments/{eid}').json()
    assert len(data['impurities']) == 1
    assert data['impurities'][0]['value_pct'] == pytest.approx(0.15, abs=1e-6)


def test_compare_numeric(client):
    exps = []
    for yield_pct in [90, 85]:
        r = client.post('/api/rd/v2/experiments', json={'product_name': 'Comp', 'route_name': '', 'raw_materials': [], 'solvents': []})
        eid = r.json()['id']
        client.post(f'/api/rd/v2/experiments/{eid}/stages', json={'stage_no': 1, 'stage_name': 'S1', 'actual_yield_pct': yield_pct, 'purity_pct': 99})
        exps.append(eid)
    r = client.post('/api/rd/v2/experiments/compare', json={'experiment_ids': exps, 'skip_ai': True})
    assert r.status_code == 200
    data = r.json()
    assert data['numeric']['best_by_yield'] == exps[0]


def test_trace(client):
    r = client.post('/api/rd/v2/experiments', json={'product_name': 'TraceTest', 'route_name': '', 'raw_materials': [], 'solvents': []})
    eid = r.json()['id']
    client.post(f'/api/rd/v2/experiments/{eid}/notes', json={'note_type': 'observation', 'content': 'Start'})
    r = client.get(f'/api/rd/v2/experiments/{eid}/trace')
    assert r.status_code == 200
    assert any(item['source'] == 'note' for item in r.json()['trace'])


def test_product_assist_no_ai(client):
    r = client.post('/api/rd/v2/product-assist', json={'product_name': 'Minoxidil', 'intent': 'inspect'})
    assert r.status_code == 200
    data = r.json()
    assert data['ok']
    assert data['product_name'].lower() == 'minoxidil'
    assert data['next_actions']
