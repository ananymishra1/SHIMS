"""R&D predictive-chemistry guardian — deterministic, offline, always-on.

This is the local-first complement to the LLM-based `rd_brain.predict_reaction_dynamics`.
It reads an experiment's logged stages (temperature, pH, time, mixing, solvent, yields)
and emits structured, explainable risk flags + heuristic predictions WITHOUT needing any
AI provider online. Every flag carries a mechanism and a concrete mitigation so a chemist
(or the copilot) can act on it.

Goals it serves (Anany's brief): impurity reduction, yield increase, cost reduction.

Design notes
------------
- No external dependencies, no network, no LLM. Pure rules over numbers.
- Conservative thresholds chosen from common process-chemistry practice; they are
  heuristics, not validated specifications. Output is clearly labelled as guidance.
- Severity: 'critical' (likely batch/impurity/safety impact) > 'warn' > 'info'.
- The same function works on a live DB experiment (`assess_experiment_id`) or on a plain
  detail dict (`assess_experiment`) so it is trivial to unit-test.
"""
from __future__ import annotations

from typing import Any, Optional

# ---------------------------------------------------------------------------
# Solvent property reference (boiling point °C, flash point °C, class).
# Used for reflux / pressure / flammability and crash-cool oiling risk.
# ---------------------------------------------------------------------------
SOLVENTS: dict[str, dict[str, Any]] = {
    'water': {'bp': 100, 'flash': None, 'class': 'aqueous'},
    'methanol': {'bp': 65, 'flash': 11, 'class': 'alcohol'},
    'ethanol': {'bp': 78, 'flash': 13, 'class': 'alcohol'},
    'ipa': {'bp': 82, 'flash': 12, 'class': 'alcohol'},
    'isopropanol': {'bp': 82, 'flash': 12, 'class': 'alcohol'},
    'isopropyl alcohol': {'bp': 82, 'flash': 12, 'class': 'alcohol'},
    'acetone': {'bp': 56, 'flash': -20, 'class': 'ketone'},
    'mek': {'bp': 80, 'flash': -9, 'class': 'ketone'},
    'ethyl acetate': {'bp': 77, 'flash': -4, 'class': 'ester'},
    'etac': {'bp': 77, 'flash': -4, 'class': 'ester'},
    'thf': {'bp': 66, 'flash': -14, 'class': 'ether', 'peroxide': True},
    'tetrahydrofuran': {'bp': 66, 'flash': -14, 'class': 'ether', 'peroxide': True},
    'mtbe': {'bp': 55, 'flash': -28, 'class': 'ether', 'peroxide': True},
    'diethyl ether': {'bp': 35, 'flash': -45, 'class': 'ether', 'peroxide': True},
    'dichloromethane': {'bp': 40, 'flash': None, 'class': 'chlorinated'},
    'dcm': {'bp': 40, 'flash': None, 'class': 'chlorinated'},
    'mdc': {'bp': 40, 'flash': None, 'class': 'chlorinated'},
    'chloroform': {'bp': 61, 'flash': None, 'class': 'chlorinated'},
    'toluene': {'bp': 111, 'flash': 4, 'class': 'aromatic'},
    'xylene': {'bp': 139, 'flash': 27, 'class': 'aromatic'},
    'acetonitrile': {'bp': 82, 'flash': 2, 'class': 'nitrile'},
    'acn': {'bp': 82, 'flash': 2, 'class': 'nitrile'},
    'dmf': {'bp': 153, 'flash': 58, 'class': 'amide', 'high_boiling': True},
    'dmac': {'bp': 165, 'flash': 70, 'class': 'amide', 'high_boiling': True},
    'dmso': {'bp': 189, 'flash': 89, 'class': 'sulfoxide', 'high_boiling': True},
    'nmp': {'bp': 202, 'flash': 91, 'class': 'amide', 'high_boiling': True},
    'heptane': {'bp': 98, 'flash': -4, 'class': 'alkane'},
    'hexane': {'bp': 69, 'flash': -26, 'class': 'alkane'},
    'n-heptane': {'bp': 98, 'flash': -4, 'class': 'alkane'},
}

