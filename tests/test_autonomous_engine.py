"""Regression tests for the hourly autonomous engine crashes (P1).

The live failure: an old DB created before ``safety_stock``/``reorder_point``/
``preferred_vendor_id`` existed never got the columns (CREATE TABLE IF NOT
EXISTS doesn't upgrade), so ``propose_inventory_reorders`` raised
``sqlite3.OperationalError`` every hour. Also: one bad ALTER (e.g. ``TEXT
UNIQUE``, illegal in SQLite ADD COLUMN) used to poison the whole upgrade batch.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from shared.database import Database, db


def _columns(path: Path, table: str) -> set[str]:
    conn = sqlite3.connect(path)
    try:
        return {r[1] for r in conn.execute(f'PRAGMA table_info({table})')}
    finally:
        conn.close()


def test_old_schema_db_gets_upgraded(tmp_path: Path):
    """A DB created from the pre-safety_stock schema must gain the columns on init."""
    old_db = tmp_path / 'old.sqlite3'
    conn = sqlite3.connect(old_db)
    conn.execute(
        '''CREATE TABLE inventory_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            material_name TEXT NOT NULL,
            sku TEXT UNIQUE NOT NULL,
            current_stock REAL NOT NULL DEFAULT 0,
            unit TEXT NOT NULL DEFAULT 'kg',
            min_stock REAL NOT NULL DEFAULT 0,
            location TEXT, vendor_id INTEGER,
            created_at TEXT, updated_at TEXT
        )'''
    )
    conn.commit()
    conn.close()

    Database(old_db).init()

    cols = _columns(old_db, 'inventory_items')
    assert {'safety_stock', 'reorder_point', 'preferred_vendor_id', 'status', 'quarantine_reason'} <= cols

    # the previously crashing query must now run
    conn = sqlite3.connect(old_db)
    conn.execute(
        'SELECT id, material_name, sku, current_stock, safety_stock, reorder_point, unit, preferred_vendor_id '
        'FROM inventory_items WHERE current_stock <= reorder_point OR current_stock <= safety_stock'
    ).fetchall()
    conn.close()


def test_bad_alter_does_not_poison_other_upgrades(tmp_path: Path):
    """A UNIQUE (illegal) ADD COLUMN must be sanitized/skipped without rolling back the rest."""
    path = tmp_path / 'poison.sqlite3'
    conn = sqlite3.connect(path)
    conn.execute('CREATE TABLE t (id INTEGER PRIMARY KEY)')
    conn.commit()
    conn.close()

    d = Database(path)
    d.ensure_columns('t', {
        'gstin': 'TEXT UNIQUE',      # illegal as ADD COLUMN — must be sanitized
        'normal_col': 'TEXT',
        'numeric_col': 'REAL NOT NULL DEFAULT 0',
    })
    cols = _columns(path, 't')
    assert {'gstin', 'normal_col', 'numeric_col'} <= cols


def test_propose_inventory_reorders_runs_clean():
    """Against the (seeded) test DB the reorder proposal must not raise."""
    from shared.autonomous_engine import propose_inventory_reorders
    db.init()
    result = propose_inventory_reorders(None)
    assert result['ok'] is True
    assert isinstance(result['proposals'], list)


def test_reorder_execution_insert_matches_schema():
    """The autonomous reorder INSERT must match the real procurement_requests columns."""
    from shared.autonomous_engine import execute_low_risk_decisions
    db.init()
    # Force a below-safety-stock item so a proposal exists.
    db.execute(
        "INSERT OR IGNORE INTO inventory_items(material_name, sku, current_stock, unit, min_stock, safety_stock, reorder_point) "
        "VALUES ('Reorder Test Material', 'RM-REORDER-T1', 1.0, 'kg', 10.0, 10.0, 12.0)"
    )
    result = execute_low_risk_decisions(None)
    assert result['ok'] is True
    if result.get('executed'):
        row = db.one('SELECT * FROM procurement_requests WHERE id=?',
                     (result['executed'][0]['procurement_request_id'],))
        assert row is not None and row['status'] == 'pending_approval'
    else:
        # If nothing executed, no proposal may carry an insert error.
        assert all('error' not in p for p in result.get('queued', []) if p.get('type') == 'reorder'), \
            f"reorder insert failed: {result.get('queued')}"


def test_cycle_isolates_task_failures(monkeypatch: pytest.MonkeyPatch):
    """One failing task must not kill the cycle; others still run."""
    import shared.autonomous_engine as eng

    monkeypatch.setattr(eng, 'ingest_new_documents', lambda uid=None: (_ for _ in ()).throw(RuntimeError('doc scan exploded')))
    monkeypatch.setattr(eng, 'auto_generate_bmrs', lambda uid=None: {'ok': True, 'generated': 0})
    monkeypatch.setattr(eng, 'auto_validate_bmrs', lambda uid=None: {'ok': True, 'validated': 0})
    monkeypatch.setattr(eng, 'make_autonomous_decisions', lambda uid=None: {'ok': True, 'results': {}})
    monkeypatch.setattr(eng, 'execute_low_risk_decisions', lambda uid=None: {'ok': True, 'executed': [], 'queued': []})
    monkeypatch.setattr(eng, 'sync_memories_to_omni', lambda uid=None: {'ok': True, 'synced': 0})

    result = eng.run_autonomous_cycle(None)
    assert result['ok'] is True
    assert result['results']['documents']['ok'] is False
    assert 'doc scan exploded' in result['results']['documents']['error']
    assert result['results']['bmrs']['ok'] is True
    assert result['errors'] and 'documents' in result['errors'][0]
