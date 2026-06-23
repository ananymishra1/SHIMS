"""Tests for the deterministic R&D predictive-chemistry guardian.

These run fully offline (no DB, no LLM) by passing a detail dict directly.
"""
from __future__ import annotations

from shared.rd_predictive import assess_experiment, _solvent_props


def _codes(res):
    return {f['code'] for f in res['flags']}


def test_crash_cool_flagged():
    detail = {
        'experiment': {'id': 1, 'product_name': 'Fluconazole'},
        'stages': [
            {'stage_no': 1, 'stage_name': 'Reflux', 'temperature_c': 80, 'solvent': 'methanol', 'reaction_time_minutes': 120},
            {'stage_no': 2, 'stage_name': 'Cool & crystallise', 'temperature_c': 5, 'solvent': 'methanol', 'reaction_time_minutes': 20},
        ],
        'raw_materials': [],
    }
    res = assess_experiment(detail)
    assert 'CRASH_COOL' in _codes(res)
    cc = next(f for f in res['flags'] if f['code'] == 'CRASH_COOL')
    assert cc['severity'] in ('warn', 'critical')
    assert 'polymorph' in cc['mechanism'].lower()


def test_fast_heat_and_over_bp():
    detail = {
        'experiment': {'id': 2, 'product_name': 'Intermediate X'},
        'stages': [
            {'stage_no': 1, 'temperature_c': 20, 'solvent': 'acetone'},
            {'stage_no': 2, 'temperature_c': 75, 'solvent': 'acetone', 'reaction_time_minutes': 10},
        ],
        'raw_materials': [],
    }
    res = assess_experiment(detail)
    codes = _codes(res)
    assert 'FAST_HEAT' in codes
    # acetone bp 56 -> 75C is over bp
    assert 'TEMP_OVER_BP' in codes


def test_ph_extreme_with_hydrolysable():
    detail = {
        'experiment': {'id': 3, 'product_name': 'Ester API'},
        'stages': [
            {'stage_no': 1, 'stage_name': 'Hydrolysis', 'ph_value': 13.5, 'temperature_c': 60,
             'rm_description': 'ethyl ester intermediate, NaOH'},
        ],
        'raw_materials': [{'name': 'ethyl acetate ester'}],
    }
    res = assess_experiment(detail)
    pe = next(f for f in res['flags'] if f['code'] == 'PH_EXTREME')
    assert pe['severity'] == 'critical'  # hydrolysable substrate escalates severity


def test_ph_swing():
    detail = {
        'experiment': {'id': 4, 'product_name': 'Salt form'},
        'stages': [
            {'stage_no': 1, 'ph_value': 1.0},
            {'stage_no': 2, 'ph_value': 9.0},
        ],
        'raw_materials': [],
    }
    assert 'PH_SWING' in _codes(assess_experiment(detail))


def test_clean_experiment_low_risk_and_yield_prediction():
    detail = {
        'experiment': {'id': 5, 'product_name': 'Clean Process'},
        'stages': [
            {'stage_no': 1, 'temperature_c': 25, 'solvent': 'toluene', 'reaction_time_minutes': 120,
             'theoretical_yield_pct': 95, 'actual_yield_pct': 92, 'purity_pct': 99.2},
            {'stage_no': 2, 'temperature_c': 40, 'solvent': 'toluene', 'reaction_time_minutes': 90,
             'theoretical_yield_pct': 90, 'actual_yield_pct': 88, 'purity_pct': 99.5},
        ],
        'raw_materials': [],
    }
    res = assess_experiment(detail)
    assert res['counts']['critical'] == 0
    # overall yield ~ 0.92 * 0.88 = ~81%
    assert 78 <= res['predictions']['predicted_overall_yield_pct'] <= 82
    assert res['predictions']['impurity_risk_index'] < 30


def test_next_trials_present_when_flags():
    detail = {
        'experiment': {'id': 6, 'product_name': 'Risky'},
        'stages': [
            {'stage_no': 1, 'temperature_c': 90, 'solvent': 'methanol'},
            {'stage_no': 2, 'temperature_c': 0, 'solvent': 'methanol', 'reaction_time_minutes': 15},
        ],
        'raw_materials': [],
    }
    res = assess_experiment(detail)
    assert res['next_trials']
    assert all('design' in t for t in res['next_trials'])


def test_solvent_lookup_substring():
    assert _solvent_props('Ethanol / Water')['bp'] == 78
    assert _solvent_props('DMF')['high_boiling'] is True
    assert _solvent_props(None) is None