# Hydrolysis-sensitive functional groups (substring hints in RM / stage text).
_HYDROLYSIS_HINTS = ('ester', 'acetate', 'amide', 'nitrile', 'lactone', 'lactam',
                     'anhydride', 'acid chloride', 'acyl chloride', 'chloride', 'imine', 'schiff')
_OXIDATION_HINTS = ('aldehyde', 'phenol', 'catechol', 'thiol', 'grignard', 'organometallic',
                    'pd/c', 'palladium', 'borohydride', 'hydride', 'enolate', 'amine')
_CHIRAL_HINTS = ('chiral', 'enantio', 'stereo', 'epimer', 'optically active', '(s)-', '(r)-')


def _f(v: Any) -> Optional[float]:
    try:
        if v is None or v == '':
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _solvent_props(name: Any) -> Optional[dict[str, Any]]:
    if not name:
        return None
    key = str(name).strip().lower()
    if key in SOLVENTS:
        return SOLVENTS[key]
    # Substring fallback for mixtures ("ethanol/water"): pick the matching solvent
    # with the LOWEST boiling point — it refluxes first, the conservative choice.
    matches = [props for sk, props in SOLVENTS.items() if sk in key]
    if matches:
        return min(matches, key=lambda p: p.get('bp') if p.get('bp') is not None else 9999)
    return None


def _text_of(stage: dict[str, Any], rms: list[dict[str, Any]]) -> str:
    parts = [str(stage.get('rm_description') or ''), str(stage.get('stage_name') or ''),
             str(stage.get('notes') or ''), str(stage.get('catalyst') or '')]
    parts += [str(r.get('name') or '') for r in rms]
    return ' '.join(parts).lower()


def _flag(code: str, severity: str, stage_no: Optional[int], parameter: str,
          message: str, mechanism: str, mitigation: str) -> dict[str, Any]:
    return {'code': code, 'severity': severity, 'stage_no': stage_no, 'parameter': parameter,
            'message': message, 'mechanism': mechanism, 'mitigation': mitigation}


_SEV_RANK = {'critical': 3, 'warn': 2, 'info': 1}


