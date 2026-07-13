"""Tech-Transfer scale-up engine — deterministic, pure standard library, no I/O.

Provides :func:`scale_batch` which takes a lab-scale experiment description, a
target batch size and a target vessel specification and returns a structured
scale-up assessment including:

* Charge scaling factors and per-material scaled charges.
* Working-volume fit check (will the batch fit in the vessel?).
* Heat-transfer area/volume (A/V) ratio change and a qualitative
  cooling/heating-time variance estimate.
* A list of risk flags (code, severity, message, mitigation) covering vessel
  overfill, large A/V drop, mixing/tip-speed change, and exotherm-at-scale.
* Suggested hold points for the scaled process.

All inputs are plain :class:`dict` objects; the function is defensive about
missing or malformed keys and will never raise on bad input.

Design notes
------------
- No external dependencies.  Pure ``math`` + builtins.
- Conservative thresholds are heuristics from common process-chemistry / engineering
  practice; they are NOT validated specifications.
- Severity ladder: ``'critical'`` > ``'warn'`` > ``'info'``.
"""
from __future__ import annotations

import math
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _f(v: Any, default: Optional[float] = None) -> Optional[float]:
    """Safely cast *v* to float; return *default* on failure."""
    if v is None or v == '':
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _flag(
    code: str,
    severity: str,
    message: str,
    mitigation: str,
) -> dict[str, str]:
    """Return a standardised risk-flag dict."""
    return {
        'code': code,
        'severity': severity,
        'message': message,
        'mitigation': mitigation,
    }


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def _cylinder_av(diameter_m: float, height_m: float) -> Optional[float]:
    """A/V ratio for a cylindrical vessel (m^-1).  Returns None on bad input."""
    if diameter_m <= 0 or height_m <= 0:
        return None
    r = diameter_m / 2.0
    area = 2 * math.pi * r * height_m + math.pi * r ** 2  # side wall + bottom
    volume = math.pi * r ** 2 * height_m
    return area / volume


def _sphere_av(diameter_m: float) -> Optional[float]:
    """A/V ratio for a sphere (m^-1)."""
    if diameter_m <= 0:
        return None
    r = diameter_m / 2.0
    return (4 * math.pi * r ** 2) / ((4 / 3) * math.pi * r ** 3)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_SEV_RANK = {'critical': 3, 'warn': 2, 'info': 1}


