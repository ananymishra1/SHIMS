"""Tests for the sales / accounting margin engine."""
from __future__ import annotations

import pytest
from shared.margin_engine import (
    calculate_product_cost,
    ensure_margin_schema,
    list_sales_orders,
    margin_report,
    record_cost_snapshot,
    record_sales_order,
)


@pytest.fixture(autouse=True)
def _schema(tmp_path, monkeypatch):
    monkeypatch.setenv("SHIMS_DATA_DIR", str(tmp_path))
    ensure_margin_schema()


def test_calculate_product_cost_unknown_product():
    out = calculate_product_cost('NonExistentAPI', 100.0)
    assert out["ok"]
    assert out["total_cost_per_kg"] >= 0
    assert isinstance(out["unpriced_raw_materials"], list)


def test_record_cost_snapshot():
    out = record_cost_snapshot('Paracetamol', 100.0)
    assert out["ok"]
    assert out["total_cost_per_kg"] >= 0


def test_record_sales_order_and_report():
    order = record_sales_order({
        'product_name': 'Paracetamol',
        'quantity': 50.0,
        'unit_price': 5000.0,
        'customer_name': 'Test Customer',
    })
    assert order["ok"]
    assert order["total_value"] == 250000.0
    assert "margin_pct" in order
    orders = list_sales_orders()
    assert any(o['order_no'] == order['order_no'] for o in orders)
    report = margin_report()
    assert report["ok"]
    assert report["total_orders"] >= 1
    assert report["total_revenue"] >= 250000.0


def test_low_margin_flag():
    record_sales_order({'product_name': 'LowMarginAPI', 'quantity': 10, 'unit_price': 100})
    report = margin_report()
    assert any(p['product'] == 'LowMarginAPI' for p in report['by_product'])