def assess_experiment(detail: dict[str, Any]) -> dict[str, Any]:
    """Assess a single experiment detail dict. Returns flags + predictions + next trials."""
    exp = detail.get('experiment') or {}
    stages = list(detail.get('stages') or [])
    rms = list(detail.get('raw_materials') or [])
    all_text = ' '.join(_text_of(s, rms) for s in stages) + ' ' + str(exp.get('product_name') or '').lower()
    has_hydrolysable = any(h in all_text for h in _HYDROLYSIS_HINTS)
    has_oxidation_sensitive = any(h in all_text for h in _OXIDATION_HINTS)
    is_chiral = any(h in all_text for h in _CHIRAL_HINTS)

    flags: list[dict[str, Any]] = []

    prev_temp: Optional[float] = None
    prev_ph: Optional[float] = None
    prev_stage_no: Optional[int] = None

    for s in stages:
        sn = s.get('stage_no')
        temp = _f(s.get('temperature_c'))
        ph = _f(s.get('ph_value'))
        time_min = _f(s.get('reaction_time_minutes'))
        rpm = _f(s.get('mixing_speed_rpm'))
        pressure = _f(s.get('pressure_bar'))
        solv = _solvent_props(s.get('solvent'))
        text = _text_of(s, rms)

        # --- Absolute temperature vs solvent boiling point -----------------
        if temp is not None and solv and solv.get('bp') is not None:
            bp = solv['bp']
            if temp >= bp and (pressure is None or pressure <= 1.2):
                flags.append(_flag('TEMP_OVER_BP', 'critical', sn, 'temperature',
                    f"Stage {sn}: set point {temp:.0f}°C is at/above {s.get('solvent')} boiling point ({bp}°C) at ~atmospheric pressure.",
                    'Solvent boils → loss of solvent, uncontrolled reflux, possible pressure build-up if the vessel is sealed.',
                    'Run at reflux with a condenser and a vent, or move to a pressure-rated reactor, or pick a higher-boiling solvent.'))
            elif temp >= bp - 10:
                flags.append(_flag('TEMP_NEAR_BP', 'warn', sn, 'temperature',
                    f"Stage {sn}: {temp:.0f}°C is within 10°C of {s.get('solvent')} boiling point ({bp}°C).",
                    'Operating near boiling point makes temperature control fragile; minor overshoot causes reflux and solvent loss.',
                    'Add margin, ensure a working condenser, and control the heating ramp.'))

        # --- Flammability: heating a low-flash solvent ---------------------
        if temp is not None and solv and solv.get('flash') is not None and temp >= solv['flash']:
            flags.append(_flag('FLAMMABLE_ABOVE_FLASH', 'warn', sn, 'temperature',
                f"Stage {sn}: {temp:.0f}°C exceeds {s.get('solvent')} flash point ({solv['flash']}°C).",
                'Vapour above the flash point can form an ignitable atmosphere.',
                'Maintain inert (N2) blanket, eliminate ignition sources, and ensure earthing/bonding.'))

        # --- Stage-to-stage temperature change (heating / cooling rate) ----
        if temp is not None and prev_temp is not None:
            dT = temp - prev_temp
            rate = abs(dT) / time_min if (time_min and time_min > 0) else None  # °C/min
            # Cooling
            if dT <= -30:
                fast = rate is None or rate >= 1.0
                sev = 'critical' if (abs(dT) >= 50 and fast) else 'warn'
                rate_txt = f" (~{rate:.1f}°C/min)" if rate is not None else " (no time logged — assume fast)"
                flags.append(_flag('CRASH_COOL', sev, sn, 'cooling_rate',
                    f"Stage {sn}: rapid cool of {abs(dT):.0f}°C from stage {prev_stage_no}{rate_txt}.",
                    'Crash cooling drives fast, uncontrolled nucleation → wrong/mixed polymorph, oiling-out, fine unfilterable crystals, and impurity occlusion (lower purity and yield).',
                    'Cool at a controlled 0.3–0.7°C/min, add seed crystals of the target polymorph near the cloud point, and hold to mature crystals before filtration.'))
            # Heating
            elif dT >= 30:
                fast = rate is None or rate >= 1.0
                sev = 'critical' if (dT >= 50 and fast) else 'warn'
                rate_txt = f" (~{rate:.1f}°C/min)" if rate is not None else " (no time logged — assume fast)"
                flags.append(_flag('FAST_HEAT', sev, sn, 'heating_rate',
                    f"Stage {sn}: rapid heat of {dT:.0f}°C from stage {prev_stage_no}{rate_txt}.",
                    'Fast heating overshoots the set point and locally over-heats, accelerating side reactions and thermal degradation → higher impurity burden; risk of runaway if the step is exothermic.',
                    'Ramp slowly (≤0.5–1°C/min), use the jacket not direct heat, and verify the heat-removal capacity covers any exotherm at this scale.'))

        # --- pH extremes ----------------------------------------------------
        if ph is not None:
            if ph <= 1.5 or ph >= 12.5:
                sev = 'critical' if has_hydrolysable else 'warn'
                extra = ' The substrate contains hydrolysis-sensitive groups (ester/amide/nitrile/halide).' if has_hydrolysable else ''
                flags.append(_flag('PH_EXTREME', sev, sn, 'ph',
                    f"Stage {sn}: extreme pH {ph:.1f}.{extra}",
                    'Strongly acidic/basic conditions hydrolyse sensitive functional groups and can epimerise stereocentres → degradation impurities and assay loss.',
                    'Minimise time at extreme pH and temperature, quench promptly, and consider a milder reagent or buffered conditions.'))
            elif ph <= 3 or ph >= 11:
                flags.append(_flag('PH_HARSH', 'info', sn, 'ph',
                    f"Stage {sn}: harsh pH {ph:.1f}.",
                    'Borderline pH can slowly degrade sensitive substrates, especially when combined with heat or long hold times.',
                    'Watch related-substance growth on stability/IPC; reduce hold time if impurities rise.'))

        # --- pH swing between stages ---------------------------------------
        if ph is not None and prev_ph is not None and abs(ph - prev_ph) >= 6:
            flags.append(_flag('PH_SWING', 'warn', sn, 'ph',
                f"Stage {sn}: pH swing of {abs(ph - prev_ph):.0f} units from stage {prev_stage_no}.",
                'A large, fast neutralisation is strongly exothermic and creates local pH/temperature hot-spots → localized degradation and salt/impurity formation.',
                'Add acid/base slowly with good mixing and cooling; monitor temperature during the charge.'))

        # --- Exotherm / mixing -------------------------------------------
        is_addition = any(w in text for w in ('add', 'charge', 'quench', 'dropwise', 'portionwise'))
        if is_addition and (rpm is not None and rpm < 50):
            flags.append(_flag('LOW_MIXING_ADDITION', 'warn', sn, 'mixing_speed',
                f"Stage {sn}: reagent addition with low agitation ({rpm:.0f} rpm).",
                'Poor mixing lets reagent pool and react locally → hot-spots, selectivity loss and impurities; at scale this is a runaway risk.',
                'Increase agitation, add slowly sub-surface, and keep the jacket cold to absorb the exotherm.'))

        # --- Long hot hold -> thermal degradation --------------------------
        if temp is not None and time_min is not None and temp >= 90 and time_min >= 360:
            flags.append(_flag('LONG_HOT_HOLD', 'warn', sn, 'reaction_time',
                f"Stage {sn}: {time_min/60:.1f} h hold at {temp:.0f}°C.",
                'Prolonged heat exposure compounds thermal degradation pathways → rising related substances and colour.',
                'Track reaction conversion and stop at end-point rather than a fixed long time; lower temperature if conversion allows.'))

        # --- Oxidation-sensitive without inert atmosphere ------------------
        atm = str(s.get('atmosphere') or '').strip().lower()
        if has_oxidation_sensitive and atm in ('', 'air', 'none', 'open'):
            flags.append(_flag('NO_INERT_ATM', 'warn', sn, 'atmosphere',
                f"Stage {sn}: oxidation-sensitive chemistry with no inert atmosphere logged.",
                'Air/oxygen oxidises sensitive intermediates (aldehydes, phenols, organometallics, hydrides) → oxidative impurities and quenched reagents.',
                'Run under nitrogen/argon, degas the solvent, and log the atmosphere.'))

        # --- Stage yield / purity signals ----------------------------------
        ty = _f(s.get('theoretical_yield_pct'))
        ay = _f(s.get('actual_yield_pct'))
        if ty is not None and ay is not None and ty > 0 and ay < ty - 15:
            flags.append(_flag('YIELD_GAP', 'warn', sn, 'yield',
                f"Stage {sn}: actual yield {ay:.0f}% trails theoretical {ty:.0f}% by {ty-ay:.0f} points.",
                'A large yield gap points to incomplete conversion, product loss in work-up, or degradation.',
                'Check end-point (IPC), work-up losses (mother liquor assay), and stability of the product under the conditions.'))
        pur = _f(s.get('purity_pct'))
        if pur is not None and pur < 95:
            sev = 'warn' if pur >= 90 else 'critical'
            flags.append(_flag('LOW_PURITY', sev, sn, 'purity',
                f"Stage {sn}: in-process purity {pur:.1f}%.",
                'Carrying low-purity material forward propagates impurities to the API and burdens downstream purification.',
                'Add/insert a purification (recrystallisation, carbon treatment, wash) and identify the major impurity by HPLC.'))

        if temp is not None:
            prev_temp = temp
        if ph is not None:
            prev_ph = ph
        prev_stage_no = sn if sn is not None else prev_stage_no

    if is_chiral:
        flags.append(_flag('CHIRAL_WATCH', 'info', None, 'stereochemistry',
            'Chiral / stereochemically defined molecule.',
            'Heat and extreme pH promote epimerisation/racemisation → loss of chiral purity (a critical quality attribute).',
            'Add chiral purity (chiral HPLC / specific rotation) to the IPC and keep stereocentre-bearing steps mild.'))

    predictions = _predictions(stages, flags)
    next_trials = _next_trials(flags, predictions, exp)

    crit = sum(1 for f in flags if f['severity'] == 'critical')
    warn = sum(1 for f in flags if f['severity'] == 'warn')
    flags.sort(key=lambda f: (-_SEV_RANK.get(f['severity'], 0), f['stage_no'] if f['stage_no'] is not None else 999))

    return {
        'ok': True,
        'experiment_id': exp.get('id'),
        'product_name': exp.get('product_name'),
        'flags': flags,
        'counts': {'critical': crit, 'warn': warn, 'info': len(flags) - crit - warn, 'total': len(flags)},
        'predictions': predictions,
        'next_trials': next_trials,
        'engine': 'rd_predictive/deterministic-v1',
        'disclaimer': 'Heuristic process-chemistry guidance, not a validated specification. Confirm against lab data and SOPs.',
    }


