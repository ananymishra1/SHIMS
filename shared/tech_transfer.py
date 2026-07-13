"""Tech Transfer — scale-up planning from finalized R&D route to plant equipment.

No-LLM-first: all scale math is deterministic via shared.tt_scale. LLM may be used
later as an explicit opt-in for narrative summaries.
"""
from __future__ import annotations

import json
import time
from typing import Any, Optional

from .database import db
from .enterprise_pharma_core import ensure_pharma_schema
from .rd_lab import _build_experiment_detail
from .tt_scale import scale_batch


# ═══════════════════════════════════════════════════════════════════════════════
# Schema
# ═══════════════════════════════════════════════════════════════════════════════


def ensure_tech_transfer_schema() -> None:
    """Idempotent schema for Tech Transfer projects and trials."""
    ensure_pharma_schema()
    db.ensure_columns('tech_transfer_projects', {
        'experiment_id': 'INTEGER NOT NULL',
        'product_name': 'TEXT NOT NULL',
        'route_name': 'TEXT',
        'target_batch_kg': 'REAL NOT NULL',
        'vessel_id': 'INTEGER',
        'status': "TEXT DEFAULT 'draft'",
        'notes': 'TEXT',
        'created_by': 'INTEGER',
        'created_at': 'TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP',
    })
    db.ensure_columns('tt_trials', {
        'tt_project_id': 'INTEGER NOT NULL',
        'trial_name': 'TEXT NOT NULL',
        'lab_batch_kg': 'REAL',
        'target_batch_kg': 'REAL',
        'vessel_json': "TEXT DEFAULT '{}'",
        'scale_result_json': "TEXT DEFAULT '{}'",
        'notes': 'TEXT',
        'created_by': 'INTEGER',
        'created_at': 'TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP',
    })


# ═══════════════════════════════════════════════════════════════════════════════
# Internal helpers
# ═══════════════════════════════════════════════════════════════════════════════


def _now() -> str:
    return time.strftime('%Y-%m-%d %H:%M:%S')


def _vessel_by_id(vessel_id: int | None) -> dict[str, Any] | None:
    if vessel_id is None:
        return None
    row = db.one('SELECT * FROM equipment_master WHERE id=?', (vessel_id,))
    if not row:
        return None
    return {
        'id': row['id'],
        'equipment_code': row['equipment_code'],
        'equipment_name': row['equipment_name'],
        'equipment_type': row.get('equipment_type'),
        'capacity': row.get('capacity'),
        'unit': row.get('unit') or 'L',
        'location': row.get('location'),
        'status': row.get('status'),
        'cleaning_status': row.get('cleaning_status'),
    }


def _vessel_to_spec(vessel: dict[str, Any] | None) -> dict[str, Any]:
    """Map equipment_master row to tt_scale vessel spec."""
    if not vessel:
        return {}
    cap = vessel.get('capacity') or 0
    unit = (vessel.get('unit') or 'L').lower()
    working_volume_L = cap if unit in ('l', 'litre', 'liter', 'litres', 'liters') else cap * 1000
    # Estimate diameter / height from capacity if not stored.
    diameter_m = None
    height_m = None
    if working_volume_L > 0:
        # Assume cylinder with H = 1.5 * D for a generic vessel.
        vol_m3 = working_volume_L / 1000.0
        # vol = pi * r^2 * h, h = 1.5 * d = 3 * r  =>  vol = 3 * pi * r^3
        r = (vol_m3 / (3.0 * 3.141592653589793)) ** (1.0 / 3.0)
        diameter_m = round(r * 2, 3)
        height_m = round(r * 3, 3)
    return {
        'working_volume_L': working_volume_L,
        'total_volume_L': round(working_volume_L / 0.8, 1) if working_volume_L else None,
        'diameter_m': diameter_m,
        'height_m': height_m,
    }


