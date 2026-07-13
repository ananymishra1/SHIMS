"""QC/QA deterministic engine tests."""
from __future__ import annotations

import pytest

from shared import qa_qc


class TestDeviationClassifier:
    def test_critical_contamination(self):
        result = qa_qc.classify_deviation('Product mix-up and contamination in dispensing area')
        assert result['severity'] == 'critical'
        assert result['category'] == 'material'
        assert result['recommended_capa'] is True

    def test_major_oos(self):
        result = qa_qc.classify_deviation('HPLC assay out of specification for batch FLC-001')
        assert result['severity'] == 'major'
        assert result['category'] == 'oos'
        assert result['recommended_capa'] is True

    def test_minor_documentation(self):
        result = qa_qc.classify_deviation('Typo in logbook page number, cosmetic formatting issue')
        assert result['severity'] == 'minor'
        assert result['category'] == 'documentation'
        assert result['recommended_capa'] is False

    def test_data_integrity(self):
        result = qa_qc.classify_deviation('Missing audit trail entries and falsified data in stability study')
        assert result['severity'] == 'critical'
        assert result['category'] == 'data_integrity'


class TestAuditReadinessShape:
    def test_score_in_range(self, monkeypatch):
        monkeypatch.setattr(qa_qc, 'ensure_qa_qc_schema', lambda: None)
        monkeypatch.setattr(qa_qc.db, 'one', lambda *a, **k: {'c': 5})
        result = qa_qc.audit_readiness_score()
        assert 0 <= result['audit_readiness_score'] <= 100
        assert result['rating'] in ('strong', 'acceptable', 'at_risk')
        assert 'details' in result
        assert 'next_actions' in result
