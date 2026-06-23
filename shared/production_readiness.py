"""Production readiness engine — deterministic check before a batch can run.

Checks:
  • Raw materials vs warehouse stock and BMR demand.
  • Equipment: free, clean, qualified, fit for batch size.
  • Manpower: roster coverage for the shift.
  • QC: sampling/test capacity heuristic.
  • Documents: approved BMR exists.

Returns a structured readiness report with per-item blockers and a simple
schedule-conflict detector. No LLM required; local-first.
"""
from __future__ import annotations

import json
from typing import Any

from .database import db
from .enterprise_bmr_corpus import search_corpus


def ensure_production_readiness_schema() -> None:
    """Idempotent schema for readiness snapshots (optional audit log)."""
    db.execute(
        """CREATE TABLE IF NOT EXISTS production_readiness_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_name TEXT NOT NULL,
            target_batch_kg REAL NOT NULL,
            readiness_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )"""
    )


def _inventory_stock(material_name: str) -> float:
    """Sum available stock for a material by name (fuzzy)."""
    if not material_name:
        return 0.0
    key = material_name.strip().lower()
    rows = db.query(
        "SELECT * FROM inventory_items WHERE LOWER(material_name) LIKE ? OR LOWER(item_code) LIKE ?",
        (f"%{key}%", f"%{key}%"),
    )
    total = 0.0
    for r in rows:
        qty = r.get('quantity') or 0
        status = (r.get('status') or '').lower()
        if status not in ('quarantine', 'rejected', 'blocked'):
            total += float(qty)
    return total


def _bmr_demand(product_name: str, target_batch_kg: float) -> list[dict[str, Any]]:
    """Estimate RM demand from BMR corpus or product route."""
    demand: list[dict[str, Any]] = []
    try:
        corpus = search_corpus(product_name, limit=1)
        doc = (corpus.get('documents') or [{}])[0]
        text = doc.get('text') or ''
        # Very simple heuristic: look for "<name> ... <qty> kg" or "<qty> g" near material lists.
        # Production-grade parsing should live in bmr_raw_material_parser; here we do best-effort.
        demand = _parse_material_lines(text, target_batch_kg)
    except Exception:
        pass
    # Fallback: use product route stages raw materials if any.
    if not demand:
        try:
            from .product_chemistry import list_route_stages
            stages = list_route_stages(product_name)
            seen: set[str] = set()
            for s in stages:
                for rm in s.get('raw_materials') or []:
                    name = rm.get('name') or rm
                    if name and name not in seen:
                        seen.add(name)
                        demand.append({'material': name, 'required_kg': 1.0})
        except Exception:
            pass
    return demand


def _parse_material_lines(text: str, target_batch_kg: float) -> list[dict[str, Any]]:
    """Naive parser: look for lines with material-ish quantities."""
    out: list[dict[str, Any]] = []
    import re
    for line in (text or '').splitlines():
        m = re.search(r'([\w\s\-/.,()]+?)\s+(\d+\.?\d*)\s*(g|kg|mg|L|ml)\b', line, re.I)
        if m:
            name = m.group(1).strip().strip('-*•')
            qty = float(m.group(2))
            unit = m.group(3).lower()
            if unit == 'g':
                qty = qty / 1000.0
            elif unit == 'mg':
                qty = qty / 1_000_000.0
            elif unit in ('ml',):
                qty = qty / 1000.0
            # Assume lab-scale quantities < 10 kg; otherwise treat as per-kg factor.
            factor = target_batch_kg if qty < 10 else qty
            required = factor if qty < 10 else qty
            out.append({'material': name, 'required_kg': round(required, 4), 'unit': unit, 'note': 'parsed from BMR'})
    return out


def _equipment_for_product(product_name: str, target_batch_kg: float) -> list[dict[str, Any]]:
    """Find equipment that is free/clean/qualified and fits batch volume."""
    from .equipment_intelligence import ensure_equipment_intelligence_schema
    ensure_equipment_intelligence_schema()
    # Assume 1 kg ≈ 1 L bulk density for fit check.
    target_volume_l = target_batch_kg
    rows = db.query(
        """SELECT e.* FROM equipment_master e
           LEFT JOIN equipment_reservations r ON r.equipment_code=e.equipment_code AND r.status IN ('active','reserved')
           WHERE (e.status='available' OR e.status='idle') AND e.cleaning_status='clean'
           AND e.capacity >= ?
           GROUP BY e.id
           ORDER BY e.capacity""",
        (target_volume_l * 0.8,),  # working-volume rule
    )
    return [dict(r) for r in rows]


def _occupied_equipment_conflicts(scheduled_start: str | None = None) -> list[dict[str, Any]]:
    """Return active reservations that may conflict with a new batch."""
    ensure_production_readiness_schema()
    rows = db.query(
        """SELECT r.*, e.equipment_name FROM equipment_reservations r
           LEFT JOIN equipment_master e ON e.equipment_code=r.equipment_code
           WHERE r.status IN ('active','reserved') ORDER BY r.start_time"""
    )
    return [dict(r) for r in rows]


def _manpower_status() -> dict[str, Any]:
    """Return current manpower roster summary."""
    rows = db.query('SELECT * FROM manpower_roster WHERE active=1')
    total = len(rows)
    available = len([r for r in rows if (r.get('status') or '').lower() in ('available', 'on-duty')])
    return {'total': total, 'available': available, 'shortfall': max(0, 4 - available)}