def _experiment_to_lab_descriptor(exp_id: int, detail: dict[str, Any]) -> dict[str, Any]:
    """Build a lab descriptor for tt_scale from an experiment detail dict."""
    exp = detail.get('experiment') or {}
    rms = detail.get('raw_materials') or []
    stages = detail.get('stages') or []
    # Use actual logged input/output to infer a lab batch size.
    lab_batch_kg = 0.0
    for s in stages:
        out = s.get('output_qty')
        inp = s.get('input_qty')
        v = out if out is not None else inp
        if v:
            lab_batch_kg = max(lab_batch_kg, float(v))
    if not lab_batch_kg:
        # Fallback: sum of RM quantities (mass only).
        lab_batch_kg = sum(
            float(r.get('quantity') or 0)
            for r in rms
            if (r.get('unit_type') or 'mass') == 'mass'
        )
    if not lab_batch_kg:
        lab_batch_kg = 1.0

    materials = []
    for r in rms:
        q = float(r.get('quantity') or 0)
        if q > 0:
            materials.append({'name': r.get('name'), 'charge_kg': q})

    # Last stage process hints.
    last = stages[-1] if stages else {}
    return {
        'batch_kg': lab_batch_kg,
        'density_kg_L': 1.0,
        'materials': materials,
        'impeller_diameter_m': None,
        'impeller_rpm': last.get('mixing_speed_rpm'),
        'vessel_diameter_m': 0.1,
        'vessel_height_m': 0.15,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# CRUD
# ═══════════════════════════════════════════════════════════════════════════════


def create_tt_project(experiment_id: int, payload: dict[str, Any], created_by: int | None = None) -> dict[str, Any]:
    """Create a TT project from a finalized R&D experiment."""
    ensure_tech_transfer_schema()
    target_batch_kg = payload.get('target_batch_kg')
    try:
        target_batch_kg = float(target_batch_kg)
    except (TypeError, ValueError):
        return {'ok': False, 'error': 'target_batch_kg required'}
    if target_batch_kg <= 0:
        return {'ok': False, 'error': 'target_batch_kg must be > 0'}

    detail = _build_experiment_detail(experiment_id)
    exp = detail.get('experiment')
    if not exp:
        return {'ok': False, 'error': 'experiment not found'}

    vessel_id = payload.get('vessel_id')
    vessel = _vessel_by_id(vessel_id)
    vessel_spec = _vessel_to_spec(vessel)

    lab = _experiment_to_lab_descriptor(experiment_id, detail)
    scale_result = scale_batch(lab, target_batch_kg, vessel_spec)

    project_id = db.execute(
        """INSERT INTO tech_transfer_projects
           (experiment_id, product_name, route_name, target_batch_kg, vessel_id, status, notes, created_by, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            experiment_id,
            exp.get('product_name', ''),
            exp.get('route_name', ''),
            target_batch_kg,
            vessel_id,
            payload.get('status') or 'draft',
            payload.get('notes', ''),
            created_by,
            _now(),
        ),
    ).lastrowid

    # Store the initial scale result as trial 0.
    add_trial(project_id, {
        'trial_name': 'Initial scale assessment',
        'lab_batch_kg': lab['batch_kg'],
        'target_batch_kg': target_batch_kg,
        'vessel_json': vessel,
        'scale_result': scale_result,
        'notes': 'Auto-generated from experiment creation.',
    }, created_by=created_by)

    return {'ok': True, 'project_id': project_id, 'scale_result': scale_result}


def get_tt_project(project_id: int) -> dict[str, Any]:
    """Fetch a TT project with experiment snapshot and trials."""
    ensure_tech_transfer_schema()
    row = db.one('SELECT * FROM tech_transfer_projects WHERE id=?', (project_id,))
    if not row:
        return {'ok': False, 'error': 'project not found'}
    project = dict(row)
    vessel = _vessel_by_id(project.get('vessel_id'))
    trials = [
        {**dict(t), 'vessel_json': _safe_json(t['vessel_json']), 'scale_result_json': _safe_json(t['scale_result_json'])}
        for t in db.query('SELECT * FROM tt_trials WHERE tt_project_id=? ORDER BY created_at', (project_id,))
    ]
    detail = _build_experiment_detail(project['experiment_id'])
    return {
        'ok': True,
        'project': project,
        'vessel': vessel,
        'trials': trials,
        'experiment_snapshot': detail,
    }


def _safe_json(raw: Any) -> Any:
    try:
        return json.loads(raw) if raw else {}
    except Exception:
        return {}


def list_tt_projects(status: str | None = None, limit: int = 100) -> dict[str, Any]:
    ensure_tech_transfer_schema()
    params: list[Any] = []
    where = ''
    if status:
        where = 'WHERE status=?'
        params.append(status)
    rows = db.query(
        f'SELECT * FROM tech_transfer_projects {where} ORDER BY created_at DESC LIMIT ?',
        params + [limit],
    )
    return {'ok': True, 'projects': [dict(r) for r in rows], 'count': len(rows)}


def update_project_status(project_id: int, status: str) -> dict[str, Any]:
    ensure_tech_transfer_schema()
    db.execute('UPDATE tech_transfer_projects SET status=? WHERE id=?', (status, project_id))
    return {'ok': True, 'project_id': project_id, 'status': status}


def add_trial(project_id: int, payload: dict[str, Any], created_by: int | None = None) -> dict[str, Any]:
    """Add a scale trial to a TT project."""
    ensure_tech_transfer_schema()
    project = db.one('SELECT * FROM tech_transfer_projects WHERE id=?', (project_id,))
    if not project:
        return {'ok': False, 'error': 'project not found'}

    lab_batch_kg = payload.get('lab_batch_kg') or project['target_batch_kg']
    target_batch_kg = payload.get('target_batch_kg') or project['target_batch_kg']
    try:
        lab_batch_kg = float(lab_batch_kg)
        target_batch_kg = float(target_batch_kg)
    except (TypeError, ValueError):
        return {'ok': False, 'error': 'lab_batch_kg and target_batch_kg must be numeric'}

    vessel_json = payload.get('vessel_json') or _vessel_by_id(project['vessel_id'])
    vessel_spec = _vessel_to_spec(vessel_json)

    # Minimal lab descriptor from trial inputs.
    lab = {
        'batch_kg': lab_batch_kg,
        'density_kg_L': 1.0,
        'materials': [],
        'impeller_rpm': None,
        'vessel_diameter_m': 0.1,
        'vessel_height_m': 0.15,
    }
    scale_result = payload.get('scale_result') or scale_batch(lab, target_batch_kg, vessel_spec)

    trial_id = db.execute(
        """INSERT INTO tt_trials
           (tt_project_id, trial_name, lab_batch_kg, target_batch_kg, vessel_json, scale_result_json, notes, created_by, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            project_id,
            payload.get('trial_name', 'Trial'),
            lab_batch_kg,
            target_batch_kg,
            json.dumps(vessel_json, default=str) if vessel_json else '{}',
            json.dumps(scale_result, default=str),
            payload.get('notes', ''),
            created_by,
            _now(),
        ),
    ).lastrowid

    return {'ok': True, 'trial_id': trial_id, 'scale_result': scale_result}
