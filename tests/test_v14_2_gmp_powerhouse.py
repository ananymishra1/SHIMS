from fastapi.testclient import TestClient

from shims_enterprise.app import app
from shared.database import db
from shared.equipment_intelligence import equipment_fit_for_batch, update_equipment_status, create_tech_transfer_package, create_scale_up_trial, equipment_dashboard_data
from shared.enterprise_pharma_core import create_production_plan


def login(c):
    r = c.post('/login', data={'username': 'admin', 'password': 'SHIMS2025!'}, follow_redirects=False)
    assert r.status_code in (302, 303)


def test_v14_2_equipment_seed_and_tracker_pages():
    data = equipment_dashboard_data()
    assert data['counts']['total'] >= 150
    assert db.one("SELECT * FROM equipment_master WHERE equipment_code='P1/SSR-05'")
    assert db.one("SELECT * FROM equipment_master WHERE equipment_code='P1/VTD-01'")
    with TestClient(app) as c:
        login(c)
        for path in ['/production/equipment', '/rd/tech-transfer', '/api/v14.2/enterprise/gmp-powerhouse']:
            r = c.get(path)
            assert r.status_code == 200, path


def test_v14_2_equipment_status_history_and_cleaning_status():
    code = 'P1/SSR-05'
    update_equipment_status(1, {'equipment_code': code, 'status': 'running', 'cleaning_status': 'cleaned', 'batch_no': 'FLC-T-001', 'stage_name': 'Triazole addition', 'reason': 'test run'})
    row = db.one('SELECT * FROM equipment_master WHERE equipment_code=?', (code,))
    assert row['status'] == 'running'
    hist = db.one('SELECT * FROM equipment_status_history WHERE equipment_code=? ORDER BY id DESC', (code,))
    assert hist and hist['new_status'] == 'running'


def test_v14_2_production_plan_reserves_equipment_and_manpower():
    # Reset a few critical pieces to ready so feasibility/reservation path can prove itself.
    for code in ['P1/SSR-05', 'P1/CFG-01', 'P1/ANFD-01', 'P1/VTD-01', 'P1/MM-01']:
        if db.one('SELECT * FROM equipment_master WHERE equipment_code=?', (code,)):
            update_equipment_status(1, {'equipment_code': code, 'status': 'ready', 'cleaning_status': 'cleaned', 'reason': 'test ready'})
    fit = equipment_fit_for_batch('Fluconazole', 100)
    assert 'stages' in fit and len(fit['stages']) >= 3
    pid = create_production_plan(1, {'product_name': 'Fluconazole', 'target_qty': '100', 'unit': 'kg', 'batch_no': 'FLC-PLAN-001'})
    reservations = db.query('SELECT * FROM equipment_reservations WHERE plan_id=?', (pid,))
    manpower = db.query('SELECT * FROM manpower_roster WHERE plan_id=?', (pid,))
    assert reservations
    assert manpower


def test_v14_2_tech_transfer_and_scale_up_records():
    tt = create_tech_transfer_package(1, {'product_name': 'Fluconazole', 'target_batch_size': '100'})
    trial = create_scale_up_trial(1, {'product_name': 'Fluconazole', 'tech_transfer_id': str(tt), 'target_qty': '10', 'trial_no': 'SU-FLC-PYTEST'})
    assert db.one('SELECT * FROM tech_transfer_packages WHERE id=?', (tt,))
    assert db.one('SELECT * FROM scale_up_trials WHERE id=?', (trial,))