def scale_batch(
    lab: dict[str, Any],
    target_batch_kg: float,
    vessel: dict[str, Any],
) -> dict[str, Any]:
    """Perform a deterministic Tech-Transfer scale-up assessment.

    Parameters
    ----------
    lab:
        Lab-scale experiment descriptor.  Expected keys (all optional with
        sensible defaults):

        ``batch_kg``          – lab batch size in kg (float, required for scaling).
        ``density_kg_L``      – bulk density of the batch (kg/L, default 1.0).
        ``exotherm_kJ_kg``    – heat of reaction per kg of batch (float, optional).
        ``materials``         – list of dicts with ``name`` (str) and ``charge_kg``
                                (float) describing each reagent/solvent charge.
        ``impeller_diameter_m`` – lab impeller diameter in metres (optional).
        ``impeller_rpm``      – lab agitator speed in RPM (optional).
        ``vessel_diameter_m`` – lab vessel internal diameter in metres (optional).
        ``vessel_height_m``   – lab vessel working height in metres (optional).

    target_batch_kg:
        Desired production batch size in kg.

    vessel:
        Target vessel specification.  Expected keys:

        ``working_volume_L``    – usable volume in litres (required).
        ``total_volume_L``      – total vessel volume in litres (optional).
        ``diameter_m``          – internal diameter in metres (optional).
        ``height_m``            – working height in metres (optional).
        ``impeller_diameter_m`` – production impeller diameter in metres (optional).
        ``impeller_rpm``        – production agitator speed in RPM (optional).
        ``jacket_area_m2``      – heat-transfer jacket area in m² (optional).

    Returns
    -------
    dict with keys:
        ``scaling_factor``       – float ratio target/lab batch size.
        ``scaled_charges``       – list of dicts (name, lab_kg, scaled_kg).
        ``volume_fit``           – dict with volume details and ``fits`` bool.
        ``heat_transfer``        – dict with A/V ratios, change, time variance estimate.
        ``risk_flags``           – list of risk-flag dicts.
        ``hold_points``          – list of suggested hold-point strings.
        ``engine``               – identifier string.
        ``disclaimer``           – disclaimer string.
    """
    flags: list[dict[str, str]] = []
    hold_points: list[str] = []

    # ------------------------------------------------------------------
    # 1. Scaling factor
    # ------------------------------------------------------------------
    lab_batch_kg = _f(lab.get('batch_kg'))
    if lab_batch_kg is None or lab_batch_kg <= 0:
        lab_batch_kg = None

    if lab_batch_kg is not None and target_batch_kg > 0:
        scaling_factor: Optional[float] = target_batch_kg / lab_batch_kg
    else:
        scaling_factor = None

    # ------------------------------------------------------------------
    # 2. Scaled charges (linear by default)
    # ------------------------------------------------------------------
    materials = lab.get('materials') or []
    scaled_charges: list[dict[str, Any]] = []
    for mat in materials:
        if not isinstance(mat, dict):
            continue
        name = str(mat.get('name') or 'unknown')
        lab_charge = _f(mat.get('charge_kg'), 0.0)
        if scaling_factor is not None and lab_charge is not None:
            scaled_kg = round(lab_charge * scaling_factor, 4)
        else:
            scaled_kg = None
        scaled_charges.append({
            'name': name,
            'lab_kg': lab_charge,
            'scaled_kg': scaled_kg,
        })

    # ------------------------------------------------------------------
    # 3. Volume fit check
    # ------------------------------------------------------------------
    density = _f(lab.get('density_kg_L'), 1.0) or 1.0
    batch_volume_L = target_batch_kg / density
    working_volume_L = _f(vessel.get('working_volume_L'))
    total_volume_L = _f(vessel.get('total_volume_L'))

    fits: Optional[bool] = None
    fill_fraction: Optional[float] = None
    if working_volume_L is not None and working_volume_L > 0:
        fill_fraction = batch_volume_L / working_volume_L
        fits = fill_fraction <= 1.0
    elif total_volume_L is not None and total_volume_L > 0:
        # Assume 80 % working-volume rule if only total is given
        effective_wv = total_volume_L * 0.80
        fill_fraction = batch_volume_L / effective_wv
        fits = fill_fraction <= 1.0

    volume_fit: dict[str, Any] = {
        'batch_volume_L': round(batch_volume_L, 2),
        'working_volume_L': working_volume_L,
        'fill_fraction': round(fill_fraction, 4) if fill_fraction is not None else None,
        'fits': fits,
    }

    # Vessel overfill flag
    if fill_fraction is not None:
        if fill_fraction > 1.0:
            flags.append(_flag(
                'VESSEL_OVERFILL',
                'critical',
                f"Batch volume {batch_volume_L:.1f} L exceeds vessel working volume "
                f"{working_volume_L or '(estimated)'} L "
                f"(fill fraction {fill_fraction:.0%}).",
                'Select a larger vessel or reduce batch size to ≤80 % of working volume. '
                'Never exceed rated working volume — overfill causes spillage, loss of '
                'reflux/condenser control, and potential safety incidents.',
            ))
            hold_points.append('HOLD: Confirm vessel size before scale-up authorisation.')
        elif fill_fraction > 0.85:
            flags.append(_flag(
                'VESSEL_HIGH_FILL',
                'warn',
                f"Fill fraction {fill_fraction:.0%} is above the recommended 80 % '"
                f"working-volume limit (batch {batch_volume_L:.1f} L).",
                'Consider a vessel with ≥20 % headspace margin to allow for '
                'foam, reflux and safe reagent addition.',
            ))

    # ------------------------------------------------------------------
    # 4. Heat-transfer A/V ratio
    # ------------------------------------------------------------------
    lab_diam = _f(lab.get('vessel_diameter_m'))
    lab_h = _f(lab.get('vessel_height_m'))
    prod_diam = _f(vessel.get('diameter_m'))
    prod_h = _f(vessel.get('height_m'))

    lab_av: Optional[float] = None
    prod_av: Optional[float] = None
    av_ratio: Optional[float] = None  # prod / lab — should be < 1 at scale
    av_change_pct: Optional[float] = None
    cooling_time_factor: Optional[float] = None  # proportional estimate

    if lab_diam and lab_h:
        lab_av = _cylinder_av(lab_diam, lab_h)
    if prod_diam and prod_h:
        prod_av = _cylinder_av(prod_diam, prod_h)
    elif prod_diam and not prod_h:
        # Fallback: estimate height from volume assuming cylinder
        if working_volume_L and working_volume_L > 0:
            vol_m3 = working_volume_L / 1000.0
            r = prod_diam / 2.0
            est_h = vol_m3 / (math.pi * r ** 2) if r > 0 else None
            if est_h:
                prod_av = _cylinder_av(prod_diam, est_h)

    if lab_av and prod_av and lab_av > 0:
        av_ratio = prod_av / lab_av
        av_change_pct = round((av_ratio - 1.0) * 100.0, 1)
        # Cooling time is approximately proportional to V/A (inverse of A/V)
        # so time_scale ~ (1/prod_av) / (1/lab_av) = lab_av / prod_av
        cooling_time_factor = round(lab_av / prod_av, 2)

    heat_transfer: dict[str, Any] = {
        'lab_av_per_m': round(lab_av, 4) if lab_av is not None else None,
        'vessel_av_per_m': round(prod_av, 4) if prod_av is not None else None,
        'av_ratio_vessel_vs_lab': round(av_ratio, 4) if av_ratio is not None else None,
        'av_change_pct': av_change_pct,
        'cooling_heating_time_factor': cooling_time_factor,
        'note': (
            'cooling_heating_time_factor is a geometric estimate; '
            'actual times depend on jacket duty and Cp.'
        ),
    }

    # A/V drop flag — slow cooling at scale risks wrong polymorph / impurity
    AV_DROP_WARN = -30.0  # % drop in A/V relative to lab
    AV_DROP_CRIT = -50.0
    if av_change_pct is not None:
        if av_change_pct <= AV_DROP_CRIT:
            flags.append(_flag(
                'LARGE_AV_DROP',
                'critical',
                f"A/V ratio drops by {abs(av_change_pct):.0f}% at scale "
                f"(lab {lab_av:.2f} m⁻¹ → vessel {prod_av:.2f} m⁻¹). "
                f"Estimated cooling/heating time ×{cooling_time_factor:.1f} vs lab.",
                'Install dedicated cooling capacity (additional coils, HEX), implement '
                'controlled cooling ramps (≤0.5°C/min near crystallisation zone), '
                'add seeding protocol, and run a heat-balance before scale-up approval.',
            ))
            hold_points.append(
                'HOLD: Complete heat-balance calculation and validate cooling capacity before proceeding.'
            )
        elif av_change_pct <= AV_DROP_WARN:
            flags.append(_flag(
                'AV_DROP',
                'warn',
                f"A/V ratio drops by {abs(av_change_pct):.0f}% at scale. "
                f"Cooling/heating ~{cooling_time_factor:.1f}× slower than lab.",
                'Review jacket/coil capacity, use a seeding protocol for crystallisations, '
                'and tighten temperature-ramp controls at scale.',
            ))

    # ------------------------------------------------------------------
    # 5. Mixing / tip-speed change
    # ------------------------------------------------------------------
    lab_imp_d = _f(lab.get('impeller_diameter_m'))
    lab_rpm = _f(lab.get('impeller_rpm'))
    prod_imp_d = _f(vessel.get('impeller_diameter_m'))
    prod_rpm = _f(vessel.get('impeller_rpm'))

    lab_tip_speed: Optional[float] = None
    prod_tip_speed: Optional[float] = None
    tip_ratio: Optional[float] = None

    if lab_imp_d and lab_rpm:
        lab_tip_speed = math.pi * lab_imp_d * lab_rpm / 60.0  # m/s
    if prod_imp_d and prod_rpm:
        prod_tip_speed = math.pi * prod_imp_d * prod_rpm / 60.0  # m/s

    mixing_info: dict[str, Any] = {
        'lab_tip_speed_m_s': round(lab_tip_speed, 3) if lab_tip_speed is not None else None,
        'vessel_tip_speed_m_s': round(prod_tip_speed, 3) if prod_tip_speed is not None else None,
    }

    if lab_tip_speed and prod_tip_speed and lab_tip_speed > 0:
        tip_ratio = prod_tip_speed / lab_tip_speed
        mixing_info['tip_speed_ratio'] = round(tip_ratio, 3)

        if tip_ratio > 2.0:
            flags.append(_flag(
                'HIGH_TIP_SPEED',
                'warn',
                f"Production tip speed ({prod_tip_speed:.2f} m/s) is "
                f"{tip_ratio:.1f}× the lab value ({lab_tip_speed:.2f} m/s). "
                'Excessive shear may degrade product or cause foaming.',
                'Reduce impeller RPM to match lab tip speed (constant tip-speed '
                'scale-up rule) or validate shear sensitivity in intermediate scale.',
            ))
        elif tip_ratio < 0.4:
            flags.append(_flag(
                'LOW_TIP_SPEED',
                'warn',
                f"Production tip speed ({prod_tip_speed:.2f} m/s) is only "
                f"{tip_ratio:.1f}× the lab value ({lab_tip_speed:.2f} m/s). "
                'Under-mixing risks poor blend uniformity, heat-transfer hot-spots '
                'and slow reagent dispersion.',
                'Increase agitator speed or switch to a more efficient impeller '
                'geometry; consider geometric similarity scale-up criteria.',
            ))
    elif (lab_imp_d or lab_rpm or prod_imp_d or prod_rpm) and not (
        lab_tip_speed and prod_tip_speed
    ):
        flags.append(_flag(
            'MIXING_DATA_INCOMPLETE',
            'info',
            'Insufficient impeller/RPM data to calculate tip-speed comparison.',
            'Provide lab impeller_diameter_m + impeller_rpm AND vessel '
            'impeller_diameter_m + impeller_rpm for a mixing scale-up check.',
        ))

    heat_transfer['mixing'] = mixing_info

    # ------------------------------------------------------------------
    # 6. Exotherm at scale
    # ------------------------------------------------------------------
    exotherm_kj_kg = _f(lab.get('exotherm_kJ_kg'))
    if exotherm_kj_kg is not None and exotherm_kj_kg > 0:
        total_exotherm_kj = exotherm_kj_kg * target_batch_kg
        heat_transfer['total_exotherm_kJ'] = round(total_exotherm_kj, 1)

        # Adiabatic temperature rise estimate: Q = m * Cp * dT, Cp_water ~ 4.18 kJ/(kg·K)
        cp_kj_kgk = 4.18
        adiabatic_dt = total_exotherm_kj / (target_batch_kg * cp_kj_kgk)
        heat_transfer['adiabatic_temp_rise_K'] = round(adiabatic_dt, 1)

        sev = 'critical' if adiabatic_dt > 50 else 'warn' if adiabatic_dt > 20 else 'info'
        flags.append(_flag(
            'EXOTHERM_AT_SCALE',
            sev,
            f"Total exotherm at {target_batch_kg:.1f} kg scale: "
            f"{total_exotherm_kj:.0f} kJ "
            f"(adiabatic ΔT ≈ {adiabatic_dt:.0f} K with Cp ≈ 4.18 kJ/kg·K).",
            'Perform a reaction-calorimetry (RC1) study before scale-up; '
            'confirm jacket cooling duty exceeds peak heat-generation rate; '
            'define an addition rate limit; install a relief device sized for '
            'runaway scenario.',
        ))
        if sev in ('critical', 'warn'):
            hold_points.append(
                'HOLD: Calorimetry data (RC1/μRC) required before exothermic step at scale.'
            )

    # ------------------------------------------------------------------
    # 7. Generic scale-up hold points (always present)
    # ------------------------------------------------------------------
    if scaling_factor is not None and scaling_factor > 10:
        hold_points.append(
            f'HOLD: Large scale-up factor ({scaling_factor:.0f}×) — '
            'consider an intermediate pilot scale before full production.'
        )
    hold_points.append('HOLD: Verify IPC (in-process control) limits transfer to new scale.')
    hold_points.append('HOLD: Confirm solvent/reagent delivery rates are compatible with addition control at scale.')

    # Deduplicate hold points while preserving order
    seen: set[str] = set()
    dedup_holds: list[str] = []
    for hp in hold_points:
        if hp not in seen:
            seen.add(hp)
            dedup_holds.append(hp)

    # Sort flags by severity
    flags.sort(key=lambda f: -_SEV_RANK.get(f['severity'], 0))

    return {
        'scaling_factor': round(scaling_factor, 4) if scaling_factor is not None else None,
        'lab_batch_kg': lab_batch_kg,
        'target_batch_kg': target_batch_kg,
        'scaled_charges': scaled_charges,
        'volume_fit': volume_fit,
        'heat_transfer': heat_transfer,
        'risk_flags': flags,
        'hold_points': dedup_holds,
        'engine': 'tt_scale/deterministic-v1',
        'disclaimer': (
            'Heuristic Tech-Transfer guidance only — not a validated engineering '
            'specification. Confirm all outputs against full process-safety review, '
            'heat-balance calculations and site SOPs before proceeding to scale.'
        ),
    }
