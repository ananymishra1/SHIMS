"""Tech Transfer unit tests — deterministic scale math + data-layer helpers."""
from __future__ import annotations

import pytest

from shared import tech_transfer as tt
from shared.tt_scale import scale_batch


class TestTTScale:
    """Tests for the deterministic scale-up engine."""

    def test_linear_scale_factor(self):
        lab = {'batch_kg': 1.0, 'density_kg_L': 1.0, 'materials': [{'name': 'A', 'charge_kg': 0.5}]}
        vessel = {'working_volume_L': 150}
        result = scale_batch(lab, 100.0, vessel)
        assert result['scaling_factor'] == 100.0
        assert result['scaled_charges'][0]['scaled_kg'] == 50.0

    def test_vessel_overfill_flag(self):
        lab = {'batch_kg': 1.0, 'density_kg_L': 1.0, 'materials': []}
        vessel = {'working_volume_L': 50}
        result = scale_batch(lab, 100.0, vessel)
        assert result['volume_fit']['fits'] is False
        codes = [f['code'] for f in result['risk_flags']]
        assert 'VESSEL_OVERFILL' in codes

    def test_av_drop_flag(self):
        lab = {'batch_kg': 1.0, 'density_kg_L': 1.0, 'vessel_diameter_m': 0.1, 'vessel_height_m': 0.15, 'materials': []}
        vessel = {'working_volume_L': 1000, 'diameter_m': 1.0, 'height_m': 1.5}
        result = scale_batch(lab, 1000.0, vessel)
        assert any(f['code'] == 'LARGE_AV_DROP' for f in result['risk_flags'])

    def test_exotherm_at_scale(self):
        lab = {'batch_kg': 1.0, 'density_kg_L': 1.0, 'exotherm_kJ_kg': 250, 'materials': []}
        vessel = {'working_volume_L': 1500}
        result = scale_batch(lab, 1000.0, vessel)
        assert any(f['code'] == 'EXOTHERM_AT_SCALE' for f in result['risk_flags'])
        assert result['heat_transfer']['total_exotherm_kJ'] > 0

    def test_high_tip_speed_flag(self):
        lab = {'batch_kg': 1.0, 'density_kg_L': 1.0, 'impeller_diameter_m': 0.05, 'impeller_rpm': 100, 'materials': []}
        vessel = {'working_volume_L': 100, 'impeller_diameter_m': 0.5, 'impeller_rpm': 300}
        result = scale_batch(lab, 100.0, vessel)
        assert any(f['code'] == 'HIGH_TIP_SPEED' for f in result['risk_flags'])


class TestTTVesselSpec:
    """Tests for the equipment → tt_scale vessel mapping."""

    def test_vessel_to_spec_estimates_geometry(self):
        vessel = {'capacity': 1000, 'unit': 'L'}
        spec = tt._vessel_to_spec(vessel)
        assert spec['working_volume_L'] == 1000
        assert spec['diameter_m'] is not None
        assert spec['height_m'] is not None

    def test_vessel_to_spec_empty(self):
        assert tt._vessel_to_spec(None) == {}


class TestTTExperimentDescriptor:
    """Tests for building a lab descriptor from experiment detail."""

    def test_descriptor_uses_output_qty(self):
        detail = {
            'experiment': {'id': 1, 'product_name': 'X'},
            'stages': [{'output_qty': 5.0}],
            'raw_materials': [{'name': 'A', 'quantity': 10.0, 'unit_type': 'mass'}],
        }
        lab = tt._experiment_to_lab_descriptor(1, detail)
        assert lab['batch_kg'] == 5.0
        assert lab['materials'][0]['charge_kg'] == 10.0

    def test_descriptor_fallback_to_rm_sum(self):
        detail = {
            'experiment': {'id': 2, 'product_name': 'Y'},
            'stages': [],
            'raw_materials': [{'name': 'B', 'quantity': 3.0, 'unit_type': 'mass'}],
        }
        lab = tt._experiment_to_lab_descriptor(2, detail)
        assert lab['batch_kg'] == 3.0


class TestTTProjectValidation:
    """Tests for payload validation helpers."""

    def test_create_project_rejects_missing_target(self):
        result = tt.create_tt_project(1, {})
        assert result['ok'] is False
        assert 'target_batch_kg' in result['error']

    def test_create_project_rejects_invalid_experiment(self):
        # exp_id -1 won't exist in DB, but the schema/data access will return no experiment.
        result = tt.create_tt_project(-1, {'target_batch_kg': 100.0})
        assert result['ok'] is False
