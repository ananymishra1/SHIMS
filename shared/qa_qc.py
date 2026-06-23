"""QC/QA source-of-truth engine — deterministic audit-readiness + deviation classifier.

Design goals:
  • LIMS samples are the single sampling source; each sample links to an R&D
    experiment OR a plant batch/work order.
  • Deviation classification is rule-first (severity + category) with optional
    LLM enrichment later.
  • Audit-readiness score is computed from SOP coverage, training effectiveness,
    open deviations/CAPA, and signed records.
"""
from __future__ import annotations

import json
from typing import Any

from .database import db
from .enterprise_expansion import EXPANSION_SCHEMA


def ensure_qa_qc_schema() -> None:
    """Idempotent schema for QC/QA source-of-truth linking."""
    db.execute(EXPANSION_SCHEMA)
    db.ensure_columns('lims_samples', {
        'source_type': "TEXT DEFAULT 'batch'",  # 'batch' | 'experiment' | 'stability'
        'source_id': 'INTEGER',
        'experiment_id': 'INTEGER',
        'work_order_id': 'INTEGER',
    })
    db.ensure_columns('qms_records', {
        'source_type': "TEXT DEFAULT 'general'",
        'source_id': 'INTEGER',
        'classification': 'TEXT',  # deviation, capa, change_control, oos
        'severity': "TEXT DEFAULT 'minor'",  # minor, major, critical
        'root_cause_category': 'TEXT',
        'status': "TEXT DEFAULT 'open'",
        'assigned_to': 'INTEGER',
        'due_date': 'TEXT',
        'closed_at': 'TEXT',
    })


# ═══════════════════════════════════════════════════════════════════════════════
# Deviation classifier
# ═══════════════════════════════════════════════════════════════════════════════

_CRITICAL_HINTS = (
    'contamination', 'mix-up', 'wrong product', 'adulteration', 'sterility failure',
    'environmental exceedance', 'unauthorized change', 'data integrity', 'falsified'
)
_MAJOR_HINTS = (
    'out of spec', 'oos', 'failed test', 'yield deviation', 'equipment malfunction',
    'temperature excursion', 'hold time exceeded', 'cleaning failure', 'documentation error'
)
_MINOR_HINTS = (
    'typo', 'label smudge', 'cosmetic', 'formatting', 'minor update', 'clarification'
)

_CATEGORY_MAP = {
    'oos': ('Out-of-Specification', ['out of spec', 'oos', 'failed test', 'specification']),
    'yield': ('Yield deviation', ['yield', 'recovery', 'loss']),
    'equipment': ('Equipment / facility', ['equipment', 'instrument', 'calibration', 'utility']),
    'documentation': ('Documentation', ['document', 'sop', 'record', 'signature', 'typo']),
    'material': ('Material / RM', ['raw material', 'rm ', 'vendor', 'supplier', 'grn', 'contamination', 'mix-up']),
    'personnel': ('Personnel / training', ['training', 'operator', 'personnel']),
    'environmental': ('Environmental / EHS', ['effluent', 'emission', 'scrubber', 'cetp', 'waste']),
    'data_integrity': ('Data integrity', ['data integrity', 'audit trail', 'falsified', 'missing data']),
}


def classify_deviation(text: str) -> dict[str, Any]:
    """Rule-based deviation classification. Returns severity + category + rationale."""
    low = (text or '').lower()
    if any(h in low for h in _CRITICAL_HINTS):
        severity = 'critical'
    elif any(h in low for h in _MAJOR_HINTS):
        severity = 'major'
    elif any(h in low for h in _MINOR_HINTS):
        severity = 'minor'
    else:
        severity = 'minor'

    category = 'general'
    category_label = 'General / Other'
    for cat, (label, hints) in _CATEGORY_MAP.items():
        if any(h in low for h in hints):
            category = cat
            category_label = label
            break

    return {
        'severity': severity,
        'category': category,
        'category_label': category_label,
        'rationale': f"Keyword match placed this in '{category_label}' with {severity} severity.",
        'recommended_capa': severity in ('major', 'critical'),
        'recommended_reviewers': _reviewers_for(severity, category),
    }


def _reviewers_for(severity: str, category: str) -> list[str]:
    base = ['qa']
    if severity in ('major', 'critical'):
        base += ['qa_head', 'regulatory']
    if category in ('equipment', 'environmental'):
        base += ['engineering', 'production']
    if category == 'data_integrity':
        base += ['qa_head', 'regulatory']
    return list(dict.fromkeys(base))


# ═══════════════════════════════════════════════════════════════════════════════
# Audit-readiness score
# ═══════════════════════════════════════════════════════════════════════════════


