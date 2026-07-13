from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Optional

from .config import settings
from .security import hash_password

SCHEMA = r'''
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    full_name TEXT NOT NULL,
    role TEXT NOT NULL,
    department TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    action TEXT NOT NULL,
    entity TEXT NOT NULL,
    entity_id TEXT,
    details TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS experiments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_name TEXT NOT NULL,
    title TEXT NOT NULL,
    objective TEXT,
    hypothesis TEXT,
    status TEXT NOT NULL DEFAULT 'planned',
    prediction TEXT,
    next_step TEXT,
    owner_id INTEGER,
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS coa_templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    product_name TEXT NOT NULL,
    fields_json TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    created_by INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS coa_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    template_id INTEGER NOT NULL,
    batch_no TEXT NOT NULL,
    product_name TEXT NOT NULL,
    values_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft',
    approved_by INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS vendors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    trade_name TEXT,
    gstin TEXT UNIQUE,
    state_code TEXT,
    pin TEXT,
    contact_person TEXT,
    phone TEXT,
    email TEXT,
    address TEXT,
    material_categories TEXT,
    drug_license_no TEXT,
    fssai_no TEXT,
    documents_json TEXT,
    approved INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS customers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    trade_name TEXT,
    gstin TEXT UNIQUE,
    state_code TEXT,
    pin TEXT,
    contact_person TEXT,
    phone TEXT,
    email TEXT,
    address TEXT,
    drug_license_no TEXT,
    fssai_no TEXT,
    documents_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS inventory_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    material_name TEXT NOT NULL,
    sku TEXT UNIQUE NOT NULL,
    current_stock REAL NOT NULL DEFAULT 0,
    unit TEXT NOT NULL DEFAULT 'kg',
    min_stock REAL NOT NULL DEFAULT 0,
    safety_stock REAL NOT NULL DEFAULT 0,
    reorder_point REAL NOT NULL DEFAULT 0,
    location TEXT,
    vendor_id INTEGER,
    preferred_vendor_id INTEGER,
    status TEXT NOT NULL DEFAULT 'quarantine',
    quarantine_reason TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS inventory_movements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id INTEGER NOT NULL,
    movement_type TEXT NOT NULL,
    quantity REAL NOT NULL,
    batch_no TEXT,
    notes TEXT,
    user_id INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS production_batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_name TEXT NOT NULL,
    batch_no TEXT UNIQUE NOT NULL,
    status TEXT NOT NULL DEFAULT 'planned',
    planned_qty REAL NOT NULL DEFAULT 0,
    unit TEXT NOT NULL DEFAULT 'kg',
    qc_status TEXT NOT NULL DEFAULT 'pending',
    blockers TEXT,
    start_date TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS procurement_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    material_name TEXT NOT NULL,
    quantity REAL NOT NULL,
    unit TEXT NOT NULL DEFAULT 'kg',
    required_by TEXT,
    status TEXT NOT NULL DEFAULT 'requested',
    requester_id INTEGER,
    linked_item_id INTEGER,
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS qms_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    record_type TEXT NOT NULL,
    title TEXT NOT NULL,
    product_name TEXT,
    batch_no TEXT,
    severity TEXT NOT NULL DEFAULT 'medium',
    status TEXT NOT NULL DEFAULT 'draft',
    description TEXT,
    description_html TEXT,
    ai_recommendation TEXT,
    owner_id INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS dms_documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_type TEXT NOT NULL,
    title TEXT NOT NULL,
    version TEXT NOT NULL DEFAULT '0.1-draft',
    status TEXT NOT NULL DEFAULT 'draft',
    owner_id INTEGER,
    file_path TEXT,
    content_html TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS rim_submissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_name TEXT NOT NULL,
    market TEXT NOT NULL,
    submission_type TEXT NOT NULL DEFAULT 'registration',
    status TEXT NOT NULL DEFAULT 'planning',
    next_commitment TEXT,
    due_date TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS autonomy_settings (
    workflow TEXT PRIMARY KEY,
    level INTEGER NOT NULL DEFAULT 1,
    updated_by INTEGER,
    reason TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ai_insights (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    department TEXT NOT NULL,
    insight_type TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    priority TEXT NOT NULL DEFAULT 'normal',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
'''

DEFAULT_COA_FIELDS = [
    {'key': 'description', 'label': 'Description', 'type': 'text', 'required': True, 'spec': 'Complies'},
    {'key': 'identification', 'label': 'Identification', 'type': 'text', 'required': True, 'spec': 'Positive'},
    {'key': 'assay', 'label': 'Assay', 'type': 'number', 'required': True, 'spec': '98.0 - 102.0 %'},
    {'key': 'loss_on_drying', 'label': 'Loss on Drying', 'type': 'number', 'required': False, 'spec': 'NMT 2.0 %'},
    {'key': 'ph', 'label': 'pH', 'type': 'number', 'required': False, 'spec': '5.0 - 7.5'},
    {'key': 'microbial_limit', 'label': 'Microbial Limit', 'type': 'text', 'required': False, 'spec': 'Within limit'},
]


