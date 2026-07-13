"""Tests for the warehouse / procurement intelligence engine."""
from __future__ import annotations

import pytest
from shared.warehouse_engine import (
    add_ledger_entry,
    ensure_warehouse_engine_schema,
    list_ledger,
    procurement_cross_check,
    recovered_material_matches,
    use_ledger,
)


@pytest.fixture(autouse=True)
def _schema(tmp_path, monkeypatch):
    monkeypatch.setenv("SHIMS_DATA_DIR", str(tmp_path))
    ensure_warehouse_engine_schema()


def test_add_and_list_ledger():
    res = add_ledger_entry('Spent methanol', 'recovered', 50.0, 'kg', source_batch='B-001', quality_grade='good')
    assert res["ok"]
    assert res["ledger_id"]
    assert any('recovered_solvent' in str(m) or 'category_hint' in m.get('type', '') for m in res["matches"])
    entries = list_ledger(item_type='recovered')
    assert any(e["material_name"] == "Spent methanol" for e in entries)


def test_use_ledger():
    res = add_ledger_entry('Recovered acetone', 'recovered', 20.0, 'kg')
    lid = res["ledger_id"]
    use = use_ledger(lid, 5.0, 'B-002')
    assert use["ok"]
    assert use["remaining"] == 15.0
    entries = [e for e in list_ledger(status='available') if e["id"] == lid]
    assert entries
    assert entries[0]["used_qty"] == 5.0


def test_use_ledger_over_use():
    res = add_ledger_entry('Waste tar', 'waste', 2.0, 'kg')
    with pytest.raises(ValueError):
        use_ledger(res["ledger_id"], 5.0, 'B-003')


def test_recovered_matches_no_crash():
    matches = recovered_material_matches('methanol')
    assert isinstance(matches, list)


def test_procurement_cross_check_no_crash():
    # With empty DB it should still return a result.
    out = procurement_cross_check('Acetone', 10.0, 'kg')
    assert out["ok"]
    assert out["material_name"] == "Acetone"
    assert "warnings" in out
    assert "suggestions" in out
    assert "vendor_suggestions" in out
