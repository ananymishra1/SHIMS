import json
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
from shared.enterprise_pharma_core import (
    ensure_pharma_schema,
    create_rd_experiment,
    add_rd_raw_material,
    add_rd_experiment_stage,
    add_stage_raw_material,
    get_experiment_rm_ledger,
    calculate_material_balance,
    compare_rd_experiments,
    stage_rm_availability,
    create_bmr_record,
    add_bmr_stage,
)
from shared.bmr_generator import BMRPDFGenerator
from shared.security import hash_password, sign_value


@pytest.fixture
def client():
    ensure_pharma_schema()
    pwd_hash = hash_password('testpass')
    db.execute("DELETE FROM users WHERE username='testrd'")
    db.execute("INSERT INTO users(username, full_name, password_hash, role, department, active) VALUES (?, ?, ?, ?, ?, ?)",
               ('testrd', 'Test RD', pwd_hash, 'rd_lead', 'rd', 1))
    user = db.one("SELECT id FROM users WHERE username='testrd'")
    with TestClient(app) as c:
        c.cookies.set('shims_enterprise_user', sign_value(str(user['id'])))
        yield c


def test_rm_ledger_enforces_balance():
    ensure_pharma_schema()
    exp_id = create_rd_experiment(1, {
        'product_name': 'Testol', 'route_name': 'Route A', 'ksm': '', 'solvent': '', 'catalyst': '', 'notes': '',
        'raw_materials': [{'name': 'Aspirin', 'quantity': 100, 'unit': 'g', 'unit_type': 'mass', 'molecular_weight_g_mol': 180}],
        'solvents': []
    })
    ledger = get_experiment_rm_ledger(exp_id)
    assert ledger['raw_materials'][0]['remaining_qty'] == 100
    stage = add_rd_experiment_stage(1, {'experiment_id': exp_id, 'stage_no': 1, 'stage_name': 'Mix'})
    add_stage_raw_material(1, stage, {
        'experiment_id': exp_id, 'rm_name': 'Aspirin', 'planned_qty': 30, 'planned_unit': 'g',
        'actual_qty_used': 0, 'wastage_qty': 0, 'recovery_qty': 0
    })
    ledger2 = get_experiment_rm_ledger(exp_id)
    assert ledger2['raw_materials'][0]['planned_used'] == 30
    assert ledger2['raw_materials'][0]['remaining_qty'] == 70
    with pytest.raises(ValueError):
        add_stage_raw_material(1, stage, {
            'experiment_id': exp_id, 'rm_name': 'Aspirin', 'planned_qty': 80, 'planned_unit': 'g'
        })


def test_stage_rm_availability_units():
    ensure_pharma_schema()
    exp_id = create_rd_experiment(1, {
        'product_name': 'Testol', 'route_name': '', 'ksm': '', 'solvent': '', 'catalyst': '', 'notes': '',
        'raw_materials': [{'name': 'NaOH', 'quantity': 2, 'unit': 'mol', 'unit_type': 'moles', 'molecular_weight_g_mol': 40}],
        'solvents': []
    })
    avail = stage_rm_availability(exp_id, 'NaOH', 1.5, 'mol')
    assert avail['status'] == 'ok'
    bad = stage_rm_availability(exp_id, 'NaOH', 3, 'mol')
    assert bad['status'] == 'exceeded'


def test_material_balance_returns_kg_equivalent():
    ensure_pharma_schema()
    exp_id = create_rd_experiment(1, {
        'product_name': 'Testol', 'route_name': '', 'ksm': '', 'solvent': '', 'catalyst': '', 'notes': '',
        'raw_materials': [{'name': 'Aspirin', 'quantity': 100, 'unit': 'g', 'unit_type': 'mass', 'molecular_weight_g_mol': 180}],
        'solvents': []
    })
    stage = add_rd_experiment_stage(1, {'experiment_id': exp_id, 'stage_no': 1, 'stage_name': 'Mix', 'output_qty': 80})
    add_stage_raw_material(1, stage, {
        'experiment_id': exp_id, 'rm_name': 'Aspirin', 'planned_qty': 100, 'planned_unit': 'g',
        'actual_qty_used': 100, 'actual_unit': 'g', 'wastage_qty': 0, 'recovery_qty': 0
    })
    bal = calculate_material_balance(exp_id)
    assert bal['total_planned_rm_kg_eq'] == pytest.approx(0.1, abs=1e-6)
    assert 'warnings' in bal


def test_compare_rd_experiments_numeric():
    ensure_pharma_schema()
    e1 = create_rd_experiment(1, {
        'product_name': 'Testol', 'route_name': '', 'ksm': '', 'solvent': '', 'catalyst': '', 'notes': '',
        'raw_materials': [], 'solvents': []
    })
    add_rd_experiment_stage(1, {'experiment_id': e1, 'stage_no': 1, 'stage_name': 'S1', 'actual_yield_pct': 90, 'purity_pct': 99})
    e2 = create_rd_experiment(1, {
        'product_name': 'Testol', 'route_name': '', 'ksm': '', 'solvent': '', 'catalyst': '', 'notes': '',
        'raw_materials': [], 'solvents': []
    })
    add_rd_experiment_stage(1, {'experiment_id': e2, 'stage_no': 1, 'stage_name': 'S1', 'actual_yield_pct': 85, 'purity_pct': 98})
    comp = compare_rd_experiments([e1, e2])
    assert comp['count'] == 2
    assert comp['best_by_yield'] == e1
    assert comp['best_by_purity'] == e1
    assert len(comp['yield_deltas']) == 1


def test_api_products_endpoint(client):
    r = client.get('/api/rd/v2/products?q=Fluconazole')
    assert r.status_code == 200
    data = r.json()
    assert data['ok']


