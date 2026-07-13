from __future__ import annotations

import json
import math
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .database import db

BASE_DIR = Path(__file__).resolve().parents[1]
EQUIPMENT_SEED_PATH = BASE_DIR / 'data' / 'reference' / 'jk_lifecare_equipment_seed.json'

EQUIPMENT_EXTRA_SCHEMA = r'''
CREATE TABLE IF NOT EXISTS equipment_status_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    equipment_code TEXT NOT NULL,
    previous_status TEXT,
    new_status TEXT NOT NULL,
    previous_cleaning_status TEXT,
    new_cleaning_status TEXT,
    batch_no TEXT,
    stage_name TEXT,
    reason TEXT,
    user_id INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS equipment_cleaning_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    equipment_code TEXT NOT NULL,
    previous_product TEXT,
    previous_batch_no TEXT,
    cleaning_type TEXT DEFAULT 'product_changeover',
    sop_no TEXT,
    status TEXT DEFAULT 'started',
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT,
    verified_by INTEGER,
    remarks TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS equipment_reservations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    equipment_code TEXT NOT NULL,
    plan_id INTEGER,
    batch_no TEXT,
    product_name TEXT,
    stage_no INTEGER,
    stage_name TEXT,
    start_time TEXT,
    end_time TEXT,
    status TEXT DEFAULT 'reserved',
    reserved_by INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS manpower_roster (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id INTEGER,
    batch_no TEXT,
    department TEXT DEFAULT 'Production',
    role_name TEXT NOT NULL,
    shift_name TEXT DEFAULT 'A',
    headcount INTEGER DEFAULT 1,
    skill_required TEXT,
    status TEXT DEFAULT 'planned',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS process_equipment_requirements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_name TEXT NOT NULL,
    stage_no INTEGER NOT NULL,
    stage_name TEXT NOT NULL,
    required_class TEXT NOT NULL,
    min_capacity_l REAL DEFAULT 0,
    preferred_moc TEXT,
    utility_need_json TEXT DEFAULT '{}',
    cleaning_requirement TEXT DEFAULT 'product-contact equipment cleaned and line cleared',
    manpower_json TEXT DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS tech_transfer_packages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_name TEXT NOT NULL,
    product_research_id INTEGER,
    source_scale TEXT DEFAULT 'lab',
    target_scale TEXT DEFAULT 'plant',
    target_batch_size REAL DEFAULT 0,
    unit TEXT DEFAULT 'kg',
    route_summary TEXT,
    cpp_json TEXT DEFAULT '[]',
    cqa_json TEXT DEFAULT '[]',
    equipment_fit_json TEXT DEFAULT '{}',
    risk_assessment TEXT,
    status TEXT DEFAULT 'draft',
    created_by INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS scale_up_trials (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_name TEXT NOT NULL,
    tech_transfer_id INTEGER,
    trial_no TEXT NOT NULL,
    scale_level TEXT DEFAULT 'pilot',
    target_qty REAL DEFAULT 0,
    unit TEXT DEFAULT 'kg',
    equipment_path_json TEXT DEFAULT '{}',
    sampling_plan_json TEXT DEFAULT '[]',
    acceptance_criteria TEXT,
    status TEXT DEFAULT 'planned',
    created_by INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS utility_capacity_plan (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    utility_name TEXT NOT NULL,
    available_capacity REAL DEFAULT 0,
    unit TEXT,
    planned_load REAL DEFAULT 0,
    lead_time_hours REAL DEFAULT 0,
    status TEXT DEFAULT 'available',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS equipment_qualification (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    equipment_id INTEGER NOT NULL,
    qualification_type TEXT NOT NULL,
    protocol_no TEXT,
    status TEXT NOT NULL DEFAULT 'draft',
    performed_by INTEGER,
    reviewed_by INTEGER,
    approved_by INTEGER,
    performed_date TEXT,
    approved_date TEXT,
    findings_json TEXT DEFAULT '{}',
    next_due_date TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS equipment_qualification_steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    qualification_id INTEGER NOT NULL,
    step_no INTEGER NOT NULL,
    description TEXT NOT NULL,
    acceptance_criteria TEXT,
    result TEXT,
    pass_fail TEXT DEFAULT 'NA',
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
'''

EQUIPMENT_MASTER_COLUMNS: dict[str, str] = {
    'source_sr_no': 'INTEGER',
    'plant': 'TEXT',
    'tag_no': 'TEXT',
    'description': 'TEXT',
    'moc': 'TEXT',
    'nominal_capacity': 'TEXT',
    'capacity_value': 'REAL DEFAULT 0',
    'capacity_unit': 'TEXT',
    'floor_elevation': 'TEXT',
    'phase_status': 'TEXT',
    'equipment_class': 'TEXT',
    'occupancy_status': "TEXT DEFAULT 'idle'",
    'readiness_status': "TEXT DEFAULT 'ready'",
    'current_batch_no': 'TEXT',
    'current_stage': 'TEXT',
    'last_cleaned_at': 'TEXT',
    'cleaning_valid_until': 'TEXT',
    'cleaning_sop_no': 'TEXT',
    'maintenance_status': "TEXT DEFAULT 'ok'",
    'maintenance_due_date': 'TEXT',
    'cross_contamination_risk': "TEXT DEFAULT 'standard'",
    'gmp_lock_reason': 'TEXT',
    'remarks': 'TEXT',
}

STATUS_OPTIONS = [
    'ready', 'reserved', 'running', 'charging', 'reaction_in_progress', 'filtration_in_progress',
    'drying_in_progress', 'cleaning_in_progress', 'cleaned_pending_qa', 'dirty_not_cleaned',
    'maintenance', 'calibration_due', 'hold', 'qualification_pending', 'out_of_service'
]
CLEANING_OPTIONS = ['cleaned', 'cleaned_pending_qa', 'dirty_not_cleaned', 'cleaning_in_progress', 'not_applicable']