def _predictions(stages: list[dict[str, Any]], flags: list[dict[str, Any]]) -> dict[str, Any]:
    """Heuristic yield / impurity / cost indices from stages + flag load."""
    # Predicted overall yield = product of stage yields (actual preferred, else theoretical).
    yield_fracs: list[float] = []
    for s in stages:
        ay = _f(s.get('actual_yield_pct'))
        ty = _f(s.get('theoretical_yield_pct'))
        v = ay if ay is not None else ty
        if v is not None and 0 < v <= 100:
            yield_fracs.append(v / 100.0)
    overall_yield = None
    if yield_fracs:
        prod = 1.0
        for fr in yield_fracs:
            prod *= fr
        overall_yield = round(prod * 100.0, 1)

    # Impurity risk index 0-100 from severity-weighted flags (capped).
    weight = sum({'critical': 22, 'warn': 9, 'info': 2}.get(f['severity'], 0) for f in flags)
    impurity_risk = min(100, weight)

    # Cost proxy: more stages + more critical flags (rework) + low yield => higher cost.
    n_stages = len([s for s in stages if s.get('stage_no') is not None])
    yield_penalty = 0 if overall_yield is None else max(0, (80 - overall_yield)) * 0.6
    cost_index = round(min(100, n_stages * 6 + impurity_risk * 0.4 + yield_penalty), 1)

    return {
        'predicted_overall_yield_pct': overall_yield,
        'impurity_risk_index': impurity_risk,
        'cost_index': cost_index,
        'n_stages': n_stages,
        'note': 'Indices are 0-100 heuristics for triage and trend, not absolute predictions.',
    }


