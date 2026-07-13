"""Sales / accounting margin engine.

Deterministic, offline:
- Record sales orders
- Compute product cost per kg from route raw materials + rm_price_book
- Compute margin, markup, contribution
- Demand-to-production feasibility (via production readiness)
- Margin report and cost-reduction feed
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from .database import db


def ensure_margin_schema() -> None:
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS sales_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_no TEXT UNIQUE,
            customer_name TEXT,
            product_name TEXT NOT NULL,
            quantity REAL NOT NULL,
            unit TEXT DEFAULT 'kg',
            unit_price REAL NOT NULL,
            total_value REAL,
            cost_per_unit REAL,
            margin_per_unit REAL,
            margin_pct REAL,
            status TEXT DEFAULT 'open',
            order_date TEXT,
            delivery_date TEXT,
            notes TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS product_cost_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_name TEXT NOT NULL,
            rm_cost_per_kg REAL,
            conversion_cost_per_kg REAL,
            overhead_per_kg REAL DEFAULT 0,
            total_cost_per_kg REAL,
            source TEXT DEFAULT 'calculated',
            valid_from TEXT,
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


def _generate_order_no() -> str:
    return f"SO-{datetime.now().strftime('%y%m%d%H%M%S%f')}"


def _rm_price(material_name: str) -> float:
    """Best available price per kg for a raw material."""
    try:
        row = db.one(
            """SELECT price_per_kg FROM rm_price_book
            WHERE lower(material_name)=?
            ORDER BY verification_status='verified' DESC, updated_at DESC LIMIT 1""",
            (material_name.lower(),),
        )
        if row and row['price_per_kg']:
            return float(row['price_per_kg'])
    except Exception:
        pass
    try:
        row = db.one(
            """SELECT suggested_price_per_kg FROM rm_price_quote_suggestions
            WHERE lower(material_name)=? ORDER BY created_at DESC LIMIT 1""",
            (material_name.lower(),),
        )
        if row and row['suggested_price_per_kg']:
            return float(row['suggested_price_per_kg'])
    except Exception:
        pass
    return 0.0


def _route_raw_materials(product_name: str) -> list[dict[str, Any]]:
    """Return normalized raw materials for a product's route."""
    rms: list[dict[str, Any]] = []
    try:
        from .product_chemistry import list_route_stages
        for stage in list_route_stages(product_name):
            for rm in stage.get('raw_materials') or []:
                if isinstance(rm, dict):
                    name = rm.get('name') or rm.get('material_name') or ''
                    qty = rm.get('quantity') or rm.get('qty') or 0
                    unit = rm.get('unit') or 'kg'
                else:
                    name = str(rm)
                    qty = 1
                    unit = 'kg'
                if name:
                    rms.append({'name': name, 'quantity': float(qty) or 0, 'unit': unit})
    except Exception:
        pass
    return rms


def calculate_product_cost(product_name: str, batch_size_kg: float = 100.0) -> dict[str, Any]:
    """Compute cost per kg from route RM quantities and latest prices."""
    rms = _route_raw_materials(product_name)
    rm_cost_total = 0.0
    priced_rms = []
    unpriced = []
    for rm in rms:
        price = _rm_price(rm['name'])
        cost = price * rm['quantity']
        rm_cost_total += cost
        if price > 0:
            priced_rms.append({**rm, 'price_per_kg': price, 'cost': cost})
        else:
            unpriced.append(rm['name'])

    # Conversion cost heuristic: 15% of RM cost + fixed batch processing.
    conversion_cost_total = rm_cost_total * 0.15 + 5000.0
    overhead_total = rm_cost_total * 0.05
    total_cost = rm_cost_total + conversion_cost_total + overhead_total
    cost_per_kg = total_cost / batch_size_kg if batch_size_kg else 0
    rm_cost_per_kg = rm_cost_total / batch_size_kg if batch_size_kg else 0
    conversion_cost_per_kg = conversion_cost_total / batch_size_kg if batch_size_kg else 0
    overhead_per_kg = overhead_total / batch_size_kg if batch_size_kg else 0

    return {
        'ok': True,
        'product_name': product_name,
        'batch_size_kg': batch_size_kg,
        'rm_cost_per_kg': round(rm_cost_per_kg, 2),
        'conversion_cost_per_kg': round(conversion_cost_per_kg, 2),
        'overhead_per_kg': round(overhead_per_kg, 2),
        'total_cost_per_kg': round(cost_per_kg, 2),
        'priced_raw_materials': priced_rms,
        'unpriced_raw_materials': unpriced,
        'engine': 'margin_engine/deterministic-v1',
    }


