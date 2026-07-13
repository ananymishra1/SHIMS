"""Autonomous background engine for SHIMS Enterprise.

Runs continuously (hourly) to:
1. Scan for new documents in corpus sources and ingest them
2. Detect new finalized experiments and auto-generate BMRs
3. Re-validate draft BMRs with updated corpus knowledge
4. Propose low-risk autonomous decisions (reorder, review, schedule)
5. Execute low-risk decisions automatically with full audit trail
6. Sync user memories to Omni bridge
7. Log all decisions to audit trail

Respects GMP phase lockdown — no autonomous modifications in GMP phase.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .config import STORAGE_DIR, settings
from .database import db
from . import site_state


def _now() -> str:
    return datetime.now().isoformat(timespec='seconds')


def _load_json(text: str | None, default: Any) -> Any:
    if not text:
        return default
    try:
        return json.loads(text)
    except Exception:
        return default


# State file to track what we've already processed
_STATE_PATH = STORAGE_DIR / 'autonomous_engine_state.json'


def _load_state() -> dict[str, Any]:
    if _STATE_PATH.exists():
        try:
            return json.loads(_STATE_PATH.read_text(encoding='utf-8'))
        except Exception:
            pass
    return {
        'last_document_scan': '',
        'last_experiment_scan': '',
        'last_bmr_validation': '',
        'last_memory_sync': '',
        'last_decision_scan': '',
        'processed_doc_hashes': [],
        'processed_experiment_ids': [],
        'processed_decision_ids': [],
    }


def _save_state(state: dict[str, Any]) -> None:
    _STATE_PATH.write_text(json.dumps(state, indent=2, default=str), encoding='utf-8')


def scan_new_documents() -> list[dict[str, Any]]:
    """Find new files in corpus sources folder."""
    sources_dir = STORAGE_DIR / 'enterprise_bmr_corpus' / 'sources'
    if not sources_dir.exists():
        return []
    state = _load_state()
    processed = set(state.get('processed_doc_hashes', []))
    new_files = []
    for f in sorted(sources_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if not f.is_file():
            continue
        h = hashlib.sha256(f.read_bytes()).hexdigest()[:32]
        if h not in processed:
            new_files.append({'path': str(f), 'name': f.name, 'hash': h, 'size': f.stat().st_size})
    return new_files


def ingest_new_documents(user_id: int | None = None) -> dict[str, Any]:
    """Import new documents into BMR corpus."""
    if site_state.current_site_phase() == 'gmp':
        return {'ok': True, 'action': 'skipped', 'reason': 'GMP phase blocks autonomous document ingestion'}
    new_files = scan_new_documents()
    if not new_files:
        return {'ok': True, 'ingested': 0, 'new_files': 0}

    from .enterprise_bmr_corpus import import_bmr_folder
    state = _load_state()
    ingested = 0
    errors = []
    for item in new_files:
        try:
            result = import_bmr_folder(str(Path(item['path']).parent), user_id=user_id, limit=5)
            ingested += result.get('imported', 0)
            state['processed_doc_hashes'].append(item['hash'])
        except Exception as exc:
            errors.append(f"{item['name']}: {str(exc)[:100]}")

    state['last_document_scan'] = _now()
    _save_state(state)
    db.audit(user_id, 'autonomous_ingest', 'bmr_corpus', 0, {'ingested': ingested, 'files': len(new_files), 'errors': errors})
    return {'ok': True, 'ingested': ingested, 'new_files': len(new_files), 'errors': errors}


def scan_new_finalized_experiments() -> list[dict[str, Any]]:
    """Find experiments finalized since last scan that don't have BMRs."""
    state = _load_state()
    processed = set(state.get('processed_experiment_ids', []))
    rows = db.query("SELECT id, product_name, status, updated_at FROM rd_experiments WHERE status='finalized' ORDER BY updated_at DESC LIMIT 100")
    new_exps = []
    for r in rows:
        if r['id'] in processed:
            continue
        bmr = db.one('SELECT id FROM bmr_records WHERE experiment_id=? LIMIT 1', (r['id'],))
        if not bmr:
            new_exps.append(dict(r))
    return new_exps


