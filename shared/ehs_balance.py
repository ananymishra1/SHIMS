"""Environmental (EHS) engine — material balance, emissions, CETP load, recovery paths.

Uses real Indian pharma EC conditions as a representative guardrail set. When the
actual JK Lifecare EC is available, replace or append conditions via the admin
endpoint / ingest function.
"""
from __future__ import annotations

import re
from typing import Any, Optional

from .database import db


# ═══════════════════════════════════════════════════════════════════════════════
# Seed EC conditions (representative Indian pharma EC — replace with JK Lifecare EC when available)
# ═══════════════════════════════════════════════════════════════════════════════

_DEFAULT_EC_CONDITIONS: list[dict[str, Any]] = [
    {'category': 'water', 'parameter': 'total_water_requirement', 'limit': 227.0, 'unit': 'KLD', 'scope': 'total'},
    {'category': 'water', 'parameter': 'fresh_water_requirement', 'limit': 189.4, 'unit': 'KLD', 'scope': 'fresh'},
    {'category': 'water', 'parameter': 'industrial_effluent_generation', 'limit': 89.0, 'unit': 'KLD', 'scope': 'total'},
    {'category': 'water', 'parameter': 'high_cod_tds_effluent', 'limit': 14.0, 'unit': 'KLD', 'scope': 'high_cod'},
    {'category': 'water', 'parameter': 'low_cod_effluent', 'limit': 75.0, 'unit': 'KLD', 'scope': 'low_cod'},
    {'category': 'water', 'parameter': 'domestic_wastewater', 'limit': 9.0, 'unit': 'KLD', 'scope': 'domestic'},
    {'category': 'air', 'parameter': 'voc_workzone_monitoring', 'limit': 1.0, 'unit': 'quarterly', 'scope': 'fugitive'},
    {'category': 'air', 'parameter': 'solvent_recovery', 'limit': 95.0, 'unit': '%', 'scope': 'recovery'},
    {'category': 'waste', 'parameter': 'incinerable_waste', 'limit': None, 'unit': 'route', 'scope': 'chwif_tsdf'},
    {'category': 'product_mix', 'parameter': 'max_products', 'limit': 20.0, 'unit': 'count', 'scope': 'plant'},
]


# ═══════════════════════════════════════════════════════════════════════════════
# Schema
# ═══════════════════════════════════════════════════════════════════════════════