def record_cost_snapshot(product_name: str, batch_size_kg: float = 100.0) -> dict[str, Any]:
    ensure_margin_schema()
    calc = calculate_product_cost(product_name, batch_size_kg)
    db.execute(
        """INSERT INTO product_cost_snapshots
        (product_name, rm_cost_per_kg, conversion_cost_per_kg, overhead_per_kg, total_cost_per_kg, source, valid_from, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            product_name,
            calc['rm_cost_per_kg'],
            calc['conversion_cost_per_kg'],
            calc['overhead_per_kg'],
            calc['total_cost_per_kg'],
            'calculated',
            _now()[:10],
            _now(),
        ),
    )
    return calc


def record_sales_order(data: dict[str, Any]) -> dict[str, Any]:
    ensure_margin_schema()
    product_name = str(data.get('product_name') or '').strip()
    quantity = float(data.get('quantity') or 0)
    unit_price = float(data.get('unit_price') or 0)
    if not product_name or quantity <= 0 or unit_price <= 0:
        raise ValueError('product_name, positive quantity and unit_price required')
    unit = str(data.get('unit') or 'kg').strip() or 'kg'
    total_value = quantity * unit_price

    # Cost
    cost = calculate_product_cost(product_name, batch_size_kg=max(quantity, 1.0))
    cost_per_unit = cost['total_cost_per_kg']
    margin_per_unit = unit_price - cost_per_unit
    margin_pct = (margin_per_unit / unit_price * 100) if unit_price else 0

    order_no = str(data.get('order_no') or '').strip() or _generate_order_no()
    now = _now()
    oid = db.execute(
        """INSERT INTO sales_orders
        (order_no, customer_name, product_name, quantity, unit, unit_price, total_value, cost_per_unit, margin_per_unit, margin_pct, status, order_date, delivery_date, notes, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            order_no,
            str(data.get('customer_name') or '').strip(),
            product_name,
            quantity,
            unit,
            unit_price,
            round(total_value, 2),
            round(cost_per_unit, 2),
            round(margin_per_unit, 2),
            round(margin_pct, 2),
            str(data.get('status') or 'open').strip(),
            str(data.get('order_date') or now[:10]).strip(),
            str(data.get('delivery_date') or '').strip(),
            str(data.get('notes') or '').strip(),
            now,
            now,
        ),
    )
    return {
        'ok': True,
        'order_id': oid,
        'order_no': order_no,
        'product_name': product_name,
        'quantity': quantity,
        'unit_price': unit_price,
        'cost_per_unit': round(cost_per_unit, 2),
        'margin_per_unit': round(margin_per_unit, 2),
        'margin_pct': round(margin_pct, 2),
        'total_value': round(total_value, 2),
    }


def list_sales_orders(product_name: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
    ensure_margin_schema()
    where = ['1=1']
    params: list[Any] = []
    if product_name:
        where.append('lower(product_name)=?')
        params.append(product_name.lower())
    if status:
        where.append('status=?')
        params.append(status)
    rows = db.query(f"SELECT * FROM sales_orders WHERE {' AND '.join(where)} ORDER BY created_at DESC", tuple(params))
    return [dict(r) for r in rows]


def margin_report() -> dict[str, Any]:
    ensure_margin_schema()
    rows = db.query('SELECT * FROM sales_orders ORDER BY created_at DESC')
    orders = [dict(r) for r in rows]
    total_revenue = sum(o['total_value'] or 0 for o in orders)
    total_margin = sum((o['margin_per_unit'] or 0) * (o['quantity'] or 0) for o in orders)
    by_product: dict[str, dict[str, Any]] = {}
    for o in orders:
        p = o['product_name']
        if p not in by_product:
            by_product[p] = {'product': p, 'orders': 0, 'quantity': 0, 'revenue': 0, 'margin': 0}
        by_product[p]['orders'] += 1
        by_product[p]['quantity'] += o['quantity'] or 0
        by_product[p]['revenue'] += o['total_value'] or 0
        by_product[p]['margin'] += (o['margin_per_unit'] or 0) * (o['quantity'] or 0)
    low_margin = [p for p in by_product.values() if p['revenue'] > 0 and (p['margin'] / p['revenue'] * 100) < 15]
    return {
        'ok': True,
        'total_orders': len(orders),
        'total_revenue': round(total_revenue, 2),
        'total_margin': round(total_margin, 2),
        'overall_margin_pct': round(total_margin / total_revenue * 100, 2) if total_revenue else 0,
        'by_product': list(by_product.values()),
        'low_margin_products': low_margin,
    }


def demand_feasibility(product_name: str, quantity: float, delivery_date: str | None = None) -> dict[str, Any]:
    """Cross-check a demand signal against production readiness."""
    try:
        from .production_readiness import check_readiness
        readiness = check_readiness(product_name, quantity)
    except Exception as exc:
        readiness = {'ok': False, 'error': str(exc)[:200]}
    cost = calculate_product_cost(product_name, batch_size_kg=max(quantity, 1.0))
    return {
        'ok': True,
        'product_name': product_name,
        'quantity': quantity,
        'delivery_date': delivery_date,
        'readiness': readiness,
        'estimated_cost_per_kg': cost['total_cost_per_kg'],
        'priced_raw_materials': cost['priced_raw_materials'],
        'unpriced_raw_materials': cost['unpriced_raw_materials'],
    }