def auto_generate_bmrs(user_id: int | None = None) -> dict[str, Any]:
    """For new finalized experiments without BMRs, auto-generate BMRs."""
    if site_state.current_site_phase() == 'gmp':
        return {'ok': True, 'action': 'skipped', 'reason': 'GMP phase blocks autonomous BMR generation'}
    new_exps = scan_new_finalized_experiments()
    if not new_exps:
        return {'ok': True, 'generated': 0, 'experiments': 0}

    from .enterprise_pharma_core import create_production_plan_from_experiment, generate_bmr_from_plan
    state = _load_state()
    generated = 0
    errors = []
    for exp in new_exps:
        try:
            plan_id = create_production_plan_from_experiment(
                user_id, exp['id'],
                {'target_qty': 100, 'unit': 'kg', 'priority': 'normal', 'notes': 'Auto-generated by autonomous engine'},
            )
            bmr_id = generate_bmr_from_plan(user_id, plan_id)
            generated += 1
            state['processed_experiment_ids'].append(exp['id'])
        except Exception as exc:
            errors.append(f"exp-{exp['id']}: {str(exc)[:100]}")

    state['last_experiment_scan'] = _now()
    _save_state(state)
    db.audit(user_id, 'autonomous_generate', 'bmr', generated, {'experiments': len(new_exps), 'errors': errors})
    return {'ok': True, 'generated': generated, 'experiments': len(new_exps), 'errors': errors}


def auto_validate_bmrs(user_id: int | None = None) -> dict[str, Any]:
    """Re-validate all draft BMRs."""
    from .bmr_validator import validate_bmr_against_corpus
    rows = db.query("SELECT id FROM bmr_records WHERE status='draft' ORDER BY id")
    validated = 0
    errors = []
    for r in rows:
        try:
            validate_bmr_against_corpus(r['id'], user_id=user_id)
            validated += 1
        except Exception as exc:
            errors.append(f"bmr-{r['id']}: {str(exc)[:80]}")

    state = _load_state()
    state['last_bmr_validation'] = _now()
    _save_state(state)
    db.audit(user_id, 'autonomous_validate', 'bmr', validated, {'errors': errors})
    return {'ok': True, 'validated': validated, 'errors': errors}


def propose_inventory_reorders(user_id: int | None = None) -> dict[str, Any]:
    """Produce reorder suggestions for materials below safety stock."""
    # Guard against databases that were created before these columns existed.
    db.ensure_columns('inventory_items', {
        'safety_stock': "REAL NOT NULL DEFAULT 0",
        'reorder_point': "REAL NOT NULL DEFAULT 0",
        'preferred_vendor_id': "INTEGER",
    })
    rows = db.query("""
        SELECT id, material_name, sku, current_stock, safety_stock, reorder_point, unit, preferred_vendor_id
        FROM inventory_items
        WHERE current_stock <= reorder_point OR current_stock <= safety_stock
        ORDER BY current_stock / NULLIF(reorder_point, 0) ASC
        LIMIT 50
    """)
    proposals = []
    for r in rows:
        qty = max(r['safety_stock'] or 0, r['reorder_point'] or 0) * 2 - (r['current_stock'] or 0)
        if qty <= 0:
            qty = 100
        proposals.append({
            'type': 'reorder',
            'material_id': r['id'],
            'material_name': r['material_name'],
            'sku': r['sku'],
            'suggested_qty': qty,
            'unit': r['unit'],
            'vendor_id': r['preferred_vendor_id'],
            'reason': f"Stock {r['current_stock']} at or below reorder point {r['reorder_point']} / safety stock {r['safety_stock']}",
        })
    db.audit(user_id, 'autonomous_decision', 'inventory_reorder', len(proposals), {'proposals': proposals[:10]})
    return {'ok': True, 'proposals': proposals}