def _table_columns(table: str) -> set[str]:
    return {r['name'] for r in db.query(f'PRAGMA table_info({table})')}


def _ensure_equipment_columns() -> None:
    existing = _table_columns('equipment_master')
    for col, ddl in EQUIPMENT_MASTER_COLUMNS.items():
        if col not in existing:
            db.execute(f'ALTER TABLE equipment_master ADD COLUMN {col} {ddl}')


def ensure_equipment_intelligence_schema() -> None:
    with db.connect() as conn:
        conn.executescript(EQUIPMENT_EXTRA_SCHEMA)
    _ensure_equipment_columns()
    seed_equipment_from_jk_list()
    seed_process_equipment_requirements()
    seed_utility_capacity()


def _load_equipment_seed() -> list[dict[str, Any]]:
    if EQUIPMENT_SEED_PATH.exists():
        return json.loads(EQUIPMENT_SEED_PATH.read_text(encoding='utf-8'))
    return []


def classify_equipment(eq_type: str, description: str, tag_no: str = '') -> str:
    text = f'{eq_type} {description} {tag_no}'.upper()
    if 'SS REACTOR' in text or 'GLR' in text or 'MSGL REACTOR' in text or 'SSR' in text:
        return 'reactor'
    if 'ANFD' in text:
        return 'filter_dryer'
    if 'VACUUM TRAY DRYER' in text or 'VTD' in text or 'FBD' in text or 'DRYER' in text:
        return 'dryer'
    if 'CENTRIFUGE' in text or re.search(r'\bCF\b', text) or 'CFG' in text:
        return 'centrifuge'
    if 'SPARKLER' in text or 'SPF' in text or 'PRESSURE NUTCH' in text or 'PNF' in text:
        return 'filtration'
    if 'HEAT EXCHANGER' in text or 'CONDENSER' in text or 'CONDENSOR' in text:
        return 'condenser'
    if 'VACUUM PUMP' in text:
        return 'vacuum_pump'
    if 'VACUUM TRAP' in text:
        return 'vacuum_trap'
    if 'SCRUBBER' in text:
        return 'scrubber'
    if 'AHU' in text or 'AIR HANDLING' in text:
        return 'ahu'
    if 'PUMP' in text:
        return 'pump'
    if 'TANK' in text or 'VESSEL' in text or '/CR-' in text or '/ML-' in text or '/ST-' in text or '/DT-' in text:
        return 'tank'
    if 'BLENDER' in text or 'MULTIMILL' in text or 'VIBRO' in text or 'JET MILL' in text or 'SIFTER' in text:
        return 'finishing'
    if 'PASS BOX' in text:
        return 'pass_box'
    return re.sub(r'[^a-z0-9]+', '_', (eq_type or 'other').lower()).strip('_') or 'other'


def parse_capacity(capacity: str) -> tuple[float, str]:
    cap = (capacity or '').strip().upper().replace(' ', '')
    if not cap:
        return 0.0, ''
    m = re.search(r'([0-9]+(?:\.[0-9]+)?)\s*KL', cap)
    if m:
        return float(m.group(1)) * 1000.0, 'L'
    m = re.search(r'([0-9]+(?:\.[0-9]+)?)\s*LTR', cap)
    if m:
        return float(m.group(1)), 'L'
    m = re.search(r'([0-9]+(?:\.[0-9]+)?)\s*L\b', cap)
    if m:
        return float(m.group(1)), 'L'
    m = re.search(r'([0-9]+(?:\.[0-9]+)?)\s*KG/HR', cap)
    if m:
        return float(m.group(1)), 'kg/hr'
    m = re.search(r'([0-9]+(?:\.[0-9]+)?)\s*KG', cap)
    if m:
        return float(m.group(1)), 'kg'
    m = re.search(r'([0-9]+(?:\.[0-9]+)?)\s*TRAY', cap)
    if m:
        return float(m.group(1)), 'tray'
    m = re.search(r'([0-9]+(?:\.[0-9]+)?)\s*CFM', cap)
    if m:
        return float(m.group(1)), 'cfm'
    m = re.search(r'([0-9]+(?:\.[0-9]+)?)\s*M2', cap)
    if m:
        return float(m.group(1)), 'm2'
    m = re.search(r'([0-9]+(?:\.[0-9]+)?)', cap)
    return (float(m.group(1)), 'unit') if m else (0.0, '')


def is_product_contact(equipment_class: str) -> bool:
    return equipment_class in {'reactor', 'filter_dryer', 'dryer', 'centrifuge', 'filtration', 'tank', 'finishing'}


def default_operational_status(row: dict[str, Any], equipment_class: str) -> tuple[str, str, str]:
    # Imported defaults are conservative: Phase-2 is marked qualification_pending until a user confirms readiness.
    phase = str(row.get('phase_status', '')).lower()
    if 'phase-2' in phase or 'phase -2' in phase:
        status = 'qualification_pending'
        ready = 'blocked'
    else:
        status = 'ready'
        ready = 'ready'
    cleaning = 'cleaned' if is_product_contact(equipment_class) else 'not_applicable'
    return status, cleaning, ready