class Database:
    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path or settings.database_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute('PRAGMA foreign_keys=ON')
            yield conn
            conn.commit()
        finally:
            conn.close()

    def init(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            # Inline schema upgrades for existing databases
            self._add_column_if_missing(conn, 'inventory_items', 'status', "TEXT NOT NULL DEFAULT 'quarantine'")
            self._add_column_if_missing(conn, 'inventory_items', 'quarantine_reason', "TEXT")
            self._add_column_if_missing(conn, 'inventory_items', 'safety_stock', "REAL NOT NULL DEFAULT 0")
            self._add_column_if_missing(conn, 'inventory_items', 'reorder_point', "REAL NOT NULL DEFAULT 0")
            self._add_column_if_missing(conn, 'inventory_items', 'preferred_vendor_id', "INTEGER")
            self._add_column_if_missing(conn, 'coa_records', 'approved_by', "INTEGER")
            # Rich-editor HTML storage upgrades
            self._add_column_if_missing(conn, 'qms_records', 'description_html', "TEXT")
            self._add_column_if_missing(conn, 'dms_documents', 'content_html', "TEXT")
            # Vendor/customer master-data upgrades
            self._add_column_if_missing(conn, 'vendors', 'trade_name', "TEXT")
            self._add_column_if_missing(conn, 'vendors', 'gstin', "TEXT UNIQUE")
            self._add_column_if_missing(conn, 'vendors', 'state_code', "TEXT")
            self._add_column_if_missing(conn, 'vendors', 'pin', "TEXT")
            self._add_column_if_missing(conn, 'vendors', 'drug_license_no', "TEXT")
            self._add_column_if_missing(conn, 'vendors', 'fssai_no', "TEXT")
            self._add_column_if_missing(conn, 'vendors', 'documents_json', "TEXT")
            self._add_column_if_missing(conn, 'vendors', 'updated_at', "TEXT")
        self.seed()

    def _add_column_if_missing(self, conn, table: str, column: str, definition: str) -> None:
        import logging
        import re
        try:
            cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
            if column in cols:
                return
            # SQLite cannot ADD COLUMN with UNIQUE — strip it so one bad DDL
            # doesn't roll back every other column upgrade in this connection.
            safe_definition = re.sub(r'\bUNIQUE\b', '', definition).strip()
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {safe_definition}")
        except Exception as exc:
            logging.getLogger('shims.database').warning(
                'Column upgrade %s.%s failed: %s', table, column, exc)

    def ensure_columns(self, table: str, columns: dict[str, str]) -> None:
        """Idempotently add missing columns. The runtime migration mechanism —
        CREATE TABLE IF NOT EXISTS never upgrades existing tables."""
        with self.connect() as conn:
            for name, ddl in columns.items():
                self._add_column_if_missing(conn, table, name, ddl)

    def execute(self, query: str, params: Iterable[Any] = ()) -> int:
        with self.connect() as conn:
            cur = conn.execute(query, tuple(params))
            return int(cur.lastrowid)

    def query(self, query: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
        with self.connect() as conn:
            cur = conn.execute(query, tuple(params))
            return [dict(row) for row in cur.fetchall()]

    def one(self, query: str, params: Iterable[Any] = ()) -> Optional[dict[str, Any]]:
        rows = self.query(query, params)
        return rows[0] if rows else None

    def audit(self, user_id: Optional[int], action: str, entity: str, entity_id: Any = None, details: Any = None) -> None:
        self.execute(
            'INSERT INTO audit_log(user_id, action, entity, entity_id, details) VALUES (?, ?, ?, ?, ?)',
            (user_id, action, entity, str(entity_id) if entity_id is not None else None, json.dumps(details, default=str) if details is not None else None),
        )

    def migrate(self) -> None:
        """Run Alembic migrations to bring schema to latest version."""
        import subprocess
        import sys
        try:
            subprocess.run(
                [sys.executable, '-m', 'alembic', 'upgrade', 'head'],
                cwd=str(Path(__file__).resolve().parents[1]),
                capture_output=True,
                text=True,
                check=True,
            )
        except Exception:
            # Alembic may not be installed or configured; raw init remains fallback
            pass

    def seed(self) -> None:
        import secrets
        import logging
        import os
        logger = logging.getLogger('shims.database')
        users = self.query('SELECT id FROM users LIMIT 1')
        if not users:
            if not settings.demo_mode:
                logger.warning('Demo mode is disabled. No default users seeded. Create an admin via CLI or API.')
            else:
                defaults = [
                    ('admin', 'Anany / Admin', 'admin', 'executive'),
                    ('executive', 'Executive View', 'executive', 'executive'),
                    ('rd', 'R&D Scientist', 'rd', 'rd'),
                    ('qc', 'QC Analyst', 'qc', 'qc'),
                    ('warehouse', 'Warehouse Officer', 'warehouse', 'warehouse'),
                    ('production', 'Production Officer', 'production', 'production'),
                    ('procurement', 'Procurement Officer', 'procurement', 'procurement'),
                    ('qa', 'QA Officer', 'qa', 'qa'),
                ]
                seeded = []
                # Use deterministic test password when running under pytest or demo mode.
                is_test = bool(os.environ.get('PYTEST_CURRENT_TEST'))
                use_predictable = is_test or settings.demo_mode
                for username, full_name, role, department in defaults:
                    password = 'SHIMS2025!' if use_predictable else secrets.token_urlsafe(10)
                    self.execute(
                        'INSERT INTO users(username, full_name, role, department, password_hash) VALUES (?, ?, ?, ?, ?)',
                        (username, full_name, role, department, hash_password(password)),
                    )
                    seeded.append(f'{username}={password}')
                if use_predictable:
                    logger.warning('DEMO MODE — default users created with predictable passwords: %s', ' | '.join(seeded))

        if not self.query('SELECT id FROM coa_templates LIMIT 1'):
            self.execute(
                'INSERT INTO coa_templates(name, product_name, fields_json, created_by) VALUES (?, ?, ?, ?)',
                ('Default Finished Product COA', 'Generic Product', json.dumps(DEFAULT_COA_FIELDS), 1),
            )

        if not self.query('SELECT id FROM vendors LIMIT 1'):
            self.execute(
                'INSERT INTO vendors(name, contact_person, phone, email, material_categories, approved) VALUES (?, ?, ?, ?, ?, ?)',
                ('Demo Approved Vendor', 'Vendor Manager', '+91-0000000000', 'vendor@example.com', 'API, excipients, packaging', 1),
            )

        if not self.query('SELECT id FROM inventory_items LIMIT 1'):
            self.execute(
                'INSERT INTO inventory_items(material_name, sku, current_stock, unit, min_stock, location, vendor_id) VALUES (?, ?, ?, ?, ?, ?, ?)',
                ('Demo Raw Material A', 'RM-A-001', 42.0, 'kg', 25.0, 'Warehouse A1', 1),
            )
            self.execute(
                'INSERT INTO inventory_items(material_name, sku, current_stock, unit, min_stock, location, vendor_id) VALUES (?, ?, ?, ?, ?, ?, ?)',
                ('Demo Packing Material B', 'PM-B-001', 1500.0, 'pcs', 500.0, 'Warehouse P2', 1),
            )

        if not self.query('SELECT id FROM experiments LIMIT 1'):
            self.execute(
                'INSERT INTO experiments(product_name, title, objective, hypothesis, status, prediction, next_step, owner_id, notes) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
                ('Generic Product', 'Pilot compatibility trial', 'Check raw material compatibility and processing window.', 'Material A should remain stable at pilot conditions.', 'planned', 'Low risk if moisture is controlled.', 'Run 3-condition DOE and submit samples to QC.', 3, 'Seed demo record.'),
            )

        if not self.query('SELECT id FROM production_batches LIMIT 1'):
            self.execute(
                'INSERT INTO production_batches(product_name, batch_no, status, planned_qty, unit, qc_status, blockers, start_date) VALUES (?, ?, ?, ?, ?, ?, ?, date())',
                ('Generic Product', 'BATCH-DEMO-001', 'planned', 100.0, 'kg', 'pending', '',),
            )

        if not self.query('SELECT id FROM qms_records LIMIT 1'):
            self.execute(
                'INSERT INTO qms_records(record_type, title, product_name, batch_no, severity, status, description, ai_recommendation, owner_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
                ('deviation', 'Demo line-clearance observation', 'Generic Product', 'BATCH-DEMO-001', 'medium', 'triage', 'Operator reported label reconciliation delay during line clearance.', 'Classify as minor deviation, check warehouse issue log, assign QA review. Human approval required before closure.', 1),
            )
        if not self.query('SELECT id FROM dms_documents LIMIT 1'):
            self.execute(
                'INSERT INTO dms_documents(doc_type, title, version, status, owner_id) VALUES (?, ?, ?, ?, ?)',
                ('sop', 'SOP-QA-001: Document Control and Training', '0.1-draft', 'draft', 1),
            )
        if not self.query('SELECT id FROM rim_submissions LIMIT 1'):
            self.execute(
                'INSERT INTO rim_submissions(product_name, market, submission_type, status, next_commitment, due_date) VALUES (?, ?, ?, ?, ?, ?)',
                ('Generic Product', 'India', 'variation', 'planning', 'Compile Module 3 quality summary from latest COA and batch records', '2026-06-30'),
            )
        if not self.query('SELECT workflow FROM autonomy_settings LIMIT 1'):
            for workflow, level, reason in [
                ('dashboard_generation', 3, 'low-risk reversible automation'),
                ('gst_draft_generation', 2, 'draft can be generated, human checks before portal submission'),
                ('coa_generation', 1, 'AI drafts only; QC/QA approval required'),
                ('batch_release', 0, 'never autonomous GxP gate'),
                ('deviation_closure', 0, 'never autonomous GxP gate'),
                ('capa_closure', 0, 'never autonomous GxP gate'),
                ('material_release', 0, 'never autonomous GxP gate'),
                ('regulatory_submission_signoff', 0, 'never autonomous GxP gate'),
            ]:
                self.execute('INSERT INTO autonomy_settings(workflow, level, updated_by, reason) VALUES (?, ?, ?, ?)', (workflow, level, 1, reason))


db = Database()