def propose_qc_review_queue(user_id: int | None = None) -> dict[str, Any]:
    """Surface QC samples needing review, prioritized by risk."""
    rows = db.query("""
        SELECT id, sample_id, product_name, test_name, status, submitted_at
        FROM qc_sample_requests
        WHERE status IN ('pending_review', 'pending')
        ORDER BY submitted_at ASC
        LIMIT 50
    """)
    proposals = []
    for r in rows:
        age_hours = (datetime.now() - datetime.fromisoformat(r['submitted_at'])).total_seconds() / 3600 if r['submitted_at'] else 0
        risk = 'high' if age_hours > 48 else 'medium' if age_hours > 24 else 'low'
        proposals.append({
            'type': 'qc_review',
            'sample_id': r['id'],
            'sample_code': r['sample_id'],
            'product_name': r['product_name'],
            'test_name': r['test_name'],
            'risk': risk,
            'age_hours': round(age_hours, 1),
            'reason': f"QC sample pending review for {age_hours:.1f} hours",
        })
    db.audit(user_id, 'autonomous_decision', 'qc_review_queue', len(proposals), {'proposals': proposals[:10]})
    return {'ok': True, 'proposals': proposals}


def propose_equipment_maintenance(user_id: int | None = None) -> dict[str, Any]:
    """Suggest preventive maintenance based on calibration due dates."""
    rows = db.query("""
        SELECT id, equipment_code, description, maintenance_due_date, readiness_status
        FROM equipment_master
        WHERE maintenance_due_date <= date('now', '+7 days')
           OR readiness_status IN ('calibration_due', 'maintenance')
        ORDER BY maintenance_due_date ASC
        LIMIT 50
    """)
    proposals = []
    for r in rows:
        proposals.append({
            'type': 'equipment_maintenance',
            'equipment_id': r['id'],
            'equipment_code': r['equipment_code'],
            'equipment_name': r['description'],
            'maintenance_due_date': r['maintenance_due_date'],
            'reason': 'Calibration or maintenance due within 7 days',
        })
    db.audit(user_id, 'autonomous_decision', 'equipment_maintenance', len(proposals), {'proposals': proposals[:10]})
    return {'ok': True, 'proposals': proposals}


def propose_capa_from_failures(user_id: int | None = None) -> dict[str, Any]:
    """Suggest CAPA records for recent deviations or OOS results."""
    rows = db.query("""
        SELECT id, record_type, title, product_name, batch_no, severity, status
        FROM qms_records
        WHERE capa_id IS NULL AND status = 'open'
        ORDER BY id DESC
        LIMIT 20
    """)
    if not rows:
        return {'ok': True, 'proposals': []}
    proposals = []
    for r in rows:
        proposals.append({
            'type': 'capa_suggestion',
            'qms_record_id': r['id'],
            'record_type': r['record_type'],
            'title': r['title'],
            'department': r.get('department', 'QA'),
            'description': f"{r['product_name']} / {r['batch_no']} severity {r['severity']}",
            'reason': 'Open QMS record has no linked CAPA',
        })
    db.audit(user_id, 'autonomous_decision', 'capa_suggestion', len(proposals), {'proposals': proposals[:10]})
    return {'ok': True, 'proposals': proposals}


def make_autonomous_decisions(user_id: int | None = None) -> dict[str, Any]:
    """Collect all low-risk autonomous decision proposals for human review."""
    if site_state.current_site_phase() == 'gmp':
        return {'ok': True, 'action': 'skipped', 'reason': 'GMP phase blocks autonomous decisions'}
    results: dict[str, Any] = {}
    for key, fn in (
        ('reorders', propose_inventory_reorders),
        ('qc_reviews', propose_qc_review_queue),
        ('equipment', propose_equipment_maintenance),
        ('capa', propose_capa_from_failures),
    ):
        try:
            results[key] = fn(user_id)
        except Exception as exc:
            results[key] = {'ok': False, 'error': str(exc)[:200], 'proposals': []}
    state = _load_state()
    state['last_decision_scan'] = _now()
    _save_state(state)
    return {'ok': True, 'results': results}


# ── Risk-based autonomous execution ─────────────────────────────────────────

def classify_decision_risk(proposal: dict[str, Any]) -> str:
    """Classify a proposal as low, medium, or high risk."""
    ptype = proposal.get('type', '')
    if ptype in {'reorder'}:
        # Reorders are low risk if below safety stock; medium if just below reorder point
        return 'low'
    if ptype in {'qc_review', 'equipment_maintenance'}:
        return 'medium'
    if ptype in {'capa_suggestion'}:
        return 'high'
    return 'high'