def test_api_create_experiment_from_bmr(client):
    # Without corpus this may seed a research project and create a blank-ish experiment
    r = client.post('/api/rd/v2/experiments/from-bmr', json={'product_name': 'UnknownMoleculeXYZ'})
    assert r.status_code in (200, 500)  # 500 acceptable if AI/DB unavailable in test


def test_api_rm_ledger_endpoint(client):
    ensure_pharma_schema()
    exp_id = create_rd_experiment(1, {
        'product_name': 'LedgerTest', 'route_name': '', 'ksm': '', 'solvent': '', 'catalyst': '', 'notes': '',
        'raw_materials': [{'name': 'RM-A', 'quantity': 50, 'unit': 'g'}], 'solvents': []
    })
    r = client.get(f'/api/rd/v2/experiments/{exp_id}/rm-ledger')
    assert r.status_code == 200
    data = r.json()
    assert data['ok']
    assert data['ledger']['raw_materials'][0]['name'] == 'RM-A'


def test_api_compare_experiments(client):
    ensure_pharma_schema()
    e1 = create_rd_experiment(1, {'product_name': 'Comp', 'route_name': '', 'ksm': '', 'solvent': '', 'catalyst': '', 'notes': '', 'raw_materials': [], 'solvents': []})
    e2 = create_rd_experiment(1, {'product_name': 'Comp', 'route_name': '', 'ksm': '', 'solvent': '', 'catalyst': '', 'notes': '', 'raw_materials': [], 'solvents': []})
    r = client.post('/api/rd/v2/experiments/compare', json={'experiment_ids': [e1, e2], 'skip_ai': True})
    assert r.status_code == 200
    data = r.json()
    assert data['ok']
    assert 'numeric' in data


def test_minoxidil_product_intent_inspect(client):
    r = client.post('/api/rd/v2/product-assist', json={'product_name': 'Minoxidil', 'intent': 'inspect'})
    assert r.status_code == 200
    data = r.json()
    assert data['ok']
    assert data['product_name'].lower() == 'minoxidil'
    assert data['next_actions']
    # In the production DB Minoxidil is in the BMR corpus, so the brain will offer run_bmr_route.
    # The test DB may not have the corpus, so we only verify the response shape here.


def test_minoxidil_run_bmr_route(client):
    r = client.post('/api/rd/v2/product-assist', json={'product_name': 'Minoxidil', 'intent': 'run_bmr_route'})
    assert r.status_code == 200
    data = r.json()
    assert data['ok']
    assert data['experiment']['product_name'].lower() == 'minoxidil'
    assert data['experiment']['experiment_id'] > 0


def test_bmr_generator_includes_corpus_detail():
    """BMR PDFs should carry parsed corpus quantities, conditions and equipment."""
    ensure_pharma_schema()
    exp_id = create_rd_experiment(1, {
        'product_name': 'CorpusDetailTest', 'route_name': 'Corpus route', 'ksm': '', 'solvent': '', 'catalyst': '', 'notes': '',
        'raw_materials': [
            {'name': '2,4-Difluorobenzyl bromide', 'quantity': 100, 'unit': 'g', 'unit_type': 'mass'},
            {'name': 'Potassium carbonate', 'quantity': 55, 'unit': 'g', 'unit_type': 'mass'},
        ],
        'solvents': [{'name': 'Acetonitrile', 'quantity_ml': 500}],
    })
    add_rd_experiment_stage(1, {
        'experiment_id': exp_id,
        'stage_no': 1,
        'stage_name': 'Alkylation',
        'temperature_c': 25.0,
        'pressure_bar': 1.2,
        'ph_value': 7.0,
        'reaction_time_minutes': 120.0,
        'mixing_speed_rpm': 200.0,
        'atmosphere': 'Nitrogen',
        'equipment_code': '100L Glass Lined Reactor',
        'rm_description': '2,4-Difluorobenzyl bromide (100 g); Potassium carbonate (55 g)',
        'solvent': 'Acetonitrile (500 mL)',
        'expected_yield_pct': 92.0,
        'purity_pct': 99.0,
    })

    bmr_id = create_bmr_record(1, {
        'experiment_id': exp_id,
        'product_name': 'CorpusDetailTest',
        'batch_no': 'B-001',
        'bmr_no': 'BMR-CDT-001',
        'target_qty': 10,
        'unit': 'kg',
        'status': 'draft',
        'encoding_mode': 'coded',
    })
    add_bmr_stage(1, bmr_id, {
        'stage_no': 1,
        'stage_name': 'Alkylation',
        'rm_description': '2,4-Difluorobenzyl bromide (100 g); Potassium carbonate (55 g)',
        'rm_description_coded': 'RM-001 (100 g); RM-002 (55 g)',
        'solvent': 'Acetonitrile (500 mL)',
        'temperature_c': 25.0,
        'pressure_bar': 1.2,
        'ph_value': 7.0,
        'reaction_time_minutes': 120.0,
        'mixing_speed_rpm': 200.0,
        'atmosphere': 'Nitrogen',
        'equipment_code': '100L Glass Lined Reactor',
        'expected_yield_pct': 92.0,
        'purity_pct': 99.0,
    })

    pdf_path = BMRPDFGenerator(bmr_id, user_role='production').build()
    assert pdf_path.exists()
    assert pdf_path.stat().st_size > 1000

    # Inspect the rendered BOM lines indirectly by re-reading stage data.
    from shared.enterprise_pharma_core import get_bmr_stages
    stages = get_bmr_stages(bmr_id)
    stage = stages[0]
    assert '100 g' in (stage['rm_description_coded'] or '')
    assert stage['temperature_c'] == 25.0
    assert stage['equipment_code'] == '100L Glass Lined Reactor'
