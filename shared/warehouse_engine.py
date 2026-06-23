"""Warehouse / procurement intelligence engine.

Deterministic, offline functions for:
- waste / recovered-solvent / sellable-waste ledger
- reorder alerts and dead-stock flags
- recovered-solvent reuse matching against process inputs
- procurement request cross-check (duplicate, stock-available, vendor suggestions)
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from .database import db


def ensure_warehouse_engine_schema() -> None:
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS warehouse_waste_recovery_ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            material_name TEXT NOT NULL,
            item_type TEXT NOT NULL CHECK(item_type IN ('waste', 'recovered', 'sellable')),
            source_batch TEXT,
            source_type TEXT,
            source_id INTEGER,
            quantity REAL,
            unit TEXT,
            quality_grade TEXT,
            status TEXT DEFAULT 'available' CHECK(status IN ('available', 'used', 'sold', 'expired', 'quarantined')),
            used_by_batch TEXT,
            used_qty REAL DEFAULT 0,
            match_suggestions_json TEXT DEFAULT '[]',
            notes TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS warehouse_movement_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            material_name TEXT NOT NULL,
            movement_type TEXT NOT NULL,
            quantity REAL,
            unit TEXT,
            batch_ref TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def _now() -> str:
    return datetime.now().isoformat(timespec='seconds')


def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)


def _load_json(text: str | None, default: Any) -> Any:
    if not text:
        return default
    try:
        return json.loads(text)
    except Exception:
        return default


def add_ledger_entry(
    material_name: str,
    item_type: str,
    quantity: float,
    unit: str = 'kg',
    source_batch: str | None = None,
    source_type: str | None = None,
    source_id: int | None = None,
    quality_grade: str | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    ensure_warehouse_engine_schema()
    material_name = material_name.strip()
    if not material_name:
        raise ValueError('material_name required')
    if item_type not in ('waste', 'recovered', 'sellable'):
        raise ValueError("item_type must be 'waste', 'recovered' or 'sellable'")
    matches = recovered_material_matches(material_name)
    lid = db.execute(
        """INSERT INTO warehouse_waste_recovery_ledger
        (material_name, item_type, source_batch, source_type, source_id, quantity, unit, quality_grade, status, match_suggestions_json, notes, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            material_name, item_type, source_batch or '', source_type or '', source_id,
            quantity, unit, quality_grade or '', 'available', _json(matches), notes or '', _now(), _now(),
        ),
    )
    return {'ok': True, 'ledger_id': lid, 'matches': matches}