def audit_readiness_score(product_name: str | None = None) -> dict[str, Any]:
    """Compute a deterministic audit-readiness score across QA pillars."""
    ensure_qa_qc_schema()

    score = 100
    details: dict[str, Any] = {}

    # SOP coverage: count approved SOPs vs active products.
    try:
        sop_total = db.one('SELECT COUNT(*) as c FROM documents WHERE doc_type="sop"')['c']
        sop_approved = db.one('SELECT COUNT(*) as c FROM documents WHERE doc_type="sop" AND status="approved"')['c']
    except Exception:
        sop_total = sop_approved = 0
    sop_pct = (sop_approved / sop_total * 100) if sop_total else 0
    details['sop'] = {'total': sop_total, 'approved': sop_approved, 'pct': round(sop_pct, 1)}
    score -= max(0, int((100 - sop_pct) * 0.15))

    # Training effectiveness: completed + effective assignments.
    try:
        total_training = db.one('SELECT COUNT(*) as c FROM training_assignments')['c']
        effective = db.one('SELECT COUNT(*) as c FROM training_assignments WHERE status="completed" AND effectiveness_check="effective"')['c']
    except Exception:
        total_training = effective = 0
    train_pct = (effective / total_training * 100) if total_training else 0
    details['training'] = {'total': total_training, 'effective': effective, 'pct': round(train_pct, 1)}
    score -= max(0, int((100 - train_pct) * 0.25))

    # Open deviations / CAPA / change controls.
    try:
        open_qms = db.one('SELECT COUNT(*) as c FROM qms_records WHERE status IN ("open","in_progress")')['c']
        overdue = db.one('SELECT COUNT(*) as c FROM qms_records WHERE status IN ("open","in_progress") AND due_date < date("now")')['c']
    except Exception:
        open_qms = overdue = 0
    details['qms'] = {'open': open_qms, 'overdue': overdue}
    score -= min(25, open_qms * 3)
    score -= min(15, overdue * 5)

    # LIMS: pending OOS / unreviewed results.
    try:
        pending_oos = db.one('SELECT COUNT(*) as c FROM lims_test_results WHERE status="oos" AND reviewed_by IS NULL')['c']
    except Exception:
        pending_oos = 0
    details['lims'] = {'pending_oos_unreviewed': pending_oos}
    score -= min(20, pending_oos * 5)

    # Electronic signatures on critical records (heuristic: at least one signed record).
    try:
        sig_count = db.one('SELECT COUNT(*) as c FROM electronic_signatures')['c']
    except Exception:
        sig_count = 0
    details['signatures'] = {'count': sig_count}
    if sig_count == 0:
        score -= 10

    score = max(0, min(100, score))

    return {
        'ok': True,
        'product_name': product_name,
        'audit_readiness_score': score,
        'rating': 'strong' if score >= 80 else 'acceptable' if score >= 60 else 'at_risk',
        'details': details,
        'next_actions': _audit_next_actions(details),
        'engine': 'qa_qc/audit-readiness-v1',
        'disclaimer': 'Heuristic audit-readiness index. A real regulatory audit requires document-by-document review.',
    }


def _audit_next_actions(details: dict[str, Any]) -> list[str]:
    actions: list[str] = []
    if details.get('sop', {}).get('pct', 100) < 100:
        actions.append('Finalize and approve all active SOPs')
    if details.get('training', {}).get('pct', 100) < 90:
        actions.append('Complete training effectiveness checks')
    if details.get('qms', {}).get('overdue', 0) > 0:
        actions.append('Close overdue deviations/CAPA/change controls')
    if details.get('lims', {}).get('pending_oos_unreviewed', 0) > 0:
        actions.append('Review and disposition pending OOS results')
    if details.get('signatures', {}).get('count', 0) == 0:
        actions.append('Apply electronic signatures to critical records')
    return actions


# ═══════════════════════════════════════════════════════════════════════════════
# Sample linking
# ═══════════════════════════════════════════════════════════════════════════════


def link_sample(sample_id: int, source_type: str, source_id: int) -> dict[str, Any]:
    """Link a LIMS sample to its source (experiment or work order/batch)."""
    ensure_qa_qc_schema()
    if source_type not in ('batch', 'experiment', 'stability'):
        return {'ok': False, 'error': 'source_type must be batch, experiment, or stability'}
    extra = {}
    if source_type == 'experiment':
        extra['experiment_id'] = source_id
    elif source_type == 'batch':
        extra['work_order_id'] = source_id
    db.execute(
        'UPDATE lims_samples SET source_type=?, source_id=?, experiment_id=?, work_order_id=? WHERE id=?',
        (source_type, source_id, extra.get('experiment_id'), extra.get('work_order_id'), sample_id),
    )
    return {'ok': True, 'sample_id': sample_id}


def samples_for_source(source_type: str, source_id: int) -> list[dict[str, Any]]:
    ensure_qa_qc_schema()
    rows = db.query(
        'SELECT * FROM lims_samples WHERE source_type=? AND source_id=? ORDER BY created_at DESC',
        (source_type, source_id),
    )
    return [dict(r) for r in rows]