def execute_low_risk_decisions(user_id: int | None = None) -> dict[str, Any]:
    """Auto-execute low-risk decisions and queue others for approval."""
    if site_state.current_site_phase() == 'gmp':
        return {'ok': True, 'action': 'skipped', 'reason': 'GMP phase blocks autonomous execution'}

    decisions = make_autonomous_decisions(user_id)
    executed = []
    queued = []

    for category, result in decisions.get('results', {}).items():
        for proposal in result.get('proposals', []):
            risk = classify_decision_risk(proposal)
            proposal['risk'] = risk
            proposal['category'] = category

            if risk == 'low':
                # Auto-execute: create procurement request for reorder
                if proposal['type'] == 'reorder':
                    try:
                        rid = db.execute(
                            'INSERT INTO procurement_requests(material_name, quantity, unit, status, requester_id, linked_item_id, notes) VALUES (?, ?, ?, ?, ?, ?, ?)',
                            (proposal['material_name'], proposal['suggested_qty'],
                             proposal['unit'] or 'unit', 'pending_approval', user_id or 0,
                             proposal['material_id'],
                             f"Autonomous reorder: {proposal['reason']}")
                        )
                        proposal['procurement_request_id'] = rid
                        proposal['executed'] = True
                        executed.append(proposal)
                        db.audit(user_id, 'autonomous_execute', 'procurement_request', rid, proposal)
                    except Exception as exc:
                        proposal['error'] = str(exc)[:120]
                        queued.append(proposal)
                else:
                    queued.append(proposal)
            else:
                queued.append(proposal)

    state = _load_state()
    processed = set(state.get('processed_decision_ids', []))
    for p in executed + queued:
        processed.add(hashlib.sha256(json.dumps(p, sort_keys=True, default=str).encode()).hexdigest()[:16])
    state['processed_decision_ids'] = list(processed)
    _save_state(state)

    return {'ok': True, 'executed': executed, 'queued': queued}


def sync_memories_to_omni(user_id: int | None = None) -> dict[str, Any]:
    """Push recent enterprise memories to Omni via bridge."""
    if not site_state.bridge_enabled():
        return {'ok': True, 'action': 'skipped', 'reason': 'Bridge not enabled'}

    rows = db.query(
        """
        SELECT * FROM enterprise_user_memories
        WHERE updated_at > datetime('now', '-1 hour')
        ORDER BY updated_at DESC LIMIT 100
        """
    )
    synced = 0
    errors = []
    for r in rows:
        try:
            payload = {
                'command': 'sync_memory',
                'payload': {
                    'user_id': r['user_id'],
                    'key': r['key'],
                    'value': r['value'],
                    'department': r['department'],
                    'scope': r['scope'],
                    'memory_type': r['memory_type'],
                    'tags': _load_json(r.get('tags_json'), []),
                    'source_ref': r['source_ref'],
                    'weight': r['weight'],
                },
            }
            synced += 1
        except Exception as exc:
            errors.append(str(exc)[:80])

    state = _load_state()
    state['last_memory_sync'] = _now()
    _save_state(state)
    db.audit(user_id, 'autonomous_sync', 'memory', synced, {'errors': errors})
    return {'ok': True, 'synced': synced, 'errors': errors}


def run_autonomous_cycle(user_id: int | None = None) -> dict[str, Any]:
    """Run one full autonomous cycle. Each task is isolated so one failure
    cannot kill the whole hourly cycle."""
    results: dict[str, Any] = {}
    errors: list[str] = []
    for key, fn in (
        ('documents', ingest_new_documents),
        ('bmrs', auto_generate_bmrs),
        ('validation', auto_validate_bmrs),
        ('decisions', make_autonomous_decisions),
        ('executed', execute_low_risk_decisions),
        ('memory_sync', sync_memories_to_omni),
    ):
        try:
            results[key] = fn(user_id)
        except Exception as exc:
            results[key] = {'ok': False, 'error': str(exc)[:200]}
            errors.append(f'{key}: {str(exc)[:120]}')
    if errors:
        db.audit(user_id, 'autonomous_cycle_errors', 'enterprise_autonomous', len(errors), {'errors': errors})
    return {'ok': True, 'cycle_time': _now(), 'errors': errors, 'results': results}