def seed_equipment_from_jk_list() -> None:
    rows = _load_equipment_seed()
    if not rows:
        return
    existing_count = db.one('SELECT COUNT(*) c FROM equipment_master WHERE source_sr_no IS NOT NULL')
    if existing_count and int(existing_count.get('c') or 0) >= 150 and db.one("SELECT id FROM equipment_master WHERE equipment_code='P1/SSR-05'"):
        return
    for row in rows:
        tag = str(row.get('tag_no') or '').strip()
        if not tag:
            continue
        eq_class = classify_equipment(row.get('eq_type', ''), row.get('description', ''), tag)
        cap_value, cap_unit = parse_capacity(row.get('capacity', ''))
        status, cleaning, readiness = default_operational_status(row, eq_class)
        name = row.get('description') or row.get('eq_type') or tag
        existing = db.one('SELECT id, equipment_code FROM equipment_master WHERE equipment_code=?', (tag,))
        if existing:
            db.execute('''UPDATE equipment_master SET equipment_name=?, equipment_type=?, capacity=?, unit=?, location=?, source_sr_no=?, plant=?, tag_no=?, description=?, moc=?, nominal_capacity=?, capacity_value=?, capacity_unit=?, floor_elevation=?, phase_status=?, equipment_class=?, updated_at=CURRENT_TIMESTAMP WHERE equipment_code=?''',
                       (name, row.get('eq_type',''), cap_value, cap_unit or row.get('capacity',''), row.get('floor',''), row.get('sr_no'), row.get('plant',''), tag, row.get('description',''), row.get('moc',''), row.get('capacity',''), cap_value, cap_unit, row.get('floor',''), row.get('phase_status',''), eq_class, tag))
        else:
            db.execute('''INSERT INTO equipment_master(equipment_code, equipment_name, equipment_type, capacity, unit, location, status, cleaning_status, current_product, utility_profile_json, compatible_products, source_sr_no, plant, tag_no, description, moc, nominal_capacity, capacity_value, capacity_unit, floor_elevation, phase_status, equipment_class, occupancy_status, readiness_status, last_cleaned_at, cleaning_sop_no, maintenance_status, cross_contamination_risk, remarks) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                       (tag, name, row.get('eq_type',''), cap_value, cap_unit or row.get('capacity',''), row.get('floor',''), status, cleaning, '', json.dumps(infer_utility_profile(eq_class)), 'fluconazole,generic api', row.get('sr_no'), row.get('plant',''), tag, row.get('description',''), row.get('moc',''), row.get('capacity',''), cap_value, cap_unit, row.get('floor',''), row.get('phase_status',''), eq_class, 'idle', readiness, datetime.now().isoformat(timespec='seconds') if cleaning == 'cleaned' else None, default_cleaning_sop(eq_class), 'ok', 'standard' if is_product_contact(eq_class) else 'low', 'Imported from J.K. Lifecare equipment list. Verify actual GMP status before use.'))


def infer_utility_profile(eq_class: str) -> dict[str, Any]:
    if eq_class == 'reactor':
        return {'steam': True, 'chilled_water': True, 'vacuum': True, 'power': 'medium', 'operator_count': 2}
    if eq_class in {'filter_dryer', 'dryer'}:
        return {'steam': True, 'vacuum': True, 'power': 'medium', 'operator_count': 1}
    if eq_class in {'centrifuge', 'finishing'}:
        return {'power': 'medium', 'operator_count': 1}
    if eq_class in {'ahu', 'scrubber', 'vacuum_pump'}:
        return {'utility_support': True, 'operator_count': 0}
    return {'power': 'low', 'operator_count': 0}


def default_cleaning_sop(eq_class: str) -> str:
    mapping = {
        'reactor': 'SOP-PRD-CLN-REACTOR',
        'filter_dryer': 'SOP-PRD-CLN-ANFD',
        'dryer': 'SOP-PRD-CLN-DRYER',
        'centrifuge': 'SOP-PRD-CLN-CENTRIFUGE',
        'filtration': 'SOP-PRD-CLN-FILTER',
        'tank': 'SOP-PRD-CLN-TANK',
        'finishing': 'SOP-PRD-CLN-FINISHING',
    }
    return mapping.get(eq_class, 'SOP-ENG-EQUIPMENT-STATUS')


def seed_process_equipment_requirements() -> None:
    if db.one("SELECT id FROM process_equipment_requirements WHERE lower(product_name)='fluconazole' LIMIT 1"):
        return
    requirements = [
        ('Fluconazole', 1, 'DFB to DFTA intermediate stage', 'reactor', 1200, 'GLR/MSGL or SS316 as approved', {'steam': True, 'chilled_water': True, 'vacuum': True}, {'operator': 2, 'supervisor': 1, 'qc_sampler': 1}),
        ('Fluconazole', 1, 'DFB to DFTA intermediate stage', 'condenser', 2, 'compatible condenser', {'cooling': True}, {'operator': 0}),
        ('Fluconazole', 2, 'DFTA purification and recovery stage', 'centrifuge', 24, 'HALAR/PVDF contact as approved', {'power': True}, {'operator': 1, 'qc_sampler': 1}),
        ('Fluconazole', 2, 'DFTA purification and recovery stage', 'filter_dryer', 500, 'SS316 ANFD preferred', {'vacuum': True, 'drying': True}, {'operator': 1}),
        ('Fluconazole', 3, 'Triazole addition / final Fluconazole formation', 'reactor', 1600, 'GLR/MSGL or SS316 as approved', {'steam': True, 'chilled_water': True, 'vacuum': True}, {'operator': 2, 'supervisor': 1, 'qc_sampler': 1}),
        ('Fluconazole', 3, 'Triazole addition / final Fluconazole formation', 'dryer', 48, 'VTD/FBD as validated', {'steam': True, 'vacuum': True}, {'operator': 1}),
        ('Fluconazole', 3, 'Triazole addition / final Fluconazole formation', 'finishing', 100, 'Multimill/sifter/blender as needed', {'power': True}, {'operator': 1}),
    ]
    for product, stage_no, stage_name, req_class, mincap, moc, utility, manpower in requirements:
        db.execute('''INSERT INTO process_equipment_requirements(product_name, stage_no, stage_name, required_class, min_capacity_l, preferred_moc, utility_need_json, manpower_json) VALUES (?,?,?,?,?,?,?,?)''',
                   (product, stage_no, stage_name, req_class, mincap, moc, json.dumps(utility), json.dumps(manpower)))


def seed_utility_capacity() -> None:
    if db.one('SELECT id FROM utility_capacity_plan LIMIT 1'):
        return
    rows = [('Steam', 1200, 'kg/day', 0, 24, 'available'), ('Chilled Water', 180, 'TR-hour/day', 0, 24, 'available'), ('Vacuum', 60, 'pump-hour/day', 0, 12, 'available'), ('Power', 3000, 'kWh/day', 0, 0, 'available'), ('Nitrogen', 100, 'Nm3/day', 0, 24, 'available')]
    for r in rows:
        db.execute('INSERT INTO utility_capacity_plan(utility_name, available_capacity, unit, planned_load, lead_time_hours, status) VALUES (?,?,?,?,?,?)', r)


def equipment_status_counts() -> dict[str, Any]:
    rows = db.query('SELECT equipment_class, status, cleaning_status, readiness_status, COUNT(*) count FROM equipment_master GROUP BY equipment_class, status, cleaning_status, readiness_status')
    by_status: dict[str, int] = {}
    by_class: dict[str, int] = {}
    clean_ready = dirty = blocked = 0
    for r in rows:
        by_status[r['status']] = by_status.get(r['status'], 0) + int(r['count'])
        by_class[r['equipment_class'] or 'other'] = by_class.get(r['equipment_class'] or 'other', 0) + int(r['count'])
        if r['readiness_status'] == 'ready' and r['cleaning_status'] in {'cleaned', 'not_applicable'}:
            clean_ready += int(r['count'])
        if r['cleaning_status'] == 'dirty_not_cleaned':
            dirty += int(r['count'])
        if r['readiness_status'] != 'ready':
            blocked += int(r['count'])
    total = sum(int(r['count']) for r in rows)
    return {'total': total, 'by_status': by_status, 'by_class': by_class, 'ready_clean': clean_ready, 'dirty': dirty, 'blocked': blocked}


def readiness_from_status(status: str, cleaning_status: str, maintenance_status: str = 'ok') -> str:
    if maintenance_status and maintenance_status not in {'ok', 'none', 'not_applicable'}:
        return 'blocked'
    if status in {'ready', 'reserved'} and cleaning_status in {'cleaned', 'not_applicable'}:
        return 'ready'
    if status in {'running', 'charging', 'reaction_in_progress', 'filtration_in_progress', 'drying_in_progress'}:
        return 'in_use'
    if status in {'qualification_pending', 'calibration_due', 'maintenance', 'hold', 'out_of_service'}:
        return 'blocked'
    if cleaning_status in {'dirty_not_cleaned', 'cleaning_in_progress', 'cleaned_pending_qa'}:
        return 'cleaning_required'
    return 'review_required'


def update_equipment_status(user_id: int | None, data: dict[str, Any]) -> None:
    code = str(data.get('equipment_code') or data.get('tag_no') or '').strip()
    if not code:
        raise ValueError('equipment_code is required')
    row = db.one('SELECT * FROM equipment_master WHERE equipment_code=?', (code,))
    if not row:
        raise ValueError(f'Equipment not found: {code}')
    status = str(data.get('status') or row.get('status') or 'ready')
    cleaning = str(data.get('cleaning_status') or row.get('cleaning_status') or 'not_applicable')
    batch_no = str(data.get('batch_no') or row.get('current_batch_no') or '')
    stage = str(data.get('stage_name') or data.get('current_stage') or row.get('current_stage') or '')
    maintenance = str(data.get('maintenance_status') or row.get('maintenance_status') or 'ok')
    readiness = readiness_from_status(status, cleaning, maintenance)
    reason = str(data.get('reason') or data.get('remarks') or '')
    db.execute('''UPDATE equipment_master SET status=?, cleaning_status=?, current_batch_no=?, current_stage=?, maintenance_status=?, readiness_status=?, gmp_lock_reason=?, updated_at=CURRENT_TIMESTAMP WHERE equipment_code=?''',
               (status, cleaning, batch_no, stage, maintenance, readiness, reason if readiness != 'ready' else '', code))
    db.execute('''INSERT INTO equipment_status_history(equipment_code, previous_status, new_status, previous_cleaning_status, new_cleaning_status, batch_no, stage_name, reason, user_id) VALUES (?,?,?,?,?,?,?,?,?)''',
               (code, row.get('status'), status, row.get('cleaning_status'), cleaning, batch_no, stage, reason, user_id))
    db.audit(user_id, 'update_status', 'equipment_master', code, {'status': status, 'cleaning_status': cleaning, 'readiness': readiness, 'reason': reason})


def create_cleaning_log(user_id: int | None, data: dict[str, Any]) -> int:
    code = str(data.get('equipment_code') or '').strip()
    row = db.one('SELECT * FROM equipment_master WHERE equipment_code=?', (code,))
    if not row:
        raise ValueError('Equipment not found')
    status = str(data.get('status') or 'completed')
    completed_at = datetime.now().isoformat(timespec='seconds') if status in {'completed', 'verified'} else None
    cid = db.execute('''INSERT INTO equipment_cleaning_log(equipment_code, previous_product, previous_batch_no, cleaning_type, sop_no, status, completed_at, verified_by, remarks) VALUES (?,?,?,?,?,?,?,?,?)''',
                    (code, data.get('previous_product') or row.get('current_product') or '', data.get('previous_batch_no') or row.get('current_batch_no') or '', data.get('cleaning_type','product_changeover'), data.get('sop_no') or row.get('cleaning_sop_no') or default_cleaning_sop(row.get('equipment_class','')), status, completed_at, user_id if status in {'verified', 'completed'} else None, data.get('remarks','')))
    if status in {'completed', 'verified'}:
        db.execute('''UPDATE equipment_master SET cleaning_status=?, status=?, readiness_status=?, last_cleaned_at=?, cleaning_valid_until=?, current_product='', current_batch_no='', current_stage='', updated_at=CURRENT_TIMESTAMP WHERE equipment_code=?''',
                   ('cleaned' if status == 'verified' else 'cleaned_pending_qa', 'ready' if status == 'verified' else 'cleaned_pending_qa', 'ready' if status == 'verified' else 'cleaning_required', completed_at, (datetime.now() + timedelta(days=7)).date().isoformat(), code))
    else:
        update_equipment_status(user_id, {'equipment_code': code, 'status': 'cleaning_in_progress', 'cleaning_status': 'cleaning_in_progress', 'reason': 'Cleaning log started'})
    db.audit(user_id, 'cleaning_log', 'equipment_master', code, data)
    return cid


def _available_equipment(required_class: str, min_capacity_l: float = 0) -> list[dict[str, Any]]:
    rows = db.query('''SELECT * FROM equipment_master WHERE equipment_class=? ORDER BY capacity_value DESC, equipment_code''', (required_class,))
    out = []
    for r in rows:
        if float(r.get('capacity_value') or 0) < float(min_capacity_l or 0):
            continue
        ready = readiness_from_status(r.get('status',''), r.get('cleaning_status',''), r.get('maintenance_status','ok'))
        if ready == 'ready':
            out.append(r)
    return out


def equipment_fit_for_batch(product_name: str, target_qty: float) -> dict[str, Any]:
    ensure_equipment_intelligence_schema()
    product = product_name or 'Unknown Product'
    reqs = db.query('''SELECT * FROM process_equipment_requirements WHERE lower(product_name)=lower(?) ORDER BY stage_no, id''', (product,))
    if not reqs and 'fluconazole' in product.lower():
        reqs = db.query("SELECT * FROM process_equipment_requirements WHERE lower(product_name)='fluconazole' ORDER BY stage_no, id")
    stage_results = []
    overall_ok = True
    for req in reqs:
        # Scale min capacity with batch size, but keep a floor from the process definition.
        base_cap = float(req.get('min_capacity_l') or 0)
        scaled_cap = max(base_cap, float(target_qty or 0) * (12 if req['required_class'] == 'reactor' else 1))
        candidates = _available_equipment(req['required_class'], scaled_cap)
        selected = candidates[0] if candidates else None
        if not selected:
            overall_ok = False
        stage_results.append({
            'stage_no': req['stage_no'],
            'stage_name': req['stage_name'],
            'required_class': req['required_class'],
            'min_capacity_l': round(scaled_cap, 2),
            'preferred_moc': req.get('preferred_moc') or '',
            'selected_equipment': selected['equipment_code'] if selected else '',
            'selected_name': selected['equipment_name'] if selected else '',
            'candidate_count': len(candidates),
            'status': 'ok' if selected else 'blocked',
            'utility_need': json.loads(req.get('utility_need_json') or '{}'),
            'manpower': json.loads(req.get('manpower_json') or '{}'),
        })
    blockers = []
    if not overall_ok:
        blockers.append('Required clean/ready equipment path is not fully available. Check qualification, cleaning, maintenance, and current reservations.')
    utilities = utility_projection(product, target_qty)
    return {'ok': overall_ok, 'product_name': product, 'target_qty': target_qty, 'stages': stage_results, 'utilities': utilities, 'blockers': blockers}


def utility_projection(product_name: str, target_qty: float) -> dict[str, Any]:
    qty = float(target_qty or 0)
    demand = {
        'Steam': round(qty * 4.5, 2),
        'Chilled Water': round(qty * 0.15, 2),
        'Vacuum': 14.0,
        'Power': round(qty * 1.2, 2),
        'Nitrogen': round(qty * 0.08, 2),
    }
    rows = db.query('SELECT * FROM utility_capacity_plan ORDER BY utility_name')
    checks = []
    ok = True
    for r in rows:
        need = demand.get(r['utility_name'], 0)
        available = float(r.get('available_capacity') or 0)
        status = 'ok' if need <= available and r.get('status') == 'available' else 'review'
        if status != 'ok':
            ok = False
        checks.append({'utility': r['utility_name'], 'needed': need, 'available': available, 'unit': r.get('unit') or '', 'lead_time_hours': r.get('lead_time_hours'), 'status': status})
    return {'ok': ok, 'checks': checks, 'lead_time_hours': max([float(x.get('lead_time_hours') or 0) for x in rows] or [0])}


def reserve_equipment_for_plan(user_id: int | None, plan_id: int, product_name: str, batch_no: str | None = None, start_time: str | None = None) -> dict[str, Any]:
    plan = db.one('SELECT * FROM production_plans WHERE id=?', (plan_id,))
    target_qty = float(plan['target_qty']) if plan else 0.0
    fit = equipment_fit_for_batch(product_name, target_qty)
    created = []
    for st in fit.get('stages', []):
        code = st.get('selected_equipment')
        if not code:
            continue
        rid = db.execute('''INSERT INTO equipment_reservations(equipment_code, plan_id, batch_no, product_name, stage_no, stage_name, start_time, end_time, status, reserved_by) VALUES (?,?,?,?,?,?,?,?,?,?)''',
                         (code, plan_id, batch_no or f'PLAN-{plan_id}', product_name, st['stage_no'], st['stage_name'], start_time or '', '', 'reserved', user_id))
        db.execute('UPDATE equipment_master SET status=?, occupancy_status=?, current_product=?, current_batch_no=?, current_stage=?, readiness_status=?, updated_at=CURRENT_TIMESTAMP WHERE equipment_code=?',
                   ('reserved', 'reserved', product_name, batch_no or f'PLAN-{plan_id}', st['stage_name'], 'ready', code))
        created.append(rid)
    # Create manpower plan from stage requirements.
    manpower_totals: dict[str, int] = {}
    for st in fit.get('stages', []):
        for role, count in (st.get('manpower') or {}).items():
            manpower_totals[role] = manpower_totals.get(role, 0) + int(count or 0)
    for role, count in manpower_totals.items():
        if count > 0:
            db.execute('INSERT INTO manpower_roster(plan_id, batch_no, role_name, shift_name, headcount, skill_required, status) VALUES (?,?,?,?,?,?,?)',
                       (plan_id, batch_no or f'PLAN-{plan_id}', role.replace('_',' ').title(), 'A/B coverage', count, 'GMP trained, area qualified, SOP read-and-understood', 'planned'))
    db.audit(user_id, 'reserve_equipment', 'production_plan', plan_id, {'equipment_reservations': created, 'manpower': manpower_totals})
    return {'reservations': created, 'manpower': manpower_totals, 'fit': fit}


def equipment_dashboard_data() -> dict[str, Any]:
    ensure_equipment_intelligence_schema()
    return {
        'counts': equipment_status_counts(),
        'equipment': db.query('SELECT * FROM equipment_master ORDER BY equipment_class, plant, equipment_code'),
        'reservations': db.query('SELECT r.*, e.equipment_name, e.equipment_class FROM equipment_reservations r LEFT JOIN equipment_master e ON e.equipment_code=r.equipment_code ORDER BY r.created_at DESC LIMIT 120'),
        'cleaning_logs': db.query('SELECT c.*, e.equipment_name, e.equipment_class FROM equipment_cleaning_log c LEFT JOIN equipment_master e ON e.equipment_code=c.equipment_code ORDER BY c.created_at DESC LIMIT 80'),
        'history': db.query('SELECT h.*, e.equipment_name, e.equipment_class FROM equipment_status_history h LEFT JOIN equipment_master e ON e.equipment_code=h.equipment_code ORDER BY h.created_at DESC LIMIT 100'),
        'utilities': db.query('SELECT * FROM utility_capacity_plan ORDER BY utility_name'),
        'status_options': STATUS_OPTIONS,
        'cleaning_options': CLEANING_OPTIONS,
        'qualifications': db.query('SELECT * FROM equipment_qualification ORDER BY created_at DESC LIMIT 50'),
    }


def create_tech_transfer_package(user_id: int | None, data: dict[str, Any]) -> int:
    product = data.get('product_name') or 'Fluconazole'
    target_qty = float(data.get('target_batch_size') or data.get('target_qty') or 100)
    fit = equipment_fit_for_batch(product, target_qty)
    cpp = data.get('cpp_json') or [
        'temperature profile and excursions', 'addition rate and sequence', 'agitation/RPM', 'phase separation or slurry behavior',
        'reaction endpoint and in-process HPLC', 'wash volume and mother liquor assay', 'drying endpoint and residual solvent trend'
    ]
    cqa = data.get('cqa_json') or ['assay', 'related substances', 'residual solvents', 'LOD', 'appearance', 'identification']
    risk = data.get('risk_assessment') or 'Scale-up focus: heat transfer, mixing, addition exotherm, solvent hold-up, filtration/drying loss, cleaning validation, and QC sample timing. AI may draft; QA/R&D must approve before plant execution.'
    rid = db.execute('''INSERT INTO tech_transfer_packages(product_name, product_research_id, source_scale, target_scale, target_batch_size, unit, route_summary, cpp_json, cqa_json, equipment_fit_json, risk_assessment, status, created_by) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                    (product, int(data.get('product_research_id') or 0) or None, data.get('source_scale','lab'), data.get('target_scale','plant'), target_qty, data.get('unit','kg'), data.get('route_summary','Lab-to-plant process transfer package generated from R&D process stages.'), json.dumps(cpp), json.dumps(cqa), json.dumps(fit), risk, 'draft_pending_qa_rd_approval', user_id))
    db.audit(user_id, 'create', 'tech_transfer_package', rid, data)
    return rid


