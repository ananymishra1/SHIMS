"""R&D Brain v2 — product-intent orchestrator for the BMR-trained enterprise brain.

Flow:
  1. product_intent(product) -> BMR corpus + chemistry + existing routes + next actions.
  2. search_patents_for_product -> multi-jurisdiction patent loop via RDBrain.
  3. suggest_routes -> corpus route + patent routes, each verified with shims_chem.
  4. score_route_for_plant -> cost + equipment fit.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from shared.enterprise_bmr_corpus import search_corpus
from shared.product_chemistry import (
    analyze_product_chemistry,
    get_canonical_product_name,
    list_route_stages,
    list_verified_prices,
    manufacturing_options,
    suggest_manufacturing_routes,
)
from shared.enterprise_pharma_core import create_rd_experiment_from_product
from shared.rd_brain import RDBrain
from shared import shims_chem_api
from shared.equipment_intelligence import equipment_fit_for_batch


async def product_intent(product_name: str, user_id: int | None = None, intent: str = 'inspect') -> dict[str, Any]:
    """Inspect a product and recommend the next R&D action."""
    canonical = get_canonical_product_name(product_name)
    # 1. BMR corpus search
    corpus_hits = search_corpus(canonical, limit=12)
    # 2. Structured chemistry
    chemistry = analyze_product_chemistry(canonical, user_id)
    # 3. Existing route options
    options = manufacturing_options(canonical)
    # 4. Next actions
    has_corpus = bool(corpus_hits.get('hits'))
    has_routes = bool(options.get('options'))
    next_actions = []
    if not has_corpus:
        next_actions.append({'action': 'create_research_project', 'label': f'Start R&D for {canonical} (no BMR found)'})
        next_actions.append({'action': 'search_patents', 'label': f'Search patents for {canonical}'})
    else:
        next_actions.append({'action': 'run_bmr_route', 'label': f'Run the BMR route for {canonical} as an experiment'})
        next_actions.append({'action': 'search_patents', 'label': f'Search patents to find alternate routes for {canonical}'})
        next_actions.append({'action': 'modify_route', 'label': f'Modify/optimize the existing route for {canonical}'})
    if intent == 'search_patents':
        patents = await _search_patents_for_product(canonical)
        return {
            'product_name': canonical,
            'intent': intent,
            'corpus': corpus_hits,
            'chemistry': chemistry,
            'existing_routes': options,
            'patents': patents,
            'next_actions': next_actions,
        }
    if intent == 'run_bmr_route':
        exp = create_rd_experiment_from_product(user_id, canonical, source='bmr')
        return {
            'product_name': canonical,
            'intent': intent,
            'corpus': corpus_hits,
            'experiment': exp,
            'next_actions': [{'action': 'open_experiment', 'label': f'Open experiment {exp["experiment_id"]}'}],
        }
    if intent == 'modify_route':
        routes = await suggest_routes(canonical, user_id=user_id)
        return {
            'product_name': canonical,
            'intent': intent,
            'corpus': corpus_hits,
            'routes': routes,
            'next_actions': next_actions,
        }
    # default inspect
    return {
        'product_name': canonical,
        'intent': intent,
        'corpus': corpus_hits,
        'chemistry': chemistry,
        'existing_routes': options,
        'next_actions': next_actions,
    }


async def _search_patents_for_product(product_name: str, top_k: int = 10) -> dict[str, Any]:
    brain = RDBrain()
    queries = [
        f"{product_name} synthesis patent",
        f"{product_name} process for preparation patent",
        f"{product_name} API patent",
    ]
    all_patents: list[dict[str, Any]] = []
    seen: set[str] = set()
    for q in queries:
        try:
            results = await brain.patent_search(q, top_k=top_k, jurisdictions=['US', 'CN', 'EP', 'WO', 'IN', 'JP'])
        except Exception:
            results = []
        for p in results:
            key = (p.patent_number or '').strip()
            if not key or key in seen:
                continue
            seen.add(key)
            all_patents.append(p.__dict__)
    return {'queries': queries, 'patents': all_patents, 'count': len(all_patents)}


async def suggest_routes(product_name: str, constraints: str = '', user_id: int | None = None) -> dict[str, Any]:
    """Return BMR route + patent-derived route options, each chemically verified."""
    canonical = get_canonical_product_name(product_name)
    # Corpus routes
    corpus_routes = suggest_manufacturing_routes(canonical, user_id)
    # Patent routes
    patent_data = await _search_patents_for_product(canonical)
    # Ask brain to synthesize route options from patent abstracts + corpus
    brain = RDBrain()
    system = (
        "You are a pharmaceutical process chemist. Given a product name, BMR corpus summary and patent abstracts, "
        "propose 2-4 distinct synthetic route options. Return JSON with 'routes': list of objects with "
        "'name', 'steps': [{'description','reagents':[],'conditions':'','reaction_smiles':''}], "
        "'starting_materials':[], 'overall_yield_pct', 'key_refs':[]}. "
        "Only include chemically plausible steps. If a reaction SMILES is uncertain, leave it blank."
    )
    prompt = f"Product: {canonical}\nConstraints: {constraints}\n\nBMR routes:\n{json.dumps(corpus_routes.get('routes', [])[:3], ensure_ascii=False)}\n\nPatents:\n{json.dumps(patent_data.get('patents', [])[:8], ensure_ascii=False)}\n\nPropose route options."
    try:
        text = await brain._call_ai(prompt, system=system, temperature=0.2)
        parsed = json.loads(text)
        routes = parsed.get('routes', []) if isinstance(parsed, dict) else []
    except Exception:
        routes = []
    # Verify each route
    verified = []
    for r in routes:
        steps = r.get('steps', [])
        rxn_steps = []
        for s in steps:
            smi = s.get('reaction_smiles', '')
            if not smi and s.get('reagents'):
                # Heuristic: try to build a reaction SMILES from reagents only if verifier can parse later
                smi = '.'.join(s.get('reagents', [])) + '>>' + canonical
            rxn_steps.append({'description': s.get('description', ''), 'reaction_smiles': smi})
        ver = await verify_route_steps(rxn_steps)
        r['verification'] = ver
        plant = score_route_for_plant(r, canonical)
        r['plant_score'] = plant
        verified.append(r)
    return {
        'product_name': canonical,
        'corpus_routes': corpus_routes.get('routes', []),
        'patent_queries': patent_data['queries'],
        'patent_count': patent_data['count'],
        'suggested_routes': verified,
    }


async def verify_route_steps(steps: list[dict[str, Any]]) -> dict[str, Any]:
    """Run every route step through the symbolic chemistry verifier."""
    if not steps:
        return {'ok': False, 'error': 'No steps provided'}
    results = []
    overall_feasible = True
    overall_score = 1.0
    for i, step in enumerate(steps, 1):
        smi = step.get('reaction_smiles', '')
        if not smi:
            results.append({'step': i, 'status': 'skipped', 'reason': 'No reaction SMILES'})
            overall_score *= 0.8
            continue
        bal = shims_chem_api.verify_reaction(smi)
        hazards = shims_chem_api.verify_hazards(smi)
        # Feasibility score from verifier
        try:
            feas = shims_chem_api.run_verifier_tool('score_route_feasibility', steps=[smi])
            step_score = feas.get('data', {}).get('score', 0.5) if isinstance(feas, dict) else 0.5
        except Exception:
            step_score = 0.5
        step_ok = bool(bal.get('ok')) and not any(i.get('severity') in ('critical', 'error') for i in hazards.get('issues', []))
        if not step_ok:
            overall_feasible = False
        overall_score *= max(0.1, step_score)
        results.append({
            'step': i,
            'description': step.get('description', ''),
            'reaction_smiles': smi,
            'balanced': bal.get('ok') and bal.get('data', {}).get('report', {}).get('balanced'),
            'hazard_issues': hazards.get('issues', []),
            'feasibility_score': round(step_score, 3),
            'status': 'pass' if step_ok else 'fail',
        })
    return {
        'ok': overall_feasible,
        'overall_feasibility_score': round(overall_score, 4),
        'steps': results,
        'disclaimer': 'Symbolic verification only; experimental validation required for GxP decisions.',
    }


def score_route_for_plant(route: dict[str, Any], product_name: str) -> dict[str, Any]:
    """Score a route on cost and equipment fit using verified plant data."""
    materials = route.get('starting_materials', []) or []
    # Pull verified prices
    price_total = 0.0
    price_hits = 0
    missing_prices: list[str] = []
    for mat in materials:
        hit = list_verified_prices(mat).get('prices', [])
        if hit:
            price_total += float(hit[0].get('price_per_kg') or 0)
            price_hits += 1
        else:
            missing_prices.append(mat)
    # Equipment check: placeholder — try to fit a 1 kg batch
    equipment_ok = True
    equipment_notes = []
    try:
        fit = equipment_fit_for_batch(product_name, 1.0)
        equipment_ok = bool(fit.get('fit'))
        equipment_notes = fit.get('notes', [])
    except Exception as exc:
        equipment_notes.append(f'Equipment check failed: {exc}')
    cost_per_kg = price_total if price_hits else None
    return {
        'verified_rm_count': price_hits,
        'total_verified_rm_price_inr_per_kg': round(price_total, 2),
        'estimated_cost_per_kg_api_inr': round(cost_per_kg, 2) if cost_per_kg else None,
        'missing_verified_prices': missing_prices,
        'equipment_fit': equipment_ok,
        'equipment_notes': equipment_notes,
        'costing_confidence': 'verified' if price_hits == len(materials) else ('partial' if price_hits else 'unverified'),
    }
