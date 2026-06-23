"""Production readiness engine unit tests."""
from __future__ import annotations

import pytest

from shared import production_readiness as pr


class TestProductionReadinessShape:
    """Tests that the readiness report has the expected structure."""

    def test_check_readiness_returns_structure(self, monkeypatch):
        # Stub DB-dependent helpers to keep test pure.
        monkeypatch.setattr(pr, '_bmr_demand', lambda p, t: [{'material': 'A', 'required_kg': 10.0}])
        monkeypatch.setattr(pr, '_inventory_stock', lambda m: 50.0)
        monkeypatch.setattr(pr, '_equipment_for_product', lambda p, t: [{'equipment_code': 'R1', 'equipment_name': 'Reactor 1', 'capacity': 200}])
        monkeypatch.setattr(pr, '_manpower_status', lambda: {'total': 5, 'available': 4, 'shortfall': 0})
        monkeypatch.setattr(pr, '_qc_capacity', lambda: {'pending_samples': 5, 'stretched': False})
        monkeypatch.setattr(pr, '_bmr_document_status', lambda p: {'has_approved_bmr': True, 'bmr_id': 1, 'bmr_status': 'approved'})
        monkeypatch.setattr(pr, '_occupied_equipment_conflicts', lambda s=None: [])

        result = pr.check_readiness('Fluconazole', 100.0)
        assert result['ok'] is True
        assert result['overall_ready'] is True
        assert result['readiness_score'] == result['max_score']
        assert 'raw_materials' in result['checks']
        assert 'equipment' in result['checks']
        assert 'manpower' in result['checks']
        assert 'qc' in result['checks']
        assert 'documents' in result['checks']
        assert result['blockers'] == []

    def test_rm_shortfall_blocks(self, monkeypatch):
        monkeypatch.setattr(pr, '_bmr_demand', lambda p, t: [{'material': 'A', 'required_kg': 100.0}])
        monkeypatch.setattr(pr, '_inventory_stock', lambda m: 20.0)
        monkeypatch.setattr(pr, '_equipment_for_product', lambda p, t: [{'equipment_code': 'R1', 'equipment_name': 'Reactor 1', 'capacity': 200}])
        monkeypatch.setattr(pr, '_manpower_status', lambda: {'total': 5, 'available': 4, 'shortfall': 0})
        monkeypatch.setattr(pr, '_qc_capacity', lambda: {'pending_samples': 5, 'stretched': False})
        monkeypatch.setattr(pr, '_bmr_document_status', lambda p: {'has_approved_bmr': True, 'bmr_id': 1, 'bmr_status': 'approved'})
        monkeypatch.setattr(pr, '_occupied_equipment_conflicts', lambda s=None: [])

        result = pr.check_readiness('X', 100.0)
        assert result['overall_ready'] is False
        assert any('Need 100' in b and 'kg' in b for b in result['blockers'])

    def test_manpower_shortfall_blocks(self, monkeypatch):
        monkeypatch.setattr(pr, '_bmr_demand', lambda p, t: [])
        monkeypatch.setattr(pr, '_inventory_stock', lambda m: 0.0)
        monkeypatch.setattr(pr, '_equipment_for_product', lambda p, t: [{'equipment_code': 'R1', 'equipment_name': 'Reactor 1', 'capacity': 200}])
        monkeypatch.setattr(pr, '_manpower_status', lambda: {'total': 2, 'available': 1, 'shortfall': 3})
        monkeypatch.setattr(pr, '_qc_capacity', lambda: {'pending_samples': 5, 'stretched': False})
        monkeypatch.setattr(pr, '_bmr_document_status', lambda p: {'has_approved_bmr': True, 'bmr_id': 1, 'bmr_status': 'approved'})
        monkeypatch.setattr(pr, '_occupied_equipment_conflicts', lambda s=None: [])

        result = pr.check_readiness('Y', 100.0)
        assert result['checks']['manpower']['ok'] is False
        assert any('Manpower shortfall' in b for b in result['blockers'])

    def test_qc_stretched_blocks(self, monkeypatch):
        monkeypatch.setattr(pr, '_bmr_demand', lambda p, t: [])
        monkeypatch.setattr(pr, '_inventory_stock', lambda m: 0.0)
        monkeypatch.setattr(pr, '_equipment_for_product', lambda p, t: [])
        monkeypatch.setattr(pr, '_manpower_status', lambda: {'total': 5, 'available': 4, 'shortfall': 0})
        monkeypatch.setattr(pr, '_qc_capacity', lambda: {'pending_samples': 25, 'stretched': True})
        monkeypatch.setattr(pr, '_bmr_document_status', lambda p: {'has_approved_bmr': True, 'bmr_id': 1, 'bmr_status': 'approved'})
        monkeypatch.setattr(pr, '_occupied_equipment_conflicts', lambda s=None: [])

        result = pr.check_readiness('Z', 100.0)
        assert result['checks']['qc']['ok'] is False
        assert any('QC stretched' in b for b in result['blockers'])

    def test_missing_bmr_blocks(self, monkeypatch):
        monkeypatch.setattr(pr, '_bmr_demand', lambda p, t: [])
        monkeypatch.setattr(pr, '_inventory_stock', lambda m: 0.0)
        monkeypatch.setattr(pr, '_equipment_for_product', lambda p, t: [])
        monkeypatch.setattr(pr, '_manpower_status', lambda: {'total': 5, 'available': 4, 'shortfall': 0})
        monkeypatch.setattr(pr, '_qc_capacity', lambda: {'pending_samples': 5, 'stretched': False})
        monkeypatch.setattr(pr, '_bmr_document_status', lambda p: {'has_approved_bmr': False, 'bmr_id': None, 'bmr_status': None})
        monkeypatch.setattr(pr, '_occupied_equipment_conflicts', lambda s=None: [])

        result = pr.check_readiness('W', 100.0)
        assert result['checks']['documents']['ok'] is False
        assert any('Approved BMR' in b for b in result['blockers'])
