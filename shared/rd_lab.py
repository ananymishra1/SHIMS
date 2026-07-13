"""R&D Lab core — clean, no-LLM-first backend for process development.

All experiment logging, tracing, and monitoring works without AI. AI helpers are
explicit opt-ins and fail gracefully when no model is available.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from .database import db
from .enterprise_pharma_core import (
    _float_or_none,
    _to_kg,
    _UNIT_FACTORS,
    ensure_pharma_schema,
    normalize_product_name,
)
from .enterprise_bmr_corpus import search_corpus
from .product_chemistry import analyze_product_chemistry, get_product_id, list_route_stages
from .bmr_raw_material_parser import format_material


# ═══════════════════════════════════════════════════════════════════════════════
# Schema + migrations
# ═══════════════════════════════════════════════════════════════════════════════


def ensure_rd_lab_schema() -> None:
    """Ensure all R&D lab tables and columns exist, idempotently."""
    ensure_pharma_schema()
    db.ensure_columns('rd_experiments', {
        'product_name': 'TEXT NOT NULL',
        'route_name': 'TEXT',
        'ksm': 'TEXT',
        'solvent': 'TEXT',
        'catalyst': 'TEXT',
        'notes': 'TEXT',
        'status': "TEXT DEFAULT 'planned'",
        'created_by': 'INTEGER',
    })
    db.ensure_columns('rd_experiment_stages', {
        'experiment_id': 'INTEGER NOT NULL',
        'stage_no': 'INTEGER NOT NULL',
        'stage_name': 'TEXT NOT NULL',
        'temperature_c': 'REAL',
        'ph_value': 'REAL',
        'pressure_bar': 'REAL',
        'reaction_time_minutes': 'REAL',
        'mixing_speed_rpm': 'REAL',
        'atmosphere': 'TEXT',
        'equipment_code': 'TEXT',
        'solvent': 'TEXT',
        'catalyst': 'TEXT',
        'rm_description': 'TEXT',
        'theoretical_yield_pct': 'REAL',
        'actual_yield_pct': 'REAL',
        'input_qty': 'REAL',
        'output_qty': 'REAL',
        'purity_pct': 'REAL',
        'material_balance_json': "TEXT DEFAULT '{}'",
        'notes': 'TEXT',
        'parent_stage_id': 'INTEGER',
        'substep_no': 'INTEGER',
    })
    db.ensure_columns('rd_experiment_raw_materials', {
        'experiment_id': 'INTEGER NOT NULL',
        'name': 'TEXT NOT NULL',
        'quantity': 'REAL',
        'unit': 'TEXT',
        'unit_type': "TEXT DEFAULT 'mass'",
        'molecular_weight_g_mol': 'REAL',
        'notes': 'TEXT',
    })
    db.ensure_columns('rd_stage_raw_materials', {
        'experiment_id': 'INTEGER NOT NULL',
        'stage_id': 'INTEGER',
        'experiment_raw_material_id': 'INTEGER',
        'rm_name': 'TEXT NOT NULL',
        'planned_qty': 'REAL',
        'planned_unit': 'TEXT',
        'actual_qty_used': 'REAL',
        'actual_unit': 'TEXT',
        'wastage_qty': 'REAL DEFAULT 0',
        'recovery_qty': 'REAL DEFAULT 0',
        'is_override': 'INTEGER DEFAULT 0',
        'notes': 'TEXT',
    })
    db.ensure_columns('rd_experiment_solvents', {
        'experiment_id': 'INTEGER NOT NULL',
        'name': 'TEXT NOT NULL',
        'quantity_ml': 'REAL',
        'notes': 'TEXT',
    })
    db.ensure_columns('rd_impurity_profiles', {
        'experiment_id': 'INTEGER NOT NULL',
        'stage_id': 'INTEGER',
        'impurity_name': 'TEXT NOT NULL',
        'rrt': 'REAL',
        'value_pct': 'REAL DEFAULT 0',
        'impurity_type': "TEXT DEFAULT 'unknown'",
        'notes': 'TEXT',
    })
    db.ensure_columns('rd_stage_measurements', {
        'experiment_id': 'INTEGER NOT NULL',
        'stage_id': 'INTEGER',
        'measurement_type': 'TEXT NOT NULL',
        'value': 'REAL',
        'unit': 'TEXT',
        'target_min': 'REAL',
        'target_max': 'REAL',
        'is_within_spec': 'INTEGER DEFAULT 1',
        'instrument_id': 'TEXT',
        'recorded_by': 'INTEGER',
        'notes': 'TEXT',
    })
    db.ensure_columns('rd_stage_tests', {
        'experiment_id': 'INTEGER NOT NULL',
        'stage_id': 'INTEGER',
        'test_name': 'TEXT NOT NULL',
        'method_ref': 'TEXT',
        'specification': 'TEXT',
        'result_value': 'TEXT',
        'result_unit': 'TEXT',
        'pass_fail': "TEXT DEFAULT 'pending'",
        'conversion_pct': 'REAL',
        'rrt': 'REAL',
        'analyst_id': 'INTEGER',
        'instrument_id': 'TEXT',
        'sample_ref': 'TEXT',
        'raw_data_ref': 'TEXT',
        'tested_at': 'TEXT',
        'reviewed_by': 'INTEGER',
        'reviewed_at': 'TEXT',
        'notes': 'TEXT',
    })
    db.ensure_columns('rd_stage_conversions', {
        'experiment_id': 'INTEGER NOT NULL',
        'stage_id': 'INTEGER',
        'timepoint_minutes': 'REAL',
        'conversion_pct': 'REAL',
        'sample_ref': 'TEXT',
        'notes': 'TEXT',
    })
    db.ensure_columns('rd_experiment_notes', {
        'experiment_id': 'INTEGER NOT NULL',
        'stage_id': 'INTEGER',
        'note_type': "TEXT DEFAULT 'observation'",
        'content': 'TEXT NOT NULL',
        'created_by': 'INTEGER',
    })
    db.ensure_columns('rd_experiment_templates', {
        'template_name': 'TEXT NOT NULL',
        'product_name': 'TEXT',
        'route_name': 'TEXT',
        'description': 'TEXT',
        'stages_json': "TEXT DEFAULT '[]'",
        'raw_materials_json': "TEXT DEFAULT '[]'",
        'solvents_json': "TEXT DEFAULT '[]'",
        'tests_json': "TEXT DEFAULT '[]'",
        'target_conditions_json': "TEXT DEFAULT '{}'",
        'ai_generated': 'INTEGER DEFAULT 0',
        'source_experiment_id': 'INTEGER',
        'created_by': 'INTEGER',
    })
    # Helpful indexes
    for table, cols in [
        ('rd_experiments', 'product_name'),
        ('rd_experiments', 'status'),
        ('rd_experiment_stages', 'experiment_id'),
        ('rd_experiment_raw_materials', 'experiment_id'),
        ('rd_stage_raw_materials', 'experiment_id'),
        ('rd_stage_raw_materials', 'stage_id'),
        ('rd_impurity_profiles', 'experiment_id'),
        ('rd_stage_measurements', 'experiment_id'),
        ('rd_stage_tests', 'experiment_id'),
        ('rd_experiment_notes', 'experiment_id'),
    ]:
        try:
            db.execute(f'CREATE INDEX IF NOT EXISTS idx_{table}_{cols} ON {table}({cols})')
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════════════════════
# Experiments
# ═══════════════════════════════════════════════════════════════════════════════


def create_experiment(user_id: int | None, data: dict[str, Any]) -> int:
    """Create a new R&D experiment from blank data or a seed."""
    rid = db.execute(
        'INSERT INTO rd_experiments(product_name, route_name, ksm, solvent, catalyst, notes, status, created_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
        (
            normalize_product_name(data.get('product_name')),
            data.get('route_name', ''),
            data.get('ksm', ''),
            data.get('solvent', ''),
            data.get('catalyst', ''),
            data.get('notes', ''),
            data.get('status', 'planned'),
            user_id,
        ),
    )
    for rm in data.get('raw_materials', []) or []:
        if str(rm.get('name', '')).strip():
            add_experiment_raw_material(user_id, rid, rm, audit=False)
    for sol in data.get('solvents', []) or []:
        if str(sol.get('name', '')).strip():
            add_solvent(user_id, rid, sol, audit=False)
    db.audit(user_id, 'create', 'rd_experiment', rid, data)
    return rid


def update_experiment(user_id: int | None, exp_id: int, data: dict[str, Any]) -> int:
    db.execute(
        'UPDATE rd_experiments SET product_name=?, route_name=?, ksm=?, solvent=?, catalyst=?, notes=?, status=?, updated_at=CURRENT_TIMESTAMP WHERE id=?',
        (
            normalize_product_name(data.get('product_name')),
            data.get('route_name', ''),
            data.get('ksm', ''),
            data.get('solvent', ''),
            data.get('catalyst', ''),
            data.get('notes', ''),
            data.get('status', 'planned'),
            exp_id,
        ),
    )
    if 'raw_materials' in data:
        db.execute('DELETE FROM rd_experiment_raw_materials WHERE experiment_id=?', (exp_id,))
        for rm in data.get('raw_materials', []) or []:
            if str(rm.get('name', '')).strip():
                add_experiment_raw_material(user_id, exp_id, rm, audit=False)
    if 'solvents' in data:
        db.execute('DELETE FROM rd_experiment_solvents WHERE experiment_id=?', (exp_id,))
        for sol in data.get('solvents', []) or []:
            if str(sol.get('name', '')).strip():
                add_solvent(user_id, exp_id, sol, audit=False)
    db.audit(user_id, 'update', 'rd_experiment', exp_id, data)
    return exp_id


def get_experiment(exp_id: int) -> dict[str, Any]:
    exp = db.one('SELECT * FROM rd_experiments WHERE id=?', (exp_id,))
    if not exp:
        return {}
    return _build_experiment_detail(exp_id)


def list_experiments(product_name: str | None = None, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    params: list[Any] = []
    clauses = ['1=1']
    if product_name:
        clauses.append('lower(product_name) LIKE ?')
        params.append(f'%{product_name.lower()}%')
    if status:
        clauses.append('status=?')
        params.append(status)
    sql = f"SELECT * FROM rd_experiments WHERE {' AND '.join(clauses)} ORDER BY updated_at DESC LIMIT ?"
    return db.query(sql, (*params, limit))


def delete_experiment(user_id: int | None, exp_id: int) -> None:
    for table in (
        'rd_stage_raw_materials',
        'rd_stage_measurements',
        'rd_stage_tests',
        'rd_stage_conversions',
        'rd_experiment_notes',
        'rd_impurity_profiles',
        'rd_experiment_stages',
        'rd_experiment_raw_materials',
        'rd_experiment_solvents',
        'rd_brain_conversations',
    ):
        db.execute(f'DELETE FROM {table} WHERE experiment_id=?', (exp_id,))
    db.execute('DELETE FROM rd_experiments WHERE id=?', (exp_id,))
    db.audit(user_id, 'delete', 'rd_experiment', exp_id)


# ═══════════════════════════════════════════════════════════════════════════════
# Stages
# ═══════════════════════════════════════════════════════════════════════════════


def add_stage(user_id: int | None, exp_id: int, data: dict[str, Any]) -> int:
    rid = db.execute(
        '''INSERT INTO rd_experiment_stages(
            experiment_id, stage_no, stage_name, temperature_c, ph_value, pressure_bar,
            reaction_time_minutes, mixing_speed_rpm, atmosphere, equipment_code, solvent, catalyst,
            rm_description, theoretical_yield_pct, actual_yield_pct, input_qty, output_qty,
            purity_pct, material_balance_json, notes, parent_stage_id, substep_no
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
        (
            exp_id,
            int(data.get('stage_no') or 1),
            data.get('stage_name', 'Stage'),
            _float_or_none(data.get('temperature_c')),
            _float_or_none(data.get('ph_value')),
            _float_or_none(data.get('pressure_bar')),
            _float_or_none(data.get('reaction_time_minutes')),
            _float_or_none(data.get('mixing_speed_rpm')),
            data.get('atmosphere', ''),
            data.get('equipment_code', ''),
            data.get('solvent', ''),
            data.get('catalyst', ''),
            data.get('rm_description', ''),
            _float_or_none(data.get('theoretical_yield_pct')),
            _float_or_none(data.get('actual_yield_pct')),
            _float_or_none(data.get('input_qty')),
            _float_or_none(data.get('output_qty')),
            _float_or_none(data.get('purity_pct')),
            json.dumps(data.get('material_balance') or {}),
            data.get('notes', ''),
            int(data.get('parent_stage_id')) if data.get('parent_stage_id') else None,
            int(data.get('substep_no')) if data.get('substep_no') else None,
        ),
    )
    db.audit(user_id, 'create', 'rd_experiment_stage', rid, {'experiment_id': exp_id, **data})
    return rid


def update_stage(user_id: int | None, stage_id: int, data: dict[str, Any]) -> int:
    db.execute(
        '''UPDATE rd_experiment_stages SET
            stage_no=?, stage_name=?, temperature_c=?, ph_value=?, pressure_bar=?,
            reaction_time_minutes=?, mixing_speed_rpm=?, atmosphere=?, solvent=?, catalyst=?,
            rm_description=?, theoretical_yield_pct=?, actual_yield_pct=?, input_qty=?, output_qty=?,
            purity_pct=?, material_balance_json=?, notes=?, parent_stage_id=?, substep_no=?,
            updated_at=CURRENT_TIMESTAMP WHERE id=?''',
        (
            int(data.get('stage_no') or 1),
            data.get('stage_name', 'Stage'),
            _float_or_none(data.get('temperature_c')),
            _float_or_none(data.get('ph_value')),
            _float_or_none(data.get('pressure_bar')),
            _float_or_none(data.get('reaction_time_minutes')),
            _float_or_none(data.get('mixing_speed_rpm')),
            data.get('atmosphere', ''),
            data.get('equipment_code', ''),
            data.get('solvent', ''),
            data.get('catalyst', ''),
            data.get('rm_description', ''),
            _float_or_none(data.get('theoretical_yield_pct')),
            _float_or_none(data.get('actual_yield_pct')),
            _float_or_none(data.get('input_qty')),
            _float_or_none(data.get('output_qty')),
            _float_or_none(data.get('purity_pct')),
            json.dumps(data.get('material_balance') or {}),
            data.get('notes', ''),
            int(data.get('parent_stage_id')) if data.get('parent_stage_id') else None,
            int(data.get('substep_no')) if data.get('substep_no') else None,
            stage_id,
        ),
    )
    db.audit(user_id, 'update', 'rd_experiment_stage', stage_id, data)
    return stage_id


def delete_stage(user_id: int | None, stage_id: int) -> None:
    exp = db.one('SELECT experiment_id FROM rd_experiment_stages WHERE id=?', (stage_id,))
    exp_id = exp['experiment_id'] if exp else None
    db.execute('DELETE FROM rd_stage_raw_materials WHERE stage_id=?', (stage_id,))
    db.execute('DELETE FROM rd_stage_measurements WHERE stage_id=?', (stage_id,))
    db.execute('DELETE FROM rd_stage_tests WHERE stage_id=?', (stage_id,))
    db.execute('DELETE FROM rd_stage_conversions WHERE stage_id=?', (stage_id,))
    db.execute('DELETE FROM rd_experiment_notes WHERE stage_id=?', (stage_id,))
    db.execute('DELETE FROM rd_impurity_profiles WHERE stage_id=?', (stage_id,))
    db.execute('DELETE FROM rd_experiment_stages WHERE id=?', (stage_id,))
    db.audit(user_id, 'delete', 'rd_experiment_stage', stage_id, {'experiment_id': exp_id})


# ═══════════════════════════════════════════════════════════════════════════════
# Experiment-level raw materials and solvents
# ═══════════════════════════════════════════════════════════════════════════════


def add_experiment_raw_material(user_id: int | None, exp_id: int, data: dict[str, Any], audit: bool = True) -> int:
    rid = db.execute(
        'INSERT INTO rd_experiment_raw_materials(experiment_id, name, quantity, unit, unit_type, molecular_weight_g_mol, notes) VALUES (?, ?, ?, ?, ?, ?, ?)',
        (
            exp_id,
            data.get('name', ''),
            _float_or_none(data.get('quantity')),
            data.get('unit', ''),
            data.get('unit_type', 'mass'),
            _float_or_none(data.get('molecular_weight_g_mol')),
            data.get('notes', ''),
        ),
    )
    if audit:
        db.audit(user_id, 'create', 'rd_raw_material', rid, data)
    return rid


def update_experiment_raw_material(user_id: int | None, rm_id: int, data: dict[str, Any]) -> int:
    db.execute(
        'UPDATE rd_experiment_raw_materials SET name=?, quantity=?, unit=?, unit_type=?, molecular_weight_g_mol=?, notes=? WHERE id=?',
        (
            data.get('name', ''),
            _float_or_none(data.get('quantity')),
            data.get('unit', ''),
            data.get('unit_type', 'mass'),
            _float_or_none(data.get('molecular_weight_g_mol')),
            data.get('notes', ''),
            rm_id,
        ),
    )
    db.audit(user_id, 'update', 'rd_raw_material', rm_id, data)
    return rm_id


def delete_experiment_raw_material(user_id: int | None, rm_id: int) -> None:
    db.execute('DELETE FROM rd_experiment_raw_materials WHERE id=?', (rm_id,))
    db.execute('DELETE FROM rd_stage_raw_materials WHERE experiment_raw_material_id=?', (rm_id,))
    db.audit(user_id, 'delete', 'rd_raw_material', rm_id)


def add_solvent(user_id: int | None, exp_id: int, data: dict[str, Any], audit: bool = True) -> int:
    rid = db.execute(
        'INSERT INTO rd_experiment_solvents(experiment_id, name, quantity_ml, notes) VALUES (?, ?, ?, ?)',
        (exp_id, data.get('name', ''), _float_or_none(data.get('quantity_ml')), data.get('notes', '')),
    )
    if audit:
        db.audit(user_id, 'create', 'rd_solvent', rid, data)
    return rid


def update_solvent(user_id: int | None, sol_id: int, data: dict[str, Any]) -> int:
    db.execute(
        'UPDATE rd_experiment_solvents SET name=?, quantity_ml=?, notes=? WHERE id=?',
        (data.get('name', ''), _float_or_none(data.get('quantity_ml')), data.get('notes', ''), sol_id),
    )
    db.audit(user_id, 'update', 'rd_solvent', sol_id, data)
    return sol_id


def delete_solvent(user_id: int | None, sol_id: int) -> None:
    db.execute('DELETE FROM rd_experiment_solvents WHERE id=?', (sol_id,))
    db.audit(user_id, 'delete', 'rd_solvent', sol_id)


# ═══════════════════════════════════════════════════════════════════════════════
# Stage-level raw materials (the actual consumption log)
# ═══════════════════════════════════════════════════════════════════════════════


def _link_stage_rm_to_experiment_rm(exp_id: int, data: dict[str, Any]) -> dict[str, Any]:
    rm_id = data.get('experiment_raw_material_id')
    rm_name = str(data.get('rm_name') or '').strip()
    if not rm_id and rm_name:
        rm = db.one(
            'SELECT id, unit, molecular_weight_g_mol FROM rd_experiment_raw_materials WHERE experiment_id=? AND lower(name)=lower(?)',
            (exp_id, rm_name),
        )
        if rm:
            data['experiment_raw_material_id'] = rm['id']
            if not data.get('planned_unit'):
                data['planned_unit'] = rm.get('unit', '')
            if not data.get('mol_weight') and rm.get('molecular_weight_g_mol'):
                data['mol_weight'] = rm['molecular_weight_g_mol']
    return data


def add_stage_raw_material(user_id: int | None, stage_id: int, data: dict[str, Any]) -> int:
    data = _link_stage_rm_to_experiment_rm(int(data.get('experiment_id') or 0), data)
    exp_id = int(data.get('experiment_id') or 0)
    rid = db.execute(
        '''INSERT INTO rd_stage_raw_materials(
            experiment_id, stage_id, experiment_raw_material_id, rm_name, planned_qty, planned_unit,
            actual_qty_used, actual_unit, wastage_qty, recovery_qty, batch_no, vendor, grade,
            mol_weight, equivalents, mole_ratio, is_override, notes, created_by
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
        (
            exp_id,
            stage_id,
            data.get('experiment_raw_material_id'),
            data.get('rm_name', ''),
            _float_or_none(data.get('planned_qty')),
            data.get('planned_unit', ''),
            _float_or_none(data.get('actual_qty_used')),
            data.get('actual_unit', ''),
            _float_or_none(data.get('wastage_qty')) or 0,
            _float_or_none(data.get('recovery_qty')) or 0,
            data.get('batch_no', ''),
            data.get('vendor', ''),
            data.get('grade', ''),
            _float_or_none(data.get('mol_weight')),
            _float_or_none(data.get('equivalents')),
            _float_or_none(data.get('mole_ratio')),
            1 if data.get('is_override') else 0,
            data.get('notes', ''),
            user_id,
        ),
    )
    db.audit(user_id, 'create', 'rd_stage_raw_material', rid, data)
    return rid


def update_stage_raw_material(user_id: int | None, srm_id: int, data: dict[str, Any]) -> int:
    existing = db.one('SELECT experiment_id FROM rd_stage_raw_materials WHERE id=?', (srm_id,))
    exp_id = existing['experiment_id'] if existing else int(data.get('experiment_id') or 0)
    data = _link_stage_rm_to_experiment_rm(exp_id, data)
    db.execute(
        '''UPDATE rd_stage_raw_materials SET
            experiment_raw_material_id=?, rm_name=?, planned_qty=?, planned_unit=?,
            actual_qty_used=?, actual_unit=?, wastage_qty=?, recovery_qty=?, batch_no=?,
            vendor=?, grade=?, mol_weight=?, equivalents=?, mole_ratio=?, is_override=?, notes=?
        WHERE id=?''',
        (
            data.get('experiment_raw_material_id'),
            data.get('rm_name', ''),
            _float_or_none(data.get('planned_qty')),
            data.get('planned_unit', ''),
            _float_or_none(data.get('actual_qty_used')),
            data.get('actual_unit', ''),
            _float_or_none(data.get('wastage_qty')) or 0,
            _float_or_none(data.get('recovery_qty')) or 0,
            data.get('batch_no', ''),
            data.get('vendor', ''),
            data.get('grade', ''),
            _float_or_none(data.get('mol_weight')),
            _float_or_none(data.get('equivalents')),
            _float_or_none(data.get('mole_ratio')),
            1 if data.get('is_override') else 0,
            data.get('notes', ''),
            srm_id,
        ),
    )
    db.audit(user_id, 'update', 'rd_stage_raw_material', srm_id, data)
    return srm_id


def delete_stage_raw_material(user_id: int | None, srm_id: int) -> None:
    db.execute('DELETE FROM rd_stage_raw_materials WHERE id=?', (srm_id,))
    db.audit(user_id, 'delete', 'rd_stage_raw_material', srm_id)


# ═══════════════════════════════════════════════════════════════════════════════
# Impurities, measurements, tests, conversions, notes
# ═══════════════════════════════════════════════════════════════════════════════


def add_impurity(user_id: int | None, exp_id: int, data: dict[str, Any]) -> int:
    rid = db.execute(
        'INSERT INTO rd_impurity_profiles(experiment_id, stage_id, impurity_name, rrt, value_pct, impurity_type, notes) VALUES (?, ?, ?, ?, ?, ?, ?)',
        (
            exp_id,
            int(data.get('stage_id') or 0) or None,
            data.get('impurity_name', 'Unknown'),
            _float_or_none(data.get('rrt')),
            _float_or_none(data.get('value_pct')),
            data.get('impurity_type', 'unknown'),
            data.get('notes', ''),
        ),
    )
    db.audit(user_id, 'create', 'rd_impurity_profile', rid, data)
    return rid


def update_impurity(user_id: int | None, imp_id: int, data: dict[str, Any]) -> int:
    db.execute(
        'UPDATE rd_impurity_profiles SET stage_id=?, impurity_name=?, rrt=?, value_pct=?, impurity_type=?, notes=? WHERE id=?',
        (
            int(data.get('stage_id') or 0) or None,
            data.get('impurity_name', 'Unknown'),
            _float_or_none(data.get('rrt')),
            _float_or_none(data.get('value_pct')),
            data.get('impurity_type', 'unknown'),
            data.get('notes', ''),
            imp_id,
        ),
    )
    db.audit(user_id, 'update', 'rd_impurity_profile', imp_id, data)
    return imp_id


def delete_impurity(user_id: int | None, imp_id: int) -> None:
    db.execute('DELETE FROM rd_impurity_profiles WHERE id=?', (imp_id,))
    db.audit(user_id, 'delete', 'rd_impurity_profile', imp_id)


def add_measurement(user_id: int | None, exp_id: int, data: dict[str, Any]) -> int:
    val = _float_or_none(data.get('value'))
    tmin = _float_or_none(data.get('target_min'))
    tmax = _float_or_none(data.get('target_max'))
    within = 1
    if val is not None and (tmin is not None or tmax is not None):
        within = 1 if ((tmin is None or val >= tmin) and (tmax is None or val <= tmax)) else 0
    rid = db.execute(
        '''INSERT INTO rd_stage_measurements(
            experiment_id, stage_id, measurement_type, value, unit, target_min, target_max,
            is_within_spec, instrument_id, recorded_by, notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
        (
            exp_id,
            int(data.get('stage_id') or 0) or None,
            data.get('measurement_type', ''),
            val,
            data.get('unit', ''),
            tmin,
            tmax,
            within,
            data.get('instrument_id', ''),
            user_id,
            data.get('notes', ''),
        ),
    )
    db.audit(user_id, 'create', 'rd_stage_measurement', rid, data)
    return rid


def update_measurement(user_id: int | None, mid: int, data: dict[str, Any]) -> int:
    val = _float_or_none(data.get('value'))
    tmin = _float_or_none(data.get('target_min'))
    tmax = _float_or_none(data.get('target_max'))
    within = 1
    if val is not None and (tmin is not None or tmax is not None):
        within = 1 if ((tmin is None or val >= tmin) and (tmax is None or val <= tmax)) else 0
    db.execute(
        '''UPDATE rd_stage_measurements SET
            stage_id=?, measurement_type=?, value=?, unit=?, target_min=?, target_max=?,
            is_within_spec=?, instrument_id=?, notes=? WHERE id=?''',
        (
            int(data.get('stage_id') or 0) or None,
            data.get('measurement_type', ''),
            val,
            data.get('unit', ''),
            tmin,
            tmax,
            within,
            data.get('instrument_id', ''),
            data.get('notes', ''),
            mid,
        ),
    )
    db.audit(user_id, 'update', 'rd_stage_measurement', mid, data)
    return mid


def delete_measurement(user_id: int | None, mid: int) -> None:
    db.execute('DELETE FROM rd_stage_measurements WHERE id=?', (mid,))
    db.audit(user_id, 'delete', 'rd_stage_measurement', mid)


def add_test(user_id: int | None, exp_id: int, data: dict[str, Any]) -> int:
    rid = db.execute(
        '''INSERT INTO rd_stage_tests(
            experiment_id, stage_id, test_name, method_ref, specification, result_value,
            result_unit, pass_fail, conversion_pct, rrt, analyst_id, instrument_id,
            sample_ref, raw_data_ref, tested_at, reviewed_by, reviewed_at, notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
        (
            exp_id,
            int(data.get('stage_id') or 0) or None,
            data.get('test_name', ''),
            data.get('method_ref', ''),
            data.get('specification', ''),
            data.get('result_value', ''),
            data.get('result_unit', ''),
            data.get('pass_fail', 'pending'),
            _float_or_none(data.get('conversion_pct')),
            _float_or_none(data.get('rrt')),
            data.get('analyst_id'),
            data.get('instrument_id', ''),
            data.get('sample_ref', ''),
            data.get('raw_data_ref', ''),
            data.get('tested_at', ''),
            data.get('reviewed_by'),
            data.get('reviewed_at', ''),
            data.get('notes', ''),
        ),
    )
    db.audit(user_id, 'create', 'rd_stage_test', rid, data)
    return rid


def update_test(user_id: int | None, test_id: int, data: dict[str, Any]) -> int:
    db.execute(
        '''UPDATE rd_stage_tests SET
            stage_id=?, test_name=?, method_ref=?, specification=?, result_value=?,
            result_unit=?, pass_fail=?, conversion_pct=?, rrt=?, analyst_id=?,
            instrument_id=?, sample_ref=?, raw_data_ref=?, tested_at=?, reviewed_by=?, reviewed_at=?, notes=?
        WHERE id=?''',
        (
            int(data.get('stage_id') or 0) or None,
            data.get('test_name', ''),
            data.get('method_ref', ''),
            data.get('specification', ''),
            data.get('result_value', ''),
            data.get('result_unit', ''),
            data.get('pass_fail', 'pending'),
            _float_or_none(data.get('conversion_pct')),
            _float_or_none(data.get('rrt')),
            data.get('analyst_id'),
            data.get('instrument_id', ''),
            data.get('sample_ref', ''),
            data.get('raw_data_ref', ''),
            data.get('tested_at', ''),
            data.get('reviewed_by'),
            data.get('reviewed_at', ''),
            data.get('notes', ''),
            test_id,
        ),
    )
    db.audit(user_id, 'update', 'rd_stage_test', test_id, data)
    return test_id


def delete_test(user_id: int | None, test_id: int) -> None:
    db.execute('DELETE FROM rd_stage_tests WHERE id=?', (test_id,))
    db.audit(user_id, 'delete', 'rd_stage_test', test_id)


def add_conversion(user_id: int | None, exp_id: int, data: dict[str, Any]) -> int:
    rid = db.execute(
        'INSERT INTO rd_stage_conversions(experiment_id, stage_id, timepoint_minutes, conversion_pct, sample_ref, notes) VALUES (?, ?, ?, ?, ?, ?)',
        (
            exp_id,
            int(data.get('stage_id') or 0) or None,
            _float_or_none(data.get('timepoint_minutes')),
            _float_or_none(data.get('conversion_pct')),
            data.get('sample_ref', ''),
            data.get('notes', ''),
        ),
    )
    db.audit(user_id, 'create', 'rd_stage_conversion', rid, data)
    return rid


def update_conversion(user_id: int | None, cid: int, data: dict[str, Any]) -> int:
    db.execute(
        'UPDATE rd_stage_conversions SET stage_id=?, timepoint_minutes=?, conversion_pct=?, sample_ref=?, notes=? WHERE id=?',
        (
            int(data.get('stage_id') or 0) or None,
            _float_or_none(data.get('timepoint_minutes')),
            _float_or_none(data.get('conversion_pct')),
            data.get('sample_ref', ''),
            data.get('notes', ''),
            cid,
        ),
    )
    db.audit(user_id, 'update', 'rd_stage_conversion', cid, data)
    return cid


def delete_conversion(user_id: int | None, cid: int) -> None:
    db.execute('DELETE FROM rd_stage_conversions WHERE id=?', (cid,))
    db.audit(user_id, 'delete', 'rd_stage_conversion', cid)


def add_note(user_id: int | None, exp_id: int, data: dict[str, Any]) -> int:
    rid = db.execute(
        'INSERT INTO rd_experiment_notes(experiment_id, stage_id, note_type, content, created_by) VALUES (?, ?, ?, ?, ?)',
        (
            exp_id,
            int(data.get('stage_id') or 0) or None,
            data.get('note_type', 'observation'),
            data.get('content', ''),
            user_id,
        ),
    )
    db.audit(user_id, 'create', 'rd_experiment_note', rid, data)
    return rid


def update_note(user_id: int | None, note_id: int, data: dict[str, Any]) -> int:
    db.execute(
        'UPDATE rd_experiment_notes SET stage_id=?, note_type=?, content=? WHERE id=?',
        (
            int(data.get('stage_id') or 0) or None,
            data.get('note_type', 'observation'),
            data.get('content', ''),
            note_id,
        ),
    )
    db.audit(user_id, 'update', 'rd_experiment_note', note_id, data)
    return note_id


def delete_note(user_id: int | None, note_id: int) -> None:
    db.execute('DELETE FROM rd_experiment_notes WHERE id=?', (note_id,))
    db.audit(user_id, 'delete', 'rd_experiment_note', note_id)


# ═══════════════════════════════════════════════════════════════════════════════
# Detail builder
# ═══════════════════════════════════════════════════════════════════════════════


def _build_experiment_detail(exp_id: int) -> dict[str, Any]:
    exp = db.one('SELECT * FROM rd_experiments WHERE id=?', (exp_id,))
    stages = db.query('SELECT * FROM rd_experiment_stages WHERE experiment_id=? ORDER BY stage_no, substep_no', (exp_id,))
    rms = db.query('SELECT * FROM rd_experiment_raw_materials WHERE experiment_id=? ORDER BY name', (exp_id,))
    solvents = db.query('SELECT * FROM rd_experiment_solvents WHERE experiment_id=? ORDER BY name', (exp_id,))
    impurities = db.query('SELECT * FROM rd_impurity_profiles WHERE experiment_id=? ORDER BY impurity_name', (exp_id,))
    measurements = db.query('SELECT * FROM rd_stage_measurements WHERE experiment_id=? ORDER BY recorded_at DESC', (exp_id,))
    tests = db.query('SELECT * FROM rd_stage_tests WHERE experiment_id=? ORDER BY created_at DESC', (exp_id,))
    conversions = db.query('SELECT * FROM rd_stage_conversions WHERE experiment_id=? ORDER BY timepoint_minutes', (exp_id,))
    notes = db.query('SELECT * FROM rd_experiment_notes WHERE experiment_id=? ORDER BY created_at DESC', (exp_id,))
    stage_rms = db.query('SELECT * FROM rd_stage_raw_materials WHERE experiment_id=? ORDER BY stage_id, rm_name', (exp_id,))
    return {
        'experiment': exp,
        'stages': stages,
        'raw_materials': rms,
        'solvents': solvents,
        'impurities': impurities,
        'measurements': measurements,
        'tests': tests,
        'conversions': conversions,
        'notes': notes,
        'stage_raw_materials': stage_rms,
        'material_balance': calculate_material_balance(exp_id),
        'ledger': get_experiment_rm_ledger(exp_id),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Material balance & RM ledger
# ═══════════════════════════════════════════════════════════════════════════════


def calculate_material_balance(exp_id: int) -> dict[str, Any]:
    """Return a material balance with kg-equivalent totals and warnings."""
    stages = db.query('SELECT * FROM rd_experiment_stages WHERE experiment_id=? ORDER BY stage_no', (exp_id,))
    stage_rms = db.query('SELECT * FROM rd_stage_raw_materials WHERE experiment_id=?', (exp_id,))
    exp_rms = {rm['id']: rm for rm in db.query('SELECT * FROM rd_experiment_raw_materials WHERE experiment_id=?', (exp_id,))}

    total_planned_kg = 0.0
    total_actual_kg = 0.0
    total_waste_kg = 0.0
    total_recovery_kg = 0.0
    total_output_kg = 0.0
    stage_breakdown = []
    warnings: list[str] = []

    for stage in stages:
        sid = stage['id']
        sname = stage['stage_name']
        stage_planned_kg = 0.0
        stage_actual_kg = 0.0
        stage_waste_kg = 0.0
        stage_recovery_kg = 0.0
        stage_output = float(stage.get('output_qty') or 0)
        # kg output only meaningful if unit is mass-like; assume kg/g/mg
        output_kg, ow = _to_kg(stage_output, 'kg', 'mass')
        if output_kg is None:
            output_kg = 0.0
        warnings.extend(ow)

        for srm in stage_rms:
            if srm.get('stage_id') != sid:
                continue
            erm = exp_rms.get(srm.get('experiment_raw_material_id') or 0)
            unit = srm.get('planned_unit') or erm.get('unit') if erm else srm.get('planned_unit')
            unit_type = erm.get('unit_type') if erm else 'mass'
            mw = srm.get('mol_weight') or (erm.get('molecular_weight_g_mol') if erm else None)

            pq = float(srm.get('planned_qty') or 0)
            aq = float(srm.get('actual_qty_used') or 0)
            wq = float(srm.get('wastage_qty') or 0)
            rq = float(srm.get('recovery_qty') or 0)

            pq_kg, w1 = _to_kg(pq, unit, unit_type, mw)
            aq_kg, w2 = _to_kg(aq, unit, unit_type, mw)
            wq_kg, w3 = _to_kg(wq, unit, unit_type, mw)
            rq_kg, w4 = _to_kg(rq, unit, unit_type, mw)

            for w in (w1 + w2 + w3 + w4):
                if w not in warnings:
                    warnings.append(w)

            stage_planned_kg += pq_kg or 0
            stage_actual_kg += aq_kg or 0
            stage_waste_kg += wq_kg or 0
            stage_recovery_kg += rq_kg or 0
            total_planned_kg += pq_kg or 0
            total_actual_kg += aq_kg or 0
            total_waste_kg += wq_kg or 0
            total_recovery_kg += rq_kg or 0

        total_output_kg += output_kg
        stage_breakdown.append({
            'stage_id': sid,
            'stage_name': sname,
            'planned_kg': round(stage_planned_kg, 4),
            'actual_kg': round(stage_actual_kg, 4),
            'waste_kg': round(stage_waste_kg, 4),
            'recovery_kg': round(stage_recovery_kg, 4),
            'output_kg': round(output_kg, 4),
        })

    unaccounted_kg = total_planned_kg - total_actual_kg - total_recovery_kg - total_output_kg
    unaccounted_pct = (unaccounted_kg / total_planned_kg * 100.0) if total_planned_kg > 0 else 0.0

    return {
        'total_planned_rm_kg_eq': round(total_planned_kg, 4),
        'total_actual_rm_used_kg_eq': round(total_actual_kg, 4),
        'total_wastage_kg_eq': round(total_waste_kg, 4),
        'total_recovery_kg_eq': round(total_recovery_kg, 4),
        'total_output_kg_eq': round(total_output_kg, 4),
        'unaccounted_loss_kg_eq': round(unaccounted_kg, 4),
        'unaccounted_pct': round(unaccounted_pct, 2),
        'stage_breakdown': stage_breakdown,
        'warnings': warnings,
    }


def get_experiment_rm_ledger(exp_id: int) -> dict[str, Any]:
    """Return experiment-level RM with stage consumption and remaining balance."""
    exp_rms = db.query('SELECT * FROM rd_experiment_raw_materials WHERE experiment_id=? ORDER BY name', (exp_id,))
    stage_rms = db.query('SELECT * FROM rd_stage_raw_materials WHERE experiment_id=?', (exp_id,))
    ledger = []
    global_warnings: list[str] = []
    for rm in exp_rms:
        rm_id = rm['id']
        name = rm['name']
        total_qty = float(rm.get('quantity') or 0)
        unit = rm.get('unit', '')
        unit_type = rm.get('unit_type') or 'mass'
        mw = rm.get('molecular_weight_g_mol')

        linked = [s for s in stage_rms if s.get('experiment_raw_material_id') == rm_id]
        if not linked:
            linked = [s for s in stage_rms if str(s.get('rm_name') or '').strip().lower() == name.strip().lower()]

        planned_used = sum(float(s.get('planned_qty') or 0) for s in linked)
        actual_used = sum(float(s.get('actual_qty_used') or 0) for s in linked)
        waste = sum(float(s.get('wastage_qty') or 0) for s in linked)
        recovery = sum(float(s.get('recovery_qty') or 0) for s in linked)
        overrides = sum(1 for s in linked if s.get('is_override'))

        total_kg, w1 = _to_kg(total_qty, unit, unit_type, mw)
        planned_kg, w2 = _to_kg(planned_used, unit, unit_type, mw)
        actual_kg, w3 = _to_kg(actual_used, unit, unit_type, mw)
        waste_kg, w4 = _to_kg(waste, unit, unit_type, mw)
        recovery_kg, w5 = _to_kg(recovery, unit, unit_type, mw)
        for w in (w1 + w2 + w3 + w4 + w5):
            if w not in global_warnings:
                global_warnings.append(w)

        consumed = max(planned_used, actual_used)
        remaining = total_qty - consumed if unit else None
        ledger.append({
            'experiment_raw_material_id': rm_id,
            'name': name,
            'total_qty': total_qty,
            'unit': unit,
            'unit_type': unit_type,
            'molecular_weight_g_mol': mw,
            'total_kg_equivalent': total_kg,
            'planned_used': planned_used,
            'actual_used': actual_used,
            'wastage_qty': waste,
            'recovery_qty': recovery,
            'remaining_qty': remaining,
            'planned_kg_equivalent': planned_kg,
            'actual_kg_equivalent': actual_kg,
            'waste_kg_equivalent': waste_kg,
            'recovery_kg_equivalent': recovery_kg,
            'stage_links': len(linked),
            'override_count': overrides,
            'conversion_warnings': w1 + w2 + w3 + w4 + w5,
        })
    return {'experiment_id': exp_id, 'raw_materials': ledger, 'warnings': global_warnings}


# ═══════════════════════════════════════════════════════════════════════════════
# Trace / audit feed
# ═══════════════════════════════════════════════════════════════════════════════


def get_experiment_trace(exp_id: int) -> list[dict[str, Any]]:
    """Build a chronological activity feed for an experiment."""
    notes = db.query(
        '''SELECT id, stage_id, note_type, content, created_by, created_at, 'note' AS source FROM rd_experiment_notes
        WHERE experiment_id=?''', (exp_id,))
    audit = db.query(
        '''SELECT id, action, entity, entity_id, details, user_id, created_at, 'audit' AS source FROM audit_log
        WHERE entity IN ('rd_experiment','rd_experiment_stage','rd_raw_material','rd_solvent','rd_stage_raw_material','rd_impurity_profile','rd_stage_measurement','rd_stage_test','rd_stage_conversion','rd_experiment_note','rd_experiment_template','rd_experiment_comparison')
        AND (details IS NULL OR details LIKE ? OR entity_id=? OR details LIKE ?)''',
        (exp_id, str(exp_id), f'%"experiment_id": {exp_id}%'),
    )
    # Include brain conversations as trace entries
    chat = db.query(
        '''SELECT id, role AS note_type, content, user_id AS created_by, created_at, 'brain_chat' AS source FROM rd_brain_conversations
        WHERE experiment_id=?''', (exp_id,))

    feed = []
    for row in notes + audit + chat:
        feed.append({
            'id': row['id'],
            'source': row['source'],
            'action': row.get('action') or row.get('note_type') or 'record',
            'entity': row.get('entity', ''),
            'entity_id': row.get('entity_id', ''),
            'stage_id': row.get('stage_id'),
            'content': row.get('content') or row.get('details') or '',
            'user_id': row.get('created_by') or row.get('user_id'),
            'created_at': row.get('created_at'),
        })
    feed.sort(key=lambda x: x['created_at'] or '', reverse=True)
    return feed


# ═══════════════════════════════════════════════════════════════════════════════
# Product discovery & BMR seeding
# ═══════════════════════════════════════════════════════════════════════════════


def list_rd_products(q: str = '', limit: int = 50) -> list[dict[str, Any]]:
    params: list[Any] = []
    where = '1=1'
    if q.strip():
        where = '(lower(canonical_name) LIKE ? OR canonical_name LIKE ?)'
        params.extend([f'%{q.strip().lower()}%', f'%{q.strip()}%'])
    products = db.query(f'SELECT canonical_name as name, confidence, status FROM enterprise_products WHERE {where} ORDER BY canonical_name LIMIT ?', (*params, limit))
    corpus = db.query(
        "SELECT DISTINCT product_name as name FROM enterprise_bmr_documents WHERE product_name != '' AND lower(product_name) LIKE ? ORDER BY product_name LIMIT ?",
        (f'%{q.strip().lower()}%', limit),
    )
    seen = {p['name'].lower() for p in products}
    for row in corpus:
        if row['name'].lower() not in seen:
            products.append({'name': row['name'], 'confidence': 0.6, 'status': 'corpus_only'})
            seen.add(row['name'].lower())
    return products[:limit]


def _seed_experiment_from_product_db(user_id: int | None, product_name: str) -> dict[str, Any]:
    """Create an experiment seeded from the product chemistry route stored in the enterprise brain."""
    canonical = normalize_product_name(product_name)
    stages = list_route_stages(canonical)
    if not stages:
        return {}

    product_id = get_product_id(canonical, create=False)
    impurities: list[dict[str, Any]] = []
    if product_id:
        impurities = db.query(
            "SELECT impurity_name, likely_source, control_strategy, qc_method, acceptance_or_alert, confidence FROM enterprise_product_impurity_controls WHERE product_id=? ORDER BY id",
            (product_id,),
        )

    # Aggregate raw materials and solvents across stages for the experiment-level lists
    rm_names: list[str] = []
    sol_names: list[str] = []
    for st in stages:
        for r in st.get('raw_materials') or []:
            name = r.get('name') if isinstance(r, dict) else str(r)
            if name and name not in rm_names:
                rm_names.append(name)
        for s in st.get('solvents') or []:
            name = s.get('name') if isinstance(s, dict) else str(s)
            if name and name not in sol_names:
                sol_names.append(name)

    exp_data = {
        'product_name': canonical,
        'route_name': f'Product DB route for {canonical}',
        'notes': f'Auto-imported from enterprise product knowledge: {len(stages)} stage(s), {len(rm_names)} raw material(s), {len(sol_names)} solvent(s). All values are editable.',
        'status': 'planned',
        'raw_materials': [{'name': n, 'quantity': 0, 'unit': 'kg', 'unit_type': 'mass', 'notes': 'From product DB'} for n in rm_names[:40]],
        'solvents': [{'name': n, 'quantity_ml': 0, 'notes': 'From product DB'} for n in sol_names[:20]],
    }
    exp_id = create_experiment(user_id, exp_data)

    for idx, st in enumerate(stages, 1):
        cond = st.get('conditions') or {}
        if not isinstance(cond, dict):
            cond = {}
        temp_min = cond.get('temperature_c_min')
        temp_max = cond.get('temperature_c_max')
        temperature_c = None
        if temp_min is not None and temp_max is not None:
            temperature_c = (temp_min + temp_max) / 2.0
        elif temp_min is not None:
            temperature_c = temp_min
        ph_min = cond.get('ph_min')
        ph_max = cond.get('ph_max')
        ph_value = None
        if ph_min is not None and ph_max is not None:
            ph_value = (ph_min + ph_max) / 2.0
        elif ph_min is not None:
            ph_value = ph_min
        extra_notes = []
        if cond.get('pressure_bar') is not None:
            extra_notes.append(f"Pressure: {cond['pressure_bar']} bar")
        if cond.get('mixing_speed_rpm') is not None:
            extra_notes.append(f"RPM: {cond['mixing_speed_rpm']}")
        if cond.get('hold_time_min') is not None:
            extra_notes.append(f"Hold time: {cond['hold_time_min']} min")
        if cond.get('atmosphere'):
            extra_notes.append(f"Atmosphere: {cond['atmosphere']}")
        equipment = st.get('equipment') or []
        if equipment:
            extra_notes.append(f"Equipment: {', '.join(str(e) for e in equipment[:6])}")
        notes = st.get('notes') or ''
        if extra_notes:
            notes += (' | ' if notes else '') + '; '.join(extra_notes)
        stage_data = {
            'experiment_id': exp_id,
            'stage_no': int(st.get('stage_no') or idx),
            'stage_name': st.get('stage_name') or f'Stage {idx}',
            'temperature_c': temperature_c,
            'ph_value': ph_value,
            'pressure_bar': cond.get('pressure_bar'),
            'reaction_time_minutes': cond.get('hold_time_min'),
            'mixing_speed_rpm': cond.get('mixing_speed_rpm'),
            'atmosphere': cond.get('atmosphere', ''),
            'equipment_code': equipment[0] if equipment else '',
            'solvent': '; '.join(format_material(m) for m in st.get('solvents', [])[:4]),
            'catalyst': ', '.join(str(c) for c in st.get('catalysts', [])[:3]),
            'rm_description': '; '.join(format_material(m) for m in st.get('raw_materials', [])[:12]),
            'notes': notes,
            'material_balance': {
                'conditions': cond,
                'equipment': equipment,
                'raw_materials': st.get('raw_materials', []),
                'solvents': st.get('solvents', []),
            },
        }
        stage_id = add_stage(user_id, exp_id, stage_data)
        for r in st.get('raw_materials') or []:
            name = r.get('name') if isinstance(r, dict) else str(r)
            if not name:
                continue
            add_stage_raw_material(user_id, stage_id, {
                'experiment_id': exp_id,
                'rm_name': name,
                'planned_qty': r.get('quantity') if isinstance(r, dict) else 0,
                'planned_unit': r.get('unit') if isinstance(r, dict) else 'kg',
                'actual_qty_used': 0,
                'is_override': 0,
                'notes': 'Auto-imported from product DB',
            })

    for imp in impurities:
        notes = []
        if imp.get('likely_source'):
            notes.append(f"Likely source: {imp['likely_source']}")
        if imp.get('qc_method'):
            notes.append(f"QC: {imp['qc_method']}")
        if imp.get('acceptance_or_alert'):
            notes.append(f"Limit: {imp['acceptance_or_alert']}")
        add_impurity(user_id, exp_id, {
            'stage_id': None,
            'impurity_name': imp.get('impurity_name') or 'Unknown impurity',
            'rrt': None,
            'value_pct': imp.get('confidence'),
            'impurity_type': imp.get('control_strategy') or 'unknown',
            'notes': ('; '.join(notes) if notes else 'From product DB'),
        })

    return {'experiment_id': exp_id, 'product_name': canonical, 'source': 'product_db', 'stages_created': len(stages)}


def create_experiment_from_product(user_id: int | None, product_name: str) -> dict[str, Any]:
    """Create an experiment from product chemistry if available, else BMR corpus, else blank."""
    canonical = normalize_product_name(product_name)
    result = _seed_experiment_from_product_db(user_id, canonical)
    if result:
        return result
    corpus = search_corpus(canonical, limit=1)
    if corpus.get('hits'):
        from .enterprise_pharma_core import create_rd_experiment_from_product as _legacy_create_from_bmr
        return _legacy_create_from_bmr(user_id, canonical, source='bmr')
    # No product knowledge: blank experiment
    exp_id = create_experiment(user_id, {
        'product_name': canonical,
        'route_name': 'Manual development',
        'notes': 'Created manually; no matching product or BMR corpus entry found.',
        'status': 'planned',
        'raw_materials': [],
        'solvents': [],
    })
    return {'experiment_id': exp_id, 'product_name': canonical, 'source': 'blank', 'stages_created': 0}


def inspect_product(product_name: str) -> dict[str, Any]:
    """Return product context and suggested next actions without calling AI."""
    canonical = normalize_product_name(product_name)
    corpus = search_corpus(canonical, limit=12)
    chemistry = analyze_product_chemistry(canonical, None)
    has_corpus = bool(corpus.get('hits'))
    next_actions = []
    if has_corpus:
        next_actions.append({'action': 'run_bmr_route', 'label': f'Run BMR route for {canonical}'})
    next_actions.append({'action': 'create_blank_experiment', 'label': f'Create blank experiment for {canonical}'})
    next_actions.append({'action': 'search_patents', 'label': f'Search patents for {canonical}'})
    next_actions.append({'action': 'modify_route', 'label': f'Suggest/verify routes for {canonical}'})
    return {
        'product_name': canonical,
        'corpus': corpus,
        'chemistry': chemistry,
        'next_actions': next_actions,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Templates
# ═══════════════════════════════════════════════════════════════════════════════


def list_templates(product_name: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    if product_name:
        return db.query('SELECT * FROM rd_experiment_templates WHERE product_name=? ORDER BY updated_at DESC LIMIT ?', (normalize_product_name(product_name), limit))
    return db.query('SELECT * FROM rd_experiment_templates ORDER BY updated_at DESC LIMIT ?', (limit,))


def get_template(template_id: int) -> dict[str, Any] | None:
    return db.one('SELECT * FROM rd_experiment_templates WHERE id=?', (template_id,))


def create_template(user_id: int | None, data: dict[str, Any]) -> int:
    rid = db.execute(
        '''INSERT INTO rd_experiment_templates(
            template_name, product_name, route_name, description, stages_json, raw_materials_json,
            solvents_json, tests_json, target_conditions_json, ai_generated, source_experiment_id, created_by
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
        (
            data.get('template_name', 'Template'),
            normalize_product_name(data.get('product_name')),
            data.get('route_name', ''),
            data.get('description', ''),
            json.dumps(data.get('stages', [])),
            json.dumps(data.get('raw_materials', [])),
            json.dumps(data.get('solvents', [])),
            json.dumps(data.get('tests', [])),
            json.dumps(data.get('target_conditions', {})),
            1 if data.get('ai_generated') else 0,
            data.get('source_experiment_id'),
            user_id,
        ),
    )
    db.audit(user_id, 'create', 'rd_experiment_template', rid, data)
    return rid


def instantiate_template(user_id: int | None, template_id: int, overrides: dict[str, Any]) -> int:
    tmpl = get_template(template_id)
    if not tmpl:
        raise ValueError('Template not found')
    exp_data = {
        'product_name': overrides.get('product_name') or tmpl['product_name'] or 'Untitled',
        'route_name': overrides.get('route_name') or tmpl['route_name'] or '',
        'ksm': overrides.get('ksm', ''),
        'solvent': overrides.get('solvent', ''),
        'catalyst': overrides.get('catalyst', ''),
        'notes': overrides.get('notes', f"Created from template: {tmpl['template_name']}"),
        'status': 'planned',
        'raw_materials': json.loads(tmpl['raw_materials_json'] or '[]'),
        'solvents': json.loads(tmpl['solvents_json'] or '[]'),
    }
    exp_id = create_experiment(user_id, exp_data)
    for st in json.loads(tmpl['stages_json'] or '[]'):
        st['experiment_id'] = exp_id
        add_stage(user_id, exp_id, st)
    db.audit(user_id, 'create', 'rd_experiment_from_template', exp_id, {'template_id': template_id})
    return exp_id


def save_experiment_as_template(user_id: int | None, exp_id: int, data: dict[str, Any]) -> int:
    detail = _build_experiment_detail(exp_id)
    if not detail.get('experiment'):
        raise ValueError('Experiment not found')
    exp = detail['experiment']
    template_data = {
        'template_name': data.get('template_name', f"Template from {exp['product_name']}"),
        'product_name': data.get('product_name') or exp['product_name'],
        'route_name': data.get('route_name') or exp['route_name'],
        'description': data.get('description', f"Template saved from experiment #{exp_id}"),
        'stages': [{
            'stage_no': s['stage_no'],
            'stage_name': s['stage_name'],
            'temperature_c': s['temperature_c'],
            'ph_value': s['ph_value'],
            'pressure_bar': s.get('pressure_bar'),
            'reaction_time_minutes': s.get('reaction_time_minutes'),
            'mixing_speed_rpm': s.get('mixing_speed_rpm'),
            'atmosphere': s.get('atmosphere'),
            'solvent': s['solvent'],
            'catalyst': s['catalyst'],
            'rm_description': s['rm_description'],
            'theoretical_yield_pct': s['theoretical_yield_pct'],
            'input_qty': s['input_qty'],
            'output_qty': s['output_qty'],
            'purity_pct': s['purity_pct'],
            'notes': s['notes'],
        } for s in detail['stages']],
        'raw_materials': [{
            'name': r['name'],
            'quantity': r['quantity'],
            'unit': r['unit'],
            'unit_type': r['unit_type'],
            'molecular_weight_g_mol': r['molecular_weight_g_mol'],
            'notes': r['notes'],
        } for r in detail['raw_materials']],
        'solvents': [{
            'name': s['name'],
            'quantity_ml': s['quantity_ml'],
            'notes': s['notes'],
        } for s in detail['solvents']],
        'tests': data.get('tests', []),
        'target_conditions': data.get('target_conditions', {}),
        'source_experiment_id': exp_id,
    }
    return create_template(user_id, template_data)


def delete_template(user_id: int | None, template_id: int) -> None:
    db.execute('DELETE FROM rd_experiment_templates WHERE id=?', (template_id,))
    db.audit(user_id, 'delete', 'rd_experiment_template', template_id)


# ═══════════════════════════════════════════════════════════════════════════════
# Numeric comparison
# ═══════════════════════════════════════════════════════════════════════════════


def compare_experiments_numeric(exp_ids: list[int]) -> dict[str, Any]:
    """Pure numeric comparison of experiments; no AI."""
    if len(exp_ids) < 2:
        raise ValueError('Select at least 2 experiments')
    rows = []
    for eid in exp_ids:
        detail = _build_experiment_detail(eid)
        exp = detail.get('experiment')
        if not exp:
            continue
        stages = detail.get('stages', [])
        yields = [s['actual_yield_pct'] for s in stages if s.get('actual_yield_pct') is not None]
        purities = [s['purity_pct'] for s in stages if s.get('purity_pct') is not None]
        impurities = detail.get('impurities', [])
        total_impurity = sum(i['value_pct'] or 0 for i in impurities)
        rows.append({
            'experiment_id': eid,
            'product_name': exp['product_name'],
            'status': exp['status'],
            'stage_count': len(stages),
            'overall_yield_pct': round(sum(yields) / len(yields), 2) if yields else None,
            'avg_purity_pct': round(sum(purities) / len(purities), 2) if purities else None,
            'total_impurity_pct': round(total_impurity, 3),
            'material_balance': detail.get('material_balance', {}),
        })
    best_yield = max((r for r in rows if r['overall_yield_pct'] is not None), key=lambda x: x['overall_yield_pct'], default=None)
    best_purity = max((r for r in rows if r['avg_purity_pct'] is not None), key=lambda x: x['avg_purity_pct'], default=None)
    return {
        'count': len(rows),
        'experiment_ids': exp_ids,
        'by_experiment': rows,
        'best_by_yield': best_yield['experiment_id'] if best_yield else None,
        'best_by_purity': best_purity['experiment_id'] if best_purity else None,
    }


def save_comparison(user_id: int | None, exp_ids: list[int], numeric: dict[str, Any], recommendations: str = '', best_id: int | None = None) -> int:
    rid = db.execute(
        'INSERT INTO rd_experiment_comparisons(experiment_ids_json, comparison_summary, ai_recommendations, best_experiment_id, created_by) VALUES (?, ?, ?, ?, ?)',
        (json.dumps(exp_ids), json.dumps(numeric, ensure_ascii=False, default=str), recommendations, best_id, user_id),
    )
    db.audit(user_id, 'create', 'rd_experiment_comparison', rid, {'exp_ids': exp_ids})
    return rid