def create_scale_up_trial(user_id: int | None, data: dict[str, Any]) -> int:
    product = data.get('product_name') or 'Fluconazole'
    target_qty = float(data.get('target_qty') or 10)
    fit = equipment_fit_for_batch(product, target_qty)
    sample_plan = data.get('sampling_plan_json') or [
        {'stage': 'reaction start', 'tests': ['appearance', 'temperature check']},
        {'stage': 'mid reaction', 'tests': ['HPLC conversion', 'related substances']},
        {'stage': 'isolation', 'tests': ['mother liquor assay', 'wet cake purity']},
        {'stage': 'drying endpoint', 'tests': ['LOD', 'GC-HS residual solvents']},
    ]
    trial_no = data.get('trial_no') or f'SU-{datetime.now().strftime("%Y%m%d%H%M%S")}'
    rid = db.execute('''INSERT INTO scale_up_trials(product_name, tech_transfer_id, trial_no, scale_level, target_qty, unit, equipment_path_json, sampling_plan_json, acceptance_criteria, status, created_by) VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                    (product, int(data.get('tech_transfer_id') or 0) or None, trial_no, data.get('scale_level','pilot'), target_qty, data.get('unit','kg'), json.dumps(fit), json.dumps(sample_plan), data.get('acceptance_criteria','Yield, impurity profile, residual solvent, and drying loss must remain inside approved development acceptance criteria. Human approval required.'), 'planned', user_id))
    db.audit(user_id, 'create', 'scale_up_trial', rid, data)
    return rid


def production_powerhouse_summary() -> dict[str, Any]:
    return {
        'equipment': equipment_dashboard_data(),
        'tech_transfer': db.query('SELECT * FROM tech_transfer_packages ORDER BY created_at DESC LIMIT 50'),
        'scale_up_trials': db.query('SELECT * FROM scale_up_trials ORDER BY created_at DESC LIMIT 50'),
        'manpower': db.query('SELECT * FROM manpower_roster ORDER BY created_at DESC LIMIT 80'),
        'requirements': db.query('SELECT * FROM process_equipment_requirements ORDER BY product_name, stage_no, required_class'),
    }


# ── Equipment Qualification (IQ/OQ/PQ) ─────────────────────────────────────

QUALIFICATION_STATUS_OPTIONS = ['draft', 'planned', 'executed', 'reviewed', 'approved', 'closed']


def create_equipment_qualification(user_id: int | None, data: dict[str, Any]) -> int:
    ensure_equipment_intelligence_schema()
    equipment_id = int(data.get('equipment_id') or 0)
    eq = db.one('SELECT id FROM equipment_master WHERE id=?', (equipment_id,))
    if not eq:
        raise ValueError('Equipment not found')
    qtype = str(data.get('qualification_type') or 'IQ').upper()
    if qtype not in {'IQ', 'OQ', 'PQ'}:
        raise ValueError('qualification_type must be IQ, OQ, or PQ')
    protocol_no = str(data.get('protocol_no') or f'{qtype}-EQ-{equipment_id}-{datetime.now().strftime("%Y%m%d")}')
    rid = db.execute(
        'INSERT INTO equipment_qualification(equipment_id, qualification_type, protocol_no, status, performed_by, next_due_date) VALUES (?, ?, ?, ?, ?, ?)',
        (equipment_id, qtype, protocol_no, 'draft', user_id, data.get('next_due_date'))
    )
    db.audit(user_id, 'create', 'equipment_qualification', rid, {'type': qtype, 'protocol_no': protocol_no})
    return rid


def add_qualification_step(user_id: int | None, data: dict[str, Any]) -> int:
    ensure_equipment_intelligence_schema()
    qual_id = int(data.get('qualification_id') or 0)
    qual = db.one('SELECT * FROM equipment_qualification WHERE id=?', (qual_id,))
    if not qual:
        raise ValueError('Qualification not found')
    sid = db.execute(
        'INSERT INTO equipment_qualification_steps(qualification_id, step_no, description, acceptance_criteria, result, pass_fail, notes) VALUES (?, ?, ?, ?, ?, ?, ?)',
        (qual_id, int(data.get('step_no') or 0), data.get('description', ''), data.get('acceptance_criteria', ''), data.get('result', ''), data.get('pass_fail', 'NA'), data.get('notes', ''))
    )
    db.audit(user_id, 'update', 'equipment_qualification', qual_id, {'step_added': sid})
    return sid


def transition_qualification_status(user_id: int | None, qual_id: int, new_status: str, data: dict[str, Any]) -> None:
    ensure_equipment_intelligence_schema()
    if new_status not in QUALIFICATION_STATUS_OPTIONS:
        raise ValueError(f'Invalid status: {new_status}')
    qual = db.one('SELECT * FROM equipment_qualification WHERE id=?', (qual_id,))
    if not qual:
        raise ValueError('Qualification not found')
    current = qual['status']
    valid_flow = {
        'draft': {'planned'},
        'planned': {'executed'},
        'executed': {'reviewed'},
        'reviewed': {'approved'},
        'approved': {'closed'},
        'closed': set(),
    }
    if new_status not in valid_flow.get(current, set()):
        raise ValueError(f'Invalid transition: {current} → {new_status}')
    updates = {'status': new_status}
    if new_status == 'executed':
        updates['performed_date'] = datetime.now().isoformat(timespec='seconds')
        updates['performed_by'] = user_id
    if new_status == 'reviewed':
        updates['reviewed_by'] = user_id
    if new_status == 'approved':
        updates['approved_date'] = datetime.now().isoformat(timespec='seconds')
        updates['approved_by'] = user_id
    set_clause = ', '.join(f'{k}=?' for k in updates)
    db.execute(f'UPDATE equipment_qualification SET {set_clause}, updated_at=CURRENT_TIMESTAMP WHERE id=?', (*updates.values(), qual_id))
    db.audit(user_id, 'transition', 'equipment_qualification', qual_id, {'from': current, 'to': new_status})


def get_equipment_qualifications(equipment_id: int) -> dict[str, Any]:
    ensure_equipment_intelligence_schema()
    quals = db.query('SELECT * FROM equipment_qualification WHERE equipment_id=? ORDER BY created_at DESC', (equipment_id,))
    out = []
    for q in quals:
        steps = db.query('SELECT * FROM equipment_qualification_steps WHERE qualification_id=? ORDER BY step_no', (q['id'],))
        out.append({**dict(q), 'steps': [dict(s) for s in steps]})
    return {'qualifications': out}


def create_tech_transfer_package_from_experiment(user_id: int | None, experiment_id: int, data: dict[str, Any] | None = None) -> int:
    """Build a tech-transfer package directly from a finalized R&D experiment."""
    from .enterprise_pharma_core import get_rd_experiment_detail
    data = data or {}
    detail = get_rd_experiment_detail(experiment_id)
    if not detail or not detail.get('experiment'):
        raise ValueError('Experiment not found')
    exp = detail['experiment']
    product = exp.get('product_name', 'Unknown')
    stages = detail.get('stages', [])
    stage_tests = []
    for st in stages:
        tests = []
        if st.get('ph_value') is not None:
            tests.append('pH')
        if st.get('temperature_c') is not None:
            tests.append('temperature profile')
        if st.get('theoretical_yield_pct'):
            tests.append('yield')
        stage_tests.append({'stage': st.get('stage_name', ''), 'tests': tests})
    cpp = data.get('cpp_json') or [
        'temperature profile and excursions', 'addition rate and sequence', 'agitation/RPM',
        'pH control', 'reaction endpoint and in-process HPLC', 'wash volume and mother liquor assay',
        'drying endpoint and residual solvent trend'
    ]
    cqa = data.get('cqa_json') or ['assay', 'related substances', 'residual solvents', 'LOD', 'appearance', 'identity']
    solvents = ', '.join({s.get('solvent_name', '') for s in detail.get('solvents', []) if s.get('solvent_name')})
    catalysts = ', '.join({s.get('catalyst_name', '') for s in detail.get('solvents', []) if s.get('catalyst_name')}) or exp.get('catalyst', '')
    route = f"{exp.get('ksm', 'KSM')} → {product}. Solvents: {solvents or 'as per SOP'}. Catalyst: {catalysts or 'none specified'}."
    fit = equipment_fit_for_batch(product, float(data.get('target_batch_size') or data.get('target_qty') or 100))
    rid = db.execute(
        '''INSERT INTO tech_transfer_packages(product_name, product_research_id, source_scale, target_scale, target_batch_size, unit, route_summary, cpp_json, cqa_json, equipment_fit_json, risk_assessment, status, created_by) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',
        (
            product,
            int(data.get('product_research_id') or 0) or None,
            data.get('source_scale', 'lab'),
            data.get('target_scale', 'plant'),
            float(data.get('target_batch_size') or data.get('target_qty') or 100),
            data.get('unit', 'kg'),
            route,
            json.dumps(cpp),
            json.dumps(cqa),
            json.dumps(fit),
            data.get('risk_assessment') or 'Scale-up focus: heat transfer, mixing, addition exotherm, solvent hold-up, filtration/drying loss, cleaning validation, and QC sample timing. Derived from experiment.',
            'draft_pending_qa_rd_approval',
            user_id,
        ),
    )
    db.audit(user_id, 'create', 'tech_transfer_from_experiment', rid, {'experiment_id': experiment_id, 'product': product})
    return rid


