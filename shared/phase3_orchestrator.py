"""Phase 3 orchestrator: connect corpus learning → drug master → R&D → BMR → validation.

Workflow:
1. For each core product, ensure a drug_master API entry.
2. Ensure an R&D experiment exists with stages from corpus / fallback template.
3. Generate a BMR from the experiment.
4. Validate the BMR with bmr_validator.
5. Report results.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from .database import db
from .drug_master import create_api, list_apis
from .bmr_validator import validate_bmr_against_corpus
from .enterprise_pharma_core import (
    get_rd_experiment_detail,
    generate_bmr_from_plan,
    create_production_plan_from_experiment,
)
from .bmr_process_map_ai import get_ai_process_map


CORE_PRODUCTS = [
    'Fluconazole', 'Itraconazole', 'Losartan potassium', 'Duloxetine HCL',
    'Gabapentin', 'Telmisartan', 'Ketoconazole', 'Rosuvastatin',
    'Olmesartan Medoxomil', 'DFTA', 'AP-301', 'Ambroxol HCl',
    'Aripiprazole', 'Atorvastatin Calcium', 'Brivaracetam', 'Desloratadine',
    'Levetiracetam Cc Ep Usp', 'Minoxidil', 'Rivaroxaban', 'Vildagliptin',
    'Trazodone HCL',
]


def ensure_drug_master_for_products(user_id: int | None = None) -> dict[str, Any]:
    created = 0
    existing = 0
    for name in CORE_PRODUCTS:
        apis = list_apis(search=name)
        if any(name.lower() in (a.get('generic_name') or '').lower() for a in apis):
            existing += 1
            continue
        try:
            create_api({
                'generic_name': name,
                'category': 'API',
                'status': 'active',
                'manufacturer': 'SHIMS Manufacturing',
            }, user_id=user_id)
            created += 1
        except Exception as exc:
            pass
    return {'ok': True, 'created': created, 'existing': existing}


def _load_json(text: str | None, default: Any) -> Any:
    if not text:
        return default
    try:
        return json.loads(text)
    except Exception:
        return default


def ensure_rd_experiment_for_product(product_name: str, user_id: int | None = None) -> dict[str, Any]:
    """Create or find an R&D experiment for the product using corpus process map or fallback."""
    # Check existing experiments
    rows = db.query("SELECT id FROM rd_experiments WHERE lower(product_name)=lower(?) ORDER BY id DESC LIMIT 1", (product_name,))
    if rows:
        return {'ok': True, 'experiment_id': rows[0]['id'], 'created': False}

    # Try AI process map
    pm = get_ai_process_map(product_name)
    stages = []
    if pm and pm.get('stages'):
        for st in pm['stages']:
            stages.append({
                'stage_name': st.get('stage_name') or st.get('stage_label') or 'operation',
                'description': st.get('description') or '',
                'duration_hours': st.get('duration_hours') or 4,
                'target_yield_pct': 75.0,
                'critical_parameters': json.dumps(st.get('critical_parameters') or []),
            })

    # Fallback to simple template
    if not stages:
        stages = [
            {'stage_name': 'reaction', 'description': f'Synthesis of {product_name}', 'duration_hours': 6, 'target_yield_pct': 85.0, 'critical_parameters': json.dumps([{'param': 'temperature', 'value': '50-60 C'}])},
            {'stage_name': 'quench', 'description': 'Quench and cool', 'duration_hours': 2, 'target_yield_pct': 95.0, 'critical_parameters': json.dumps([{'param': 'pH', 'value': '6-7'}])},
            {'stage_name': 'extraction', 'description': 'Workup and extraction', 'duration_hours': 3, 'target_yield_pct': 90.0, 'critical_parameters': '[]'},
            {'stage_name': 'crystallization', 'description': 'Crystallization', 'duration_hours': 4, 'target_yield_pct': 85.0, 'critical_parameters': json.dumps([{'param': 'temperature', 'value': '0-5 C'}])},
            {'stage_name': 'filtration', 'description': 'Filtration and washing', 'duration_hours': 2, 'target_yield_pct': 98.0, 'critical_parameters': '[]'},
            {'stage_name': 'drying', 'description': 'Drying under vacuum', 'duration_hours': 8, 'target_yield_pct': 99.0, 'critical_parameters': json.dumps([{'param': 'temperature', 'value': '60-70 C'}])},
            {'stage_name': 'sizing', 'description': 'Sizing / milling', 'duration_hours': 2, 'target_yield_pct': 99.5, 'critical_parameters': '[]'},
            {'stage_name': 'packing', 'description': 'Packing and labeling', 'duration_hours': 1, 'target_yield_pct': 100.0, 'critical_parameters': '[]'},
        ]

    # Create experiment in rd_experiments / rd_experiment_stages
    exp_id = db.execute(
        'INSERT INTO rd_experiments(product_name, route_name, notes, status, created_by) VALUES (?, ?, ?, ?, ?)',
        (product_name, f'{product_name} API Route', 'Auto-created from Phase 3 orchestrator using corpus process map or fallback template', 'active', user_id),
    )
    for idx, st in enumerate(stages, 1):
        # Parse critical_parameters for temperature and pH if present
        temp = None
        ph = None
        params = json.loads(st.get('critical_parameters') or '[]')
        for p in params:
            if isinstance(p, dict):
                pn = str(p.get('param') or '').lower()
                if 'temp' in pn:
                    try:
                        temp = float(re.findall(r'\d+\.?\d*', str(p.get('value') or ''))[0])
                    except Exception:
                        pass
                if 'ph' in pn:
                    try:
                        ph = float(re.findall(r'\d+\.?\d*', str(p.get('value') or ''))[0])
                    except Exception:
                        pass
        db.execute(
            'INSERT INTO rd_experiment_stages(experiment_id, stage_no, stage_name, temperature_c, ph_value, solvent, catalyst, theoretical_yield_pct, notes) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (exp_id, idx, st['stage_name'], temp, ph, '', '', st['target_yield_pct'], st['description']),
        )
    db.audit(user_id, 'create', 'experiment', exp_id, {'product': product_name, 'stages': len(stages), 'source': 'phase3_orchestrator'})
    return {'ok': True, 'experiment_id': exp_id, 'created': True, 'stages': len(stages)}


def generate_bmr_for_experiment(experiment_id: int, user_id: int | None = None) -> dict[str, Any]:
    """Generate a BMR from an experiment."""
    exp = get_rd_experiment_detail(experiment_id)
    if not exp:
        return {'ok': False, 'error': 'Experiment not found'}
    # Create production plan first
    plan_id = create_production_plan_from_experiment(user_id, experiment_id, {
        'target_qty': 100,
        'batch_size': 100,
        'priority': 'normal',
        'notes': 'Auto-generated from Phase 3 orchestrator',
    })
    if not plan_id:
        return {'ok': False, 'error': 'Failed to create production plan'}
    # Generate BMR from plan
    bmr_id = generate_bmr_from_plan(user_id, plan_id)
    if not bmr_id:
        return {'ok': False, 'error': 'Failed to generate BMR from plan'}
    return {'ok': True, 'bmr_id': bmr_id, 'plan_id': plan_id}


def run_phase3_for_product(product_name: str, user_id: int | None = None) -> dict[str, Any]:
    """Full Phase 3 pipeline for one product."""
    results = {'product_name': product_name}

    # 1. Drug master
    apis = list_apis(search=product_name)
    api_match = next((a for a in apis if product_name.lower() in (a.get('generic_name') or '').lower()), None)
    if not api_match:
        try:
            api_id = create_api({'generic_name': product_name, 'category': 'API', 'status': 'active', 'manufacturer': 'SHIMS Manufacturing'}, user_id=user_id)
            results['api_id'] = api_id
        except Exception as exc:
            results['api_error'] = str(exc)
    else:
        results['api_id'] = api_match['id']

    # 2. R&D experiment
    exp_result = ensure_rd_experiment_for_product(product_name, user_id)
    results['experiment'] = exp_result

    # 3. BMR
    if exp_result.get('experiment_id'):
        bmr_result = generate_bmr_for_experiment(exp_result['experiment_id'], user_id)
        results['bmr'] = bmr_result

        # 4. Validate
        if bmr_result.get('bmr_id'):
            try:
                report = validate_bmr_against_corpus(bmr_result['bmr_id'], user_id=user_id)
                results['validation'] = {
                    'overall_score': report.overall_score,
                    'status': report.status,
                    'findings_count': len(report.findings),
                    'critical': len([f for f in report.findings if f.severity == 'critical']),
                    'major': len([f for f in report.findings if f.severity == 'major']),
                }
            except Exception as exc:
                results['validation_error'] = str(exc)

    return results


def run_phase3_batch(user_id: int | None = None, products: list[str] | None = None) -> dict[str, Any]:
    if products is None:
        products = CORE_PRODUCTS
    all_results = []
    for name in products:
        all_results.append(run_phase3_for_product(name, user_id))
    return {'ok': True, 'products_processed': len(all_results), 'results': all_results}