def _next_trials(flags: list[dict[str, Any]], predictions: dict[str, Any], exp: dict[str, Any]) -> list[dict[str, Any]]:
    """Turn the worst flags into concrete next-experiment suggestions toward the goal
    (impurity down, yield up, cost down)."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    priority = sorted(flags, key=lambda f: -_SEV_RANK.get(f['severity'], 0))
    for f in priority:
        if f['code'] in seen:
            continue
        seen.add(f['code'])
        goal = {'CRASH_COOL': 'yield + polymorph control', 'FAST_HEAT': 'impurity reduction',
                'PH_EXTREME': 'impurity reduction', 'TEMP_OVER_BP': 'control + safety',
                'LOW_PURITY': 'impurity reduction', 'YIELD_GAP': 'yield increase'}.get(f['code'], 'process robustness')
        out.append({
            'trigger': f['code'],
            'stage_no': f['stage_no'],
            'hypothesis': f['mitigation'],
            'goal': goal,
            'design': f"Re-run stage {f['stage_no']} varying only the flagged parameter ({f['parameter']}); hold everything else constant and compare HPLC purity, isolated yield and filtration behaviour against the current run.",
        })
        if len(out) >= 4:
            break
    if not out:
        out.append({
            'trigger': 'baseline', 'stage_no': None, 'goal': 'yield increase',
            'hypothesis': 'No condition risks flagged. Pursue yield/cost via solvent volume reduction or catalyst loading.',
            'design': 'Run a small DoE on solvent volume and charge ratio around the current best point.',
        })
    return out


def assess_experiment_id(exp_id: int) -> dict[str, Any]:
    """Load an experiment from the DB and assess it. Thin DB wrapper around assess_experiment."""
    from shared.rd_lab import _build_experiment_detail  # local import to av