def list_ledger(item_type: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
    ensure_warehouse_engine_schema()
    where = ['1=1']
    params: list[Any] = []
    if item_type:
        where.append('item_type=?')
        params.append(item_type)
    if status:
        where.append('status=?')
        params.append(status)
    rows = db.query(f"SELECT * FROM warehouse_waste_recovery_ledger WHERE {' AND '.join(where)} ORDER BY created_at DESC", tuple(params))
    return [_hydrate_ledger(r) for r in rows]


def _hydrate_ledger(row: dict[str, Any]) -> dict[str, Any]:
    return {**dict(row), 'match_suggestions': _load_json(row.get('match_suggestions_json'), [])}


def use_ledger(ledger_id: int, used_qty: float, used_by_batch: str) -> dict[str, Any]:
    ensure_warehouse_engine_schema()
    row = db.one('SELECT * FROM warehouse_waste_recovery_ledger WHERE id=?', (ledger_id,))
    if not row:
        raise ValueError('Ledger entry not found')
    available = (row['quantity'] or 0) - (row['used_qty'] or 0)
    if used_qty > available:
        raise ValueError(f'Only {available} {row["unit"]} available')
    new_used = (row['used_qty'] or 0) + used_qty
    new_status = 'used' if new_used >= (row['quantity'] or 0) else 'available'
    db.execute(
        'UPDATE warehouse_waste_recovery_ledger SET used_qty=?, used_by_batch=?, status=?, updated_at=? WHERE id=?',
        (new_used, used_by_batch, new_status, _now(), ledger_id),
    )
    return {'ok': True, 'remaining': row['quantity'] - new_used}


def reorder_alerts() -> list[dict[str, Any]]:
    """Items in inventory_items at or below min_stock."""
    try:
        rows = db.query('SELECT * FROM inventory_items WHERE current_stock <= min_stock ORDER BY current_stock')
    except Exception:
        return []
    return [dict(r) for r in rows]


def dead_stock(days: int = 180) -> list[dict[str, Any]]:
    """Items with no recent movement and significant quantity."""
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    try:
        rows = db.query(
            """SELECT i.* FROM inventory_items i
            WHERE i.current_stock > 0
            AND NOT EXISTS (
                SELECT 1 FROM warehouse_movement_log m
                WHERE m.material_name = i.material_name AND m.created_at > ?
            )
            ORDER BY i.current_stock DESC""",
            (cutoff,),
        )
    except Exception:
        return []
    return [dict(r) for r in rows]


def recovered_material_matches(material_name: str) -> list[dict[str, Any]]:
    """Suggest internal processes or inventory items that could consume this material."""
    name = (material_name or '').lower()
    matches: list[dict[str, Any]] = []
    # Internal inventory items that are the same or similar.
    try:
        rows = db.query('SELECT material_name, item_code, current_stock, unit FROM inventory_items WHERE LOWER(material_name) LIKE ?', (f'%{name}%',))
        for r in rows:
            matches.append({'type': 'inventory_match', 'material': r['material_name'], 'stock': f"{r['current_stock']} {r['unit']}", 'match_score': 0.9})
    except Exception:
        pass
    # Process inputs from R&D route stages.
    try:
        rows = db.query('SELECT DISTINCT product_name FROM enterprise_products WHERE product_name IS NOT NULL LIMIT 50')
        for r in rows:
            product = r['product_name']
            try:
                from .product_chemistry import list_route_stages
                for stage in list_route_stages(product):
                    for rm in stage.get('raw_materials') or []:
                        rm_name = (rm.get('name') if isinstance(rm, dict) else str(rm)).lower()
                        if name in rm_name or rm_name in name:
                            matches.append({'type': 'process_input', 'product': product, 'material': rm_name, 'match_score': 0.8})
            except Exception:
                pass
    except Exception:
        pass
    # Common solvent categories.
    solvent_keywords = {'methanol', 'ethanol', 'acetone', 'thf', 'toluene', 'ethyl acetate', 'ipa', 'isopropanol', 'dcm', 'dichloromethane'}
    if any(k in name for k in solvent_keywords):
        matches.append({'type': 'category_hint', 'category': 'recovered_solvent', 'message': 'Consider solvent recovery and reuse in similar reactions.', 'match_score': 0.6})
    return matches[:10]


def procurement_cross_check(material_name: str, quantity: float, unit: str) -> dict[str, Any]:
    """Before raising a PO, check for duplicates, available stock, and recovered alternatives."""
    material_name = material_name.strip()
    warnings: list[str] = []
    suggestions: list[dict[str, Any]] = []

    # Duplicate open request?
    try:
        dup = db.one(
            "SELECT id, quantity, status FROM procurement_requests WHERE LOWER(material_name)=? AND status NOT IN ('closed','rejected','ordered') ORDER BY created_at DESC LIMIT 1",
            (material_name.lower(),),
        )
        if dup:
            warnings.append(f"Open {dup['status']} request already exists ({dup['quantity']} {unit}). Consider updating instead of creating a duplicate.")
    except Exception:
        pass

    # Stock already available?
    try:
        stock = db.query('SELECT current_stock, unit, status FROM inventory_items WHERE LOWER(material_name)=? LIMIT 1', (material_name.lower(),))
        if stock:
            s = stock[0]
            if s['current_stock'] >= quantity:
                warnings.append(f"Stock available: {s['current_stock']} {s['unit']} — issue from warehouse instead of buying.")
            else:
                suggestions.append({'type': 'partial_stock', 'available': s['current_stock'], 'unit': s['unit'], 'message': 'Use available stock first, then raise PO for remainder.'})
    except Exception:
        pass

    # Recovered / waste alternative.
    try:
        alt = db.query(
            "SELECT * FROM warehouse_waste_recovery_ledger WHERE LOWER(material_name) LIKE ? AND status='available' AND item_type IN ('recovered','sellable') ORDER BY quantity DESC LIMIT 3",
            (f'%{material_name.lower()}%',),
        )
        if alt:
            for a in alt:
                suggestions.append({'type': 'recovered_alternative', 'material': a['material_name'], 'quantity': a['quantity'], 'unit': a['unit'], 'quality_grade': a['quality_grade']})
    except Exception:
        pass

    # Vendor suggestions.
    vendors = vendor_suggestions(material_name)

    return {
        'ok': True,
        'material_name': material_name,
        'requested_quantity': quantity,
        'unit': unit,
        'can_proceed': len(warnings) == 0 or not any('already exists' in w for w in warnings),
        'warnings': warnings,
        'suggestions': suggestions,
        'vendor_suggestions': vendors,
    }


def vendor_suggestions(material_name: str, limit: int = 5) -> list[dict[str, Any]]:
    """Return approved vendors whose categories or supplied materials match."""
    name = material_name.lower()
    try:
        rows = db.query(
            """SELECT DISTINCT v.id, v.name, v.approved, v.lead_time_days, v.category, v.material_categories
            FROM vendors v
            LEFT JOIN material_vendors mv ON mv.vendor_id=v.id
            LEFT JOIN material_master m ON m.id=mv.material_id
            WHERE v.approved=1 AND (
                LOWER(v.category) LIKE ? OR LOWER(v.material_categories) LIKE ? OR LOWER(m.material_name) LIKE ?
            )
            ORDER BY v.lead_time_days
            LIMIT ?""",
            (f'%{name}%', f'%{name}%', f'%{name}%', limit),
        )
    except Exception:
        try:
            rows = db.query(
                "SELECT id, name, approved, lead_time_days, category, material_categories FROM vendors WHERE approved=1 ORDER BY lead_time_days LIMIT ?",
                (limit,),
            )
        except Exception:
            return []
    return [dict(r) for r in rows]