def rapid_plant_readiness(plan_id: int | None, product_name: str, target_qty: float, desired_start_date: str | None = None) -> dict[str, Any]:
    """Compute whether the plant can be ready within target_days (default 3) based on equipment, materials, and utilities."""
    target_days = 3
    today = datetime.now().date()
    blockers: list[str] = []

    # Equipment fit and earliest reservation slot
    fit = equipment_fit_for_batch(product_name, target_qty)
    if not fit.get('ok'):
        blockers.append('Required equipment path is not fully available.')

    # Material lead times from inventory / vendor defaults
    from .enterprise_pharma_core import _material_key_for_product, MATERIAL_REQUIREMENTS
    product_key = _material_key_for_product(product_name)
    reqs = MATERIAL_REQUIREMENTS.get(product_key, [])
    material_lead_days = 0
    if reqs:
        rows = db.query('SELECT material_name, current_stock, vendor_id FROM inventory_items')
        by_name = {r['material_name'].lower(): r for r in rows}
        for req in reqs:
            required = float(req.get('per_kg_output', 0)) * target_qty
            item = by_name.get(req['material'].lower())
            available = float(item['current_stock']) if item else 0.0
            if available < required:
                vendor_lead = 2  # default vendor lead in days
                if item and item.get('vendor_id'):
                    v = db.one('SELECT lead_time_days FROM vendors WHERE id=?', (item['vendor_id'],))
                    if v and v.get('lead_time_days'):
                        vendor_lead = int(v['lead_time_days'])
                material_lead_days = max(material_lead_days, vendor_lead)

    # Utility lead time
    utility_lead_hours = fit.get('utilities', {}).get('lead_time_hours', 0)
    utility_lead_days = math.ceil(utility_lead_hours / 24.0) if utility_lead_hours else 0

    # Cleaning lead time default
    cleaning_lead_days = 1

    total_lead_days = max(material_lead_days, utility_lead_days, cleaning_lead_days)
    ready_by = today + timedelta(days=total_lead_days)

    if desired_start_date:
        try:
            desired = datetime.strptime(desired_start_date, '%Y-%m-%d').date()
            if desired < ready_by:
                blockers.append(f"Desired start {desired} is before earliest ready date {ready_by}.")
        except Exception:
            pass

    if total_lead_days > target_days:
        blockers.append(f"Minimum lead time is {total_lead_days} days; target is {target_days} days.")

    return {
        'can_meet_target': len(blockers) == 0,
        'ready_by': ready_by.isoformat(),
        'target_days': target_days,
        'lead_days': total_lead_days,
        'material_lead_days': material_lead_days,
        'utility_lead_days': utility_lead_days,
        'cleaning_lead_days': cleaning_lead_days,
        'blockers': blockers,
        'equipment_fit': fit,
    }
