"""initial_schema

Revision ID: 10dc704bf98e
Revises:
Create Date: 2026-05-29 12:17:03.254115

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '10dc704bf98e'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Base schema from shared/database.py
BASE_SCHEMA = r'''
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
    contact_person TEXT,
    phone TEXT,
    email TEXT,
    address TEXT,
    material_categories TEXT,
    approved INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS inventory_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    material_name TEXT NOT NULL,
    sku TEXT UNIQUE NOT NULL,
    current_stock REAL NOT NULL DEFAULT 0,
    unit TEXT NOT NULL DEFAULT 'kg',
    min_stock REAL NOT NULL DEFAULT 0,
    location TEXT,
    vendor_id INTEGER,
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

# Expansion schema from shared/enterprise_expansion.py
EXPANSION_SCHEMA = r'''
CREATE TABLE IF NOT EXISTS lims_samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sample_no TEXT UNIQUE NOT NULL,
    product_name TEXT NOT NULL,
    batch_no TEXT,
    sample_type TEXT NOT NULL DEFAULT 'finished_product',
    test_plan TEXT,
    status TEXT NOT NULL DEFAULT 'logged',
    assigned_to INTEGER,
    due_date TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS stability_protocols (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    protocol_no TEXT UNIQUE NOT NULL,
    product_name TEXT NOT NULL,
    batch_no TEXT,
    condition_name TEXT NOT NULL DEFAULT '30C/65RH',
    pull_schedule_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS mes_ebr_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id INTEGER,
    product_name TEXT NOT NULL,
    batch_no TEXT NOT NULL,
    mbr_no TEXT,
    stage TEXT NOT NULL DEFAULT 'dispensing',
    step_name TEXT NOT NULL,
    expected_value TEXT,
    actual_value TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    operator_id INTEGER,
    qa_review_status TEXT NOT NULL DEFAULT 'pending',
    exception_notes TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS line_clearance_checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_no TEXT NOT NULL,
    area TEXT NOT NULL,
    checklist_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'draft',
    checked_by INTEGER,
    qa_verified_by INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS supplier_qualifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vendor_id INTEGER,
    vendor_name TEXT NOT NULL,
    material_category TEXT,
    risk_level TEXT NOT NULL DEFAULT 'medium',
    status TEXT NOT NULL DEFAULT 'draft',
    next_audit_date TEXT,
    ai_risk_notes TEXT,
    approved_by INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS training_assignments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER,
    document_title TEXT NOT NULL,
    assigned_to INTEGER,
    role_required TEXT,
    due_date TEXT,
    status TEXT NOT NULL DEFAULT 'assigned',
    effectiveness_check TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS electronic_signatures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    meaning TEXT NOT NULL,
    entity TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    comment TEXT,
    signer_name TEXT,
    signed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    metadata_json TEXT DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS gst_drafts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_type TEXT NOT NULL,
    document_no TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft',
    payload_json TEXT NOT NULL,
    pdf_path TEXT,
    json_path TEXT,
    ledger_json TEXT,
    created_by INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS regulatory_commitments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    submission_id INTEGER,
    commitment TEXT NOT NULL,
    owner TEXT,
    due_date TEXT,
    status TEXT NOT NULL DEFAULT 'open',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
'''

# Pharma core schema from shared/enterprise_pharma_core.py
PHARMA_SCHEMA = r'''
CREATE TABLE IF NOT EXISTS product_research (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_name TEXT NOT NULL,
    chemical_name TEXT,
    cas_no TEXT,
    molecular_formula TEXT,
    product_type TEXT DEFAULT 'API',
    research_status TEXT DEFAULT 'draft',
    internet_query TEXT,
    patent_query TEXT,
    route_summary TEXT,
    ai_research_brief TEXT,
    created_by INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS process_stages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_research_id INTEGER,
    product_name TEXT NOT NULL,
    stage_no INTEGER NOT NULL,
    stage_name TEXT NOT NULL,
    input_material TEXT,
    output_material TEXT,
    critical_controls_json TEXT DEFAULT '[]',
    default_tests_json TEXT DEFAULT '[]',
    target_yield_pct REAL,
    expected_duration_hours REAL,
    status TEXT DEFAULT 'draft',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS experiment_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_research_id INTEGER,
    run_no TEXT NOT NULL,
    product_name TEXT NOT NULL,
    target_qty REAL DEFAULT 0,
    unit TEXT DEFAULT 'kg',
    route_name TEXT,
    status TEXT DEFAULT 'planned',
    owner_id INTEGER,
    purpose TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS reaction_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    stage_name TEXT NOT NULL,
    timepoint TEXT,
    temperature_c REAL,
    ph_value REAL,
    addition TEXT,
    phase TEXT,
    color TEXT,
    sample_taken INTEGER DEFAULT 0,
    sample_request_id INTEGER,
    notes TEXT,
    created_by INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS yield_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    stage_name TEXT NOT NULL,
    input_qty REAL DEFAULT 0,
    output_qty REAL DEFAULT 0,
    yield_pct REAL DEFAULT 0,
    purity_pct REAL DEFAULT 0,
    impurity_profile_json TEXT DEFAULT '{}',
    loss_analysis TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS qc_sample_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_department TEXT NOT NULL,
    source_ref TEXT,
    product_name TEXT NOT NULL,
    batch_no TEXT,
    stage_name TEXT,
    sample_type TEXT DEFAULT 'in-process',
    tests_json TEXT NOT NULL DEFAULT '[]',
    priority TEXT DEFAULT 'normal',
    status TEXT DEFAULT 'requested',
    due_date TEXT,
    created_by INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS qc_test_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id INTEGER,
    coa_record_id INTEGER,
    test_name TEXT NOT NULL,
    specification TEXT,
    result_value TEXT,
    unit TEXT,
    pass_fail TEXT DEFAULT 'pending',
    analyst_id INTEGER,
    instrument_id TEXT,
    raw_data_ref TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS equipment_master (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    equipment_code TEXT UNIQUE NOT NULL,
    equipment_name TEXT NOT NULL,
    equipment_type TEXT,
    capacity REAL DEFAULT 0,
    unit TEXT DEFAULT 'L',
    location TEXT,
    status TEXT DEFAULT 'available',
    cleaning_status TEXT DEFAULT 'clean',
    current_product TEXT,
    utility_profile_json TEXT DEFAULT '{}',
    compatible_products TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS production_plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_name TEXT NOT NULL,
    target_qty REAL NOT NULL,
    unit TEXT DEFAULT 'kg',
    desired_start_date TEXT,
    status TEXT DEFAULT 'draft',
    feasibility_score INTEGER DEFAULT 0,
    stock_check_json TEXT DEFAULT '{}',
    equipment_check_json TEXT DEFAULT '{}',
    utility_check_json TEXT DEFAULT '{}',
    ai_plan TEXT,
    created_by INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS production_plan_stages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id INTEGER NOT NULL,
    stage_no INTEGER NOT NULL,
    stage_name TEXT NOT NULL,
    dependency TEXT,
    expected_hours REAL DEFAULT 0,
    equipment_code TEXT,
    material_requirements_json TEXT DEFAULT '[]',
    qc_tests_json TEXT DEFAULT '[]',
    utility_requirements_json TEXT DEFAULT '{}',
    status TEXT DEFAULT 'planned'
);
CREATE TABLE IF NOT EXISTS procurement_rfq (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    material_name TEXT NOT NULL,
    quantity REAL NOT NULL,
    unit TEXT DEFAULT 'kg',
    vendor_id INTEGER,
    target_delivery_date TEXT,
    status TEXT DEFAULT 'draft',
    ai_vendor_note TEXT,
    created_by INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS qa_docs_center (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_no TEXT,
    doc_type TEXT NOT NULL,
    title TEXT NOT NULL,
    department TEXT DEFAULT 'QA',
    version TEXT DEFAULT '0.1-draft',
    status TEXT DEFAULT 'draft',
    owner_id INTEGER,
    file_path TEXT,
    next_review_date TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS enterprise_ai_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    department TEXT NOT NULL,
    event_type TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT,
    source_ref TEXT,
    status TEXT DEFAULT 'open',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
'''

# Equipment intelligence extra schema from shared/equipment_intelligence.py
EQUIPMENT_SCHEMA = r'''
CREATE TABLE IF NOT EXISTS equipment_status_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    equipment_code TEXT NOT NULL,
    previous_status TEXT,
    new_status TEXT NOT NULL,
    previous_cleaning_status TEXT,
    new_cleaning_status TEXT,
    batch_no TEXT,
    stage_name TEXT,
    reason TEXT,
    user_id INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS equipment_cleaning_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    equipment_code TEXT NOT NULL,
    previous_product TEXT,
    previous_batch_no TEXT,
    cleaning_type TEXT DEFAULT 'product_changeover',
    sop_no TEXT,
    status TEXT DEFAULT 'started',
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT,
    verified_by INTEGER,
    remarks TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS equipment_reservations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    equipment_code TEXT NOT NULL,
    plan_id INTEGER,
    batch_no TEXT,
    product_name TEXT,
    stage_no INTEGER,
    stage_name TEXT,
    start_time TEXT,
    end_time TEXT,
    status TEXT DEFAULT 'reserved',
    reserved_by INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS manpower_roster (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id INTEGER,
    batch_no TEXT,
    department TEXT DEFAULT 'Production',
    role_name TEXT NOT NULL,
    shift_name TEXT DEFAULT 'A',
    headcount INTEGER DEFAULT 1,
    skill_required TEXT,
    status TEXT DEFAULT 'planned',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS process_equipment_requirements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_name TEXT NOT NULL,
    stage_no INTEGER NOT NULL,
    stage_name TEXT NOT NULL,
    required_class TEXT NOT NULL,
    min_capacity_l REAL DEFAULT 0,
    preferred_moc TEXT,
    utility_need_json TEXT DEFAULT '{}',
    cleaning_requirement TEXT DEFAULT 'product-contact equipment cleaned and line cleared',
    manpower_json TEXT DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS tech_transfer_packages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_name TEXT NOT NULL,
    product_research_id INTEGER,
    source_scale TEXT DEFAULT 'lab',
    target_scale TEXT DEFAULT 'plant',
    target_batch_size REAL DEFAULT 0,
    unit TEXT DEFAULT 'kg',
    route_summary TEXT,
    cpp_json TEXT DEFAULT '[]',
    cqa_json TEXT DEFAULT '[]',
    equipment_fit_json TEXT DEFAULT '{}',
    risk_assessment TEXT,
    status TEXT DEFAULT 'draft',
    created_by INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS scale_up_trials (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_name TEXT NOT NULL,
    tech_transfer_id INTEGER,
    trial_no TEXT NOT NULL,
    scale_level TEXT DEFAULT 'pilot',
    target_qty REAL DEFAULT 0,
    unit TEXT DEFAULT 'kg',
    equipment_path_json TEXT DEFAULT '{}',
    sampling_plan_json TEXT DEFAULT '[]',
    acceptance_criteria TEXT,
    status TEXT DEFAULT 'planned',
    created_by INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS utility_capacity_plan (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    utility_name TEXT NOT NULL,
    available_capacity REAL DEFAULT 0,
    unit TEXT,
    planned_load REAL DEFAULT 0,
    lead_time_hours REAL DEFAULT 0,
    status TEXT DEFAULT 'available',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
'''

# Additional columns for equipment_master added by equipment_intelligence.py
EQUIPMENT_ALTER_COLUMNS = [
    ('source_sr_no', 'INTEGER'),
    ('plant', 'TEXT'),
    ('tag_no', 'TEXT'),
    ('description', 'TEXT'),
    ('moc', 'TEXT'),
    ('nominal_capacity', 'TEXT'),
    ('capacity_value', 'REAL DEFAULT 0'),
    ('capacity_unit', 'TEXT'),
    ('floor_elevation', 'TEXT'),
    ('phase_status', 'TEXT'),
    ('equipment_class', 'TEXT'),
    ('occupancy_status', "TEXT DEFAULT 'idle'"),
    ('readiness_status', "TEXT DEFAULT 'ready'"),
    ('current_batch_no', 'TEXT'),
    ('current_stage', 'TEXT'),
    ('last_cleaned_at', 'TEXT'),
    ('cleaning_valid_until', 'TEXT'),
    ('cleaning_sop_no', 'TEXT'),
    ('maintenance_status', "TEXT DEFAULT 'ok'"),
    ('maintenance_due_date', 'TEXT'),
    ('cross_contamination_risk', "TEXT DEFAULT 'standard'"),
    ('gmp_lock_reason', 'TEXT'),
    ('remarks', 'TEXT'),
]


def _execute_sql_block(sql: str) -> None:
    """Split multi-statement SQL and execute each safely."""
    for stmt in sql.split(';'):
        stmt = stmt.strip()
        if not stmt:
            continue
        # PRAGMA must be executed alone; op.execute handles it fine
        op.execute(stmt)


def upgrade() -> None:
    """Create all SHIMS tables."""
    _execute_sql_block(BASE_SCHEMA)
    _execute_sql_block(EXPANSION_SCHEMA)
    _execute_sql_block(PHARMA_SCHEMA)
    _execute_sql_block(EQUIPMENT_SCHEMA)
    for col, ddl in EQUIPMENT_ALTER_COLUMNS:
        try:
            op.execute(f"ALTER TABLE equipment_master ADD COLUMN {col} {ddl}")
        except Exception:
            pass


def downgrade() -> None:
    """Drop all SHIMS tables in reverse dependency order."""
    tables = [
        'scale_up_trials',
        'tech_transfer_packages',
        'process_equipment_requirements',
        'manpower_roster',
        'equipment_reservations',
        'equipment_cleaning_log',
        'equipment_status_history',
        'utility_capacity_plan',
        'production_plan_stages',
        'production_plans',
        'procurement_rfq',
        'qa_docs_center',
        'enterprise_ai_events',
        'reaction_observations',
        'yield_results',
        'experiment_runs',
        'process_stages',
        'product_research',
        'qc_test_results',
        'qc_sample_requests',
        'line_clearance_checks',
        'mes_ebr_records',
        'stability_protocols',
        'lims_samples',
        'supplier_qualifications',
        'training_assignments',
        'electronic_signatures',
        'gst_drafts',
        'regulatory_commitments',
        'autonomy_settings',
        'ai_insights',
        'rim_submissions',
        'dms_documents',
        'qms_records',
        'procurement_requests',
        'production_batches',
        'inventory_movements',
        'inventory_items',
        'vendors',
        'coa_records',
        'coa_templates',
        'experiments',
        'audit_log',
        'users',
        'equipment_master',
    ]
    for t in tables:
        try:
            op.execute(f"DROP TABLE IF EXISTS {t}")
        except Exception:
            pass