def ensure_ehs_schema() -> None:
    db.execute(
        """CREATE TABLE IF NOT EXISTS ehs_ec_conditions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL,
            parameter TEXT NOT NULL,
            limit_value REAL,
            unit TEXT,
            scope TEXT,
            source TEXT DEFAULT 'seed',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    db.ensure_columns('ehs_batch_balances', {
        'product_name': 'TEXT NOT NULL',
        'batch_no': 'TEXT',
        'batch_size_kg': 'REAL',
        'input_kg': 'REAL',
        'output_kg': 'REAL',
        'waste_kg': 'REAL',
        'recovery_kg': 'REAL',
        'emission_kg': 'REAL',
        'effluent_kg': 'REAL',
        'balance_json': "TEXT DEFAULT '{}'",
        'created_at': 'TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP',
    })
    _seed_ec_conditions()


def _parse_limit(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().lower()
    if s in ('', 'n/a', 'null', 'none', 'zero liquid discharge', 'zero', 'as per norms', 'as per mppcb/cpcb norms'):
        # Special-case explicit zero strings to 0.0 so guardrails fire correctly.
        if 'zero' in s:
            return 0.0
        return None
    # Strip non-numeric trailing characters and extract first number.
    import re
    m = re.search(r'[+-]?\d+(?:\.\d+)?', s)
    return float(m.group(0)) if m else None


def _seed_ec_conditions() -> None:
    db.execute(
        """CREATE TABLE IF NOT EXISTS ehs_ec_conditions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL,
            parameter TEXT NOT NULL,
            limit_value REAL,
            unit TEXT,
            scope TEXT,
            source TEXT DEFAULT 'seed',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    existing = db.one('SELECT COUNT(*) as c FROM ehs_ec_conditions')
    if existing and existing['c'] > 0:
        return
    # Prefer real JK Lifecare EC extraction if present.
    import json, pathlib
    ec_file = pathlib.Path('storage/enterprise/jklc_ec_conditions.json')
    conds: list[dict[str, Any]] = []
    source = 'seed'
    if ec_file.exists():
        try:
            conds = json.loads(ec_file.read_text(encoding='utf-8')).get('conditions', [])
            source = 'jklc_ec_pdf'
        except Exception:
            conds = []
    if not conds:
        conds = _DEFAULT_EC_CONDITIONS
        source = 'seed'
    for cond in conds:
        db.execute(
            'INSERT INTO ehs_ec_conditions (category, parameter, limit_value, unit, scope, source) VALUES (?, ?, ?, ?, ?, ?)',
            (cond['category'], cond['parameter'], _parse_limit(cond.get('limit')), cond.get('unit'), cond.get('scope'), source),
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Material balance engine
# ═══════════════════════════════════════════════════════════════════════════════


def _f(v: Any) -> Optional[float]:
    try:
        return float(v) if v is not None and v != '' else None
    except Exception:
        return None


def batch_material_balance(
    product_name: str,
    batch_size_kg: float,
    raw_materials: list[dict[str, Any]],
    outputs: list[dict[str, Any]] | None = None,
    recovery_paths: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Compute a per-batch material balance and environmental loads."""
    outputs = outputs or []
    recovery_paths = recovery_paths or []

    input_kg = sum((_f(r.get('quantity_kg')) or 0) for r in raw_materials)
    output_kg = sum((_f(o.get('quantity_kg')) or 0) for o in outputs if o.get('type') == 'product')
    waste_kg = sum((_f(o.get('quantity_kg')) or 0) for o in outputs if o.get('type') == 'waste')
    recovery_kg = sum((_f(r.get('quantity_kg')) or 0) for r in recovery_paths)

    # Estimate emissions: 0.5% of volatile inputs + 2% of solvent inputs.
    emission_kg = 0.0
    for r in raw_materials:
        name = str(r.get('name') or '').lower()
        qty = _f(r.get('quantity_kg')) or 0
        if any(s in name for s in ('solvent', 'methanol', 'ethanol', 'acetone', 'thf', 'toluene', 'ethyl acetate')):
            emission_kg += qty * 0.02
        elif any(s in name for s in ('hcl', 'ammonia', 'voc', 'vapor')):
            emission_kg += qty * 0.005

    # Estimate effluent: 0.8 * (input - product - waste - recovery), capped at input.
    effluent_kg = max(0, input_kg - output_kg - waste_kg - recovery_kg)
    unaccounted_kg = input_kg - output_kg - waste_kg - recovery_kg - emission_kg - effluent_kg

    # Scrubber load: assume 80% of emissions captured.
    scrubber_load_kg = emission_kg * 0.8

    # CETP load: effluent.
    cetp_load_kg = effluent_kg

    # Recovery evaluation.
    recoverable_names = {'solvent', 'methanol', 'ethanol', 'acetone', 'thf', 'toluene', 'ethyl acetate', 'ipa', 'isopropanol'}
    recovery_eval: list[dict[str, Any]] = []
    for r in raw_materials:
        name = str(r.get('name') or '').lower()
        qty = _f(r.get('quantity_kg')) or 0
        if any(s in name for s in recoverable_names):
            recovery_eval.append({
                'material': r.get('name'),
                'input_kg': qty,
                'recoverable_estimate_kg': round(qq := qty * 0.9, 2),
                'reuse_value': 'high' if qq > 10 else 'medium',
            })

    return {
        'ok': True,
        'product_name': product_name,
        'batch_size_kg': batch_size_kg,
        'input_kg': round(input_kg, 4),
        'output_kg': round(output_kg, 4),
        'waste_kg': round(waste_kg, 4),
        'recovery_kg': round(recovery_kg, 4),
        'emission_kg': round(emission_kg, 4),
        'effluent_kg': round(effluent_kg, 4),
        'scrubber_load_kg': round(scrubber_load_kg, 4),
        'cetp_load_kg': round(cetp_load_kg, 4),
        'unaccounted_kg': round(unaccounted_kg, 4),
        'mass_closure_pct': round((output_kg + waste_kg + recovery_kg + emission_kg + effluent_kg) / input_kg * 100, 2) if input_kg else 0,
        'recovery_evaluation': recovery_eval,
        'engine': 'ehs_balance/deterministic-v1',
        'disclaimer': 'Heuristic environmental balance. Confirm against actual effluent/emission measurements and site EC conditions.',
    }


# ═══════════════════════════════════════════════════════════════════════════════
# EC limit guardrails
# ═══════════════════════════════════════════════════════════════════════════════


def ec_limit_check(
    product_mix_count: int | None = None,
    total_effluent_kld: float | None = None,
    fresh_water_kld: float | None = None,
    high_cod_kld: float | None = None,
) -> dict[str, Any]:
    """Check plant-level totals against the loaded EC conditions (JK Lifecare EC if present)."""
    ensure_ehs_schema()
    conditions = db.query('SELECT * FROM ehs_ec_conditions')
    flags: list[dict[str, Any]] = []
    statuses: dict[str, str] = {}
    values: dict[str, Any] = {}
    limits: dict[str, Any] = {}

    def _norm(p: str) -> str:
        return re.sub(r'[^a-z0-9]+', '_', p.lower()).strip('_')

    # Map EC parameter names to the caller-supplied values.
    param_inputs: dict[str, Any] = {}
    for c in conditions:
        param = c['parameter']
        norm = _norm(param)
        if 'max_product' in norm or 'product_mix' in norm:
            param_inputs[param] = product_mix_count
        elif 'effluent_discharge' in norm or ('effluent' in norm and 'generation' in norm):
            param_inputs[param] = total_effluent_kld
        elif 'fresh_water' in norm or 'water_requirement' in norm or 'total_water' in norm:
            param_inputs[param] = fresh_water_kld
        elif 'high_cod' in norm or 'high_c_o_d' in norm:
            param_inputs[param] = high_cod_kld

    # Deduplicate mapped inputs by keeping the strictest numeric limit per parameter.
    # This handles duplicate EC rows (e.g., "Fresh Water Source" vs "Total fresh water requirement").
    strictest: dict[str, dict[str, Any]] = {}
    for c in conditions:
        limit = c['limit_value']
        param = c['parameter']
        actual = param_inputs.get(param)
        if actual is None:
            continue
        if limit is None:
            continue
        existing = strictest.get(param)
        if existing is None or limit < existing['limit']:
            strictest[param] = dict(c)

    for c in strictest.values():
        limit = c['limit_value']
        param = c['parameter']
        actual = param_inputs[param]
        values[param] = actual
        limits[param] = limit
        unit = c.get('unit') or ''
        if actual > limit:
            severity = 'critical' if actual > limit * 1.1 or limit == 0 else 'warn'
            statuses[param] = 'over'
            flags.append({
                'parameter': param,
                'limit': limit,
                'actual': actual,
                'unit': unit,
                'severity': severity,
                'message': f"{param} ({actual} {unit}) exceeds EC limit ({limit} {unit})",
            })
        elif limit == 0:
            # Zero-limit conditions (e.g., Zero Liquid Discharge) are satisfied only at exactly zero.
            statuses[param] = 'ok' if actual == 0 else 'over'
        elif limit > 0 and actual > limit * 0.85:
            statuses[param] = 'at_limit'
        else:
            statuses[param] = 'ok'

    return {
        'ok': True,
        'flags': flags,
        'within_limits': len(flags) == 0,
        'conditions': limits,
        'values': values,
        'statuses': statuses,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# "Is this waste someone's RM?" matcher
# ═══════════════════════════════════════════════════════════════════════════════


def waste_rm_match(waste_name: str) -> list[dict[str, Any]]:
    """Suggest which internal/external buyers might use a waste stream as RM."""
    name = (waste_name or '').lower()
    matches: list[dict[str, Any]] = []
    # Internal inventory.
    try:
        rows = db.query('SELECT material_name, item_code, quantity, unit FROM inventory_items WHERE LOWER(material_name) LIKE ?', (f'%{name}%',))
        for r in rows:
            matches.append({'type': 'internal_inventory', 'material': r['material_name'], 'stock': f"{r['quantity']} {r['unit']}"})
    except Exception:
        pass
    # Route-stage hints.
    try:
        from .product_chemistry import list_route_stages
        for product in _common_products():
            for stage in list_route_stages(product):
                for rm in stage.get('raw_materials') or []:
                    rm_name = (rm.get('name') if isinstance(rm, dict) else str(rm)).lower()
                    if any(token in rm_name for token in name.split()) or any(token in name for token in rm_name.split()):
                        matches.append({'type': 'process_input', 'product': product, 'material': rm_name})
    except Exception:
        pass
    return matches[:10]


def _common_products() -> list[str]:
    try:
        rows = db.query('SELECT DISTINCT product_name FROM enterprise_products WHERE product_name IS NOT NULL LIMIT 20')
        return [r['product_name'] for r in rows]
    except Exception:
        return []


# ═══════════════════════════════════════════════════════════════════════════════
# Admin / ingest
# ═══════════════════════════════════════════════════════════════════════════════


def add_ec_condition(category: str, parameter: str, limit_value: float | None, unit: str, scope: str, source: str = 'manual') -> dict[str, Any]:
    ensure_ehs_schema()
    db.execute(
        'INSERT INTO ehs_ec_conditions (category, parameter, limit_value, unit, scope, source) VALUES (?, ?, ?, ?, ?, ?)',
        (category, parameter, limit_value, unit, scope, source),
    )
    return {'ok': True}


def list_ec_conditions() -> list[dict[str, Any]]:
    ensure_ehs_schema()
    return [dict(r) for r in db.query('SELECT * FROM ehs_ec_conditions ORDER BY category, parameter')]