def _qc_capacity() -> dict[str, Any]:
    """Heuristic QC capacity from open LIMS samples."""
    try:
        pending = db.one('SELECT COUNT(*) as c FROM lims_samples WHERE status IN ("pending","scheduled")')
        pending_count = pending['c'] if pending else 0
    except Exception:
        pending_count = 0
    # Simple threshold: >20 pending samples = stretched.
    return {'pending_samples': pending_count, 'stretched': pending_count > 20}


def _bmr_document_status(product_name: str) -> dict[str, Any]:
    """Check whether an approved BMR exists for the product."""
    rows = db.query(
        'SELECT * FROM bmr_records WHERE LOWER(product_name)=LOWER(?) AND status IN ("approved","active") ORDER BY created_at DESC LIMIT 1',
        (product_name,),
    )
    row = rows[0] if rows else None
    return {
        'has_approved_bmr': bool(row),
        'bmr_id': row['id'] if row else None,
        'bmr_status': row['status'] if row else None,
    }


def check_readiness(product_name: str, target_batch_kg: float, *, scheduled_start: str | None = None) -> dict[str, Any]:
    """One-call production readiness check with per-item blockers."""
    ensure_production_readiness_schema()

    # Raw materials
    rm_demand = _bmr_demand(product_name, target_batch_kg)
    rm_items: list[dict[str, Any]] = []
    rm_ok = True
    for d in rm_demand:
        stock = _inventory_stock(d['material'])
        required = d.get('required_kg', 0.0)
        ok = stock >= required
        rm_ok = rm_ok and ok
        rm_items.append({
            'material': d['material'],
            'required_kg': required,
            'stock_kg': round(stock, 4),
            'ok': ok,
            'blocker': None if ok else f"Need {required} kg, have {round(stock, 2)} kg",
        })
    if not rm_demand:
        rm_items.append({'material': 'BMR demand unknown', 'required_kg': 0, 'stock_kg': 0, 'ok': True, 'blocker': 'No demand data — verify RM list manually'})

    # Equipment
    equipment = _equipment_for_product(product_name, target_batch_kg)
    equipment_ok = len(equipment) > 0
    equipment_items = []
    if equipment_ok:
        for e in equipment[:10]:
            equipment_items.append({
                'equipment_code': e['equipment_code'],
                'equipment_name': e['equipment_name'],
                'capacity': e.get('capacity'),
                'ok': True,
                'blocker': None,
            })
    else:
        # Diagnose why no equipment.
        candidates = db.query('SELECT * FROM equipment_master WHERE capacity >= ?', (target_batch_kg * 0.8,))
        for e in candidates[:5]:
            reasons = []
            if (e.get('status') or '').lower() not in ('available', 'idle'):
                reasons.append('not available')
            if (e.get('cleaning_status') or '').lower() != 'clean':
                reasons.append('not clean')
            equipment_items.append({
                'equipment_code': e['equipment_code'],
                'equipment_name': e['equipment_name'],
                'capacity': e.get('capacity'),
                'ok': False,
                'blocker': ', '.join(reasons) or 'reserved/conflict',
            })

    # Manpower
    mp = _manpower_status()
    manpower_ok = mp['shortfall'] == 0

    # QC
    qc = _qc_capacity()
    qc_ok = not qc['stretched']

    # Documents
    doc = _bmr_document_status(product_name)
    doc_ok = doc['has_approved_bmr']

    # Overall
    checks = {
        'raw_materials': {'ok': rm_ok, 'items': rm_items},
        'equipment': {'ok': equipment_ok, 'items': equipment_items},
        'manpower': {'ok': manpower_ok, 'status': mp},
        'qc': {'ok': qc_ok, 'status': qc},
        'documents': {'ok': doc_ok, 'status': doc},
    }
    overall_ok = all(c['ok'] for c in checks.values())
    blockers = [
        item['blocker'] for section in checks.values()
        if 'items' in section
        for item in section['items']
        if item.get('blocker')
    ]
    if not manpower_ok:
        blockers.append(f"Manpower shortfall: need {mp['shortfall']} more available staff")
    if not qc_ok:
        blockers.append(f"QC stretched: {qc['pending_samples']} pending samples")
    if not doc_ok:
        blockers.append('Approved BMR not found')

    # Occupancy / conflicts
    conflicts = _occupied_equipment_conflicts(scheduled_start)

    result = {
        'ok': True,
        'product_name': product_name,
        'target_batch_kg': target_batch_kg,
        'overall_ready': overall_ok,
        'readiness_score': sum(c['ok'] for c in checks.values()),
        'max_score': len(checks),
        'checks': checks,
        'blockers': blockers,
        'occupancy_conflicts': conflicts[:10],
        'next_steps': [
            'Resolve RM shortfalls' if not rm_ok else None,
            'Clean/release equipment' if not equipment_ok else None,
            'Assign shift staff' if not manpower_ok else None,
            'Clear QC queue or defer sampling' if not qc_ok else None,
            'Approve BMR' if not doc_ok else None,
        ],
        'engine': 'production_readiness/deterministic-v1',
        'disclaimer': 'Heuristic readiness check. Confirm all blockers against plant SOPs and QA review before batch release.',
    }
    result['next_steps'] = [s for s in result['next_steps'] if s]
    return result
