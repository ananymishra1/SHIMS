from __future__ import annotations

import json
from typing import Any, Dict, List


def rd_suggestions(payload: Dict[str, Any]) -> List[str]:
    text = json.dumps(payload, default=str).lower()
    out = []
    if any(x in text for x in ['reaction', 'synthesis', 'intermediate', 'organic']):
        out.append('Check stoichiometry, impurity pathways, reaction exotherm, alternate reagent route, and patent landscape before scale-up.')
    if any(x in text for x in ['solvent', 'ethanol', 'ipa', 'methanol', 'acetone']):
        out.append('Record solvent grade, water content, recovery method, residual-solvent risk, and safe handling notes.')
    if any(x in text for x in ['heat', 'temperature', 'reflux', 'cooling']):
        out.append('Add temperature ramp, hold time, cooling profile, and deviation triggers to the experiment log.')
    if not out:
        out.append('Define one clear variable, a control, acceptance criteria, safety notes, and observation frequency before starting.')
    out.append('After the run, compare expected vs actual result and let Omni propose the next experiment in the pipeline.')
    return out


def validate_coa(schema: Dict[str, Any], values: Dict[str, Any]) -> Dict[str, Any]:
    missing = []
    warnings = []
    for f in schema.get('fields', []):
        name = f.get('name')
        label = f.get('label', name)
        if f.get('required', True) and values.get(name) in [None, '']:
            missing.append(label)
        if f.get('spec') and values.get(name) not in [None, '']:
            warnings.append(f"Verify {label} value {values.get(name)!r} against specification {f.get('spec')!r}.")
    return {'status': 'complete' if not missing else 'draft', 'missing': missing, 'warnings': warnings}


def warehouse_alerts(items: List[Dict[str, Any]]) -> List[str]:
    alerts = []
    for item in items:
        if float(item.get('stock_qty') or 0) <= float(item.get('reorder_level') or 0):
            alerts.append(f"Reorder {item.get('material_name')} ({item.get('stock_qty')} {item.get('uom')} available; reorder level {item.get('reorder_level')}).")
    return alerts or ['No reorder alert from current stock list.']


def production_suggestions(batch: Dict[str, Any]) -> List[str]:
    out = []
    if batch.get('blockers'):
        out.append('Escalate blockers to Executive, Warehouse, Procurement, and QC if relevant.')
    if batch.get('qc_status') in ['pending', 'hold', 'failed']:
        out.append('Do not release batch before QC status and COA approval are complete.')
    if batch.get('status') == 'planned':
        out.append('Before start, verify material availability, line clearance, manpower, equipment readiness, and QC sampling plan.')
    return out or ['Batch can continue stage-wise logging.']
