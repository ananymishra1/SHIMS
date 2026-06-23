"""Hospital SQLite database helpers."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Optional

from .config import DB_PATH


@contextmanager
def get_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    try:
        yield con
        con.commit()
    finally:
        con.close()


def _ensure_base_schema(con: sqlite3.Connection) -> None:
    con.executescript(SCHEMA)


def ensure_schema() -> None:
    with get_db() as con:
        _ensure_base_schema(con)


def query_one(sql: str, params: tuple = ()) -> dict[str, Any] | None:
    with get_db() as con:
        row = con.execute(sql, params).fetchone()
        return dict(row) if row else None


def query_all(sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    with get_db() as con:
        return [dict(row) for row in con.execute(sql, params).fetchall()]


def insert(sql: str, params: tuple = ()) -> int:
    with get_db() as con:
        cur = con.execute(sql, params)
        return cur.lastrowid


def execute(sql: str, params: tuple = ()) -> int:
    with get_db() as con:
        cur = con.execute(sql, params)
        return cur.rowcount


SCHEMA = r'''
-- users / roles
CREATE TABLE IF NOT EXISTS hospital_users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    full_name TEXT NOT NULL,
    role TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS hospital_audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    action TEXT NOT NULL,
    entity TEXT NOT NULL,
    entity_id TEXT,
    details TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- patient registry
CREATE TABLE IF NOT EXISTS patients (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hospital_id TEXT UNIQUE,  -- assigned UHID
    name TEXT NOT NULL,
    phone TEXT NOT NULL,
    email TEXT,
    gender TEXT,
    dob TEXT,
    age INTEGER,
    blood_group TEXT,
    address TEXT,
    city TEXT,
    state TEXT,
    pincode TEXT,
    emergency_name TEXT,
    emergency_phone TEXT,
    insurance_provider TEXT,
    insurance_id TEXT,
    allergies TEXT,
    medical_history TEXT,
    current_medications TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS visits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id INTEGER NOT NULL,
    visit_type TEXT NOT NULL,  -- opd, ipd, emergency, ivf, ot
    status TEXT NOT NULL DEFAULT 'active',  -- active, discharged, cancelled
    department TEXT,
    assigned_user_id INTEGER,
    bed_id INTEGER,
    chief_complaint TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    discharged_at TEXT
);

CREATE TABLE IF NOT EXISTS vitals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    visit_id INTEGER NOT NULL,
    recorded_by INTEGER,
    temperature REAL,
    pulse INTEGER,
    bp_systolic INTEGER,
    bp_diastolic INTEGER,
    respiratory_rate INTEGER,
    spo2 REAL,
    weight_kg REAL,
    height_cm REAL,
    bmi REAL,
    notes TEXT,
    recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS complaints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    visit_id INTEGER NOT NULL,
    complaint TEXT NOT NULL,
    duration TEXT,
    severity TEXT,
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS diagnoses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    visit_id INTEGER NOT NULL,
    diagnosis TEXT NOT NULL,
    icd_code TEXT,
    type TEXT DEFAULT 'provisional',  -- provisional / confirmed / differential
    notes TEXT,
    created_by INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS prescriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    visit_id INTEGER NOT NULL,
    medication TEXT NOT NULL,
    dosage TEXT,
    frequency TEXT,
    duration TEXT,
    route TEXT,
    instructions TEXT,
    created_by INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- lab
CREATE TABLE IF NOT EXISTS lab_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    visit_id INTEGER NOT NULL,
    test_name TEXT NOT NULL,
    category TEXT,
    status TEXT NOT NULL DEFAULT 'ordered',  -- ordered, sampled, in_progress, reported, cancelled
    ordered_by INTEGER,
    ordered_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    reported_at TEXT
);

CREATE TABLE IF NOT EXISTS lab_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lab_order_id INTEGER NOT NULL,
    parameter TEXT,
    value TEXT,
    unit TEXT,
    reference_range TEXT,
    status TEXT DEFAULT 'normal',  -- normal / abnormal / critical
    notes TEXT,
    reported_by INTEGER,
    reported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ipd / rooms
CREATE TABLE IF NOT EXISTS rooms (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ward TEXT NOT NULL,
    room_number TEXT NOT NULL,
    bed_number TEXT NOT NULL,
    bed_type TEXT DEFAULT 'general',  -- general / semi_private / private / icu
    status TEXT NOT NULL DEFAULT 'available',  -- available / occupied / maintenance
    UNIQUE(ward, room_number, bed_number)
);

CREATE TABLE IF NOT EXISTS bed_allocations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    visit_id INTEGER NOT NULL,
    bed_id INTEGER NOT NULL,
    allocated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    discharged_at TEXT
);

-- ot
CREATE TABLE IF NOT EXISTS ot_schedules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    visit_id INTEGER NOT NULL,
    procedure TEXT NOT NULL,
    scheduled_at TEXT NOT NULL,
    ot_room TEXT,
    surgeon_id INTEGER,
    anaesthetist_id INTEGER,
    status TEXT NOT NULL DEFAULT 'scheduled',  -- scheduled / in_progress / completed / cancelled
    notes TEXT,
    consent_signed INTEGER DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ot_team (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ot_schedule_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    role TEXT NOT NULL
);

-- appointments / reminders
CREATE TABLE IF NOT EXISTS appointments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id INTEGER NOT NULL,
    visit_id INTEGER,
    appointment_type TEXT NOT NULL,  -- follow_up / routine / procedure
    scheduled_at TEXT NOT NULL,
    department TEXT,
    doctor_id INTEGER,
    status TEXT NOT NULL DEFAULT 'scheduled',
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id INTEGER,
    visit_id INTEGER,
    reminder_type TEXT NOT NULL,  -- appointment, medication, lab, ot_prep, follow_up_call
    scheduled_at TEXT NOT NULL,
    message TEXT,
    status TEXT NOT NULL DEFAULT 'pending',  -- pending / sent / dismissed
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ai notes
CREATE TABLE IF NOT EXISTS ai_notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    visit_id INTEGER,
    patient_id INTEGER,
    note_type TEXT NOT NULL,  -- differential, treatment_suggestion, ivf_insight, mentor
    prompt_context TEXT,
    response TEXT NOT NULL,
    reviewed_by INTEGER,
    reviewed_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ivf module
CREATE TABLE IF NOT EXISTS ivf_couples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    female_patient_id INTEGER NOT NULL,
    male_patient_id INTEGER,
    married_years INTEGER,
    trying_to_conceive_years INTEGER,
    previous_pregnancies INTEGER DEFAULT 0,
    previous_miscarriages INTEGER DEFAULT 0,
    prior_ivf_cycles INTEGER DEFAULT 0,
    known_causes TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ivf_cycles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    couple_id INTEGER NOT NULL,
    cycle_number INTEGER NOT NULL,
    protocol TEXT,  -- antagonist, agonist, mild, natural
    start_date TEXT,
    expected_retrieval_date TEXT,
    expected_transfer_date TEXT,
    status TEXT NOT NULL DEFAULT 'planned',  -- planned / active / completed / cancelled
    outcome TEXT,
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ivf_stimulations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle_id INTEGER NOT NULL,
    medication TEXT NOT NULL,
    dose REAL,
    unit TEXT,
    administered_at TEXT,
    administered_by INTEGER,
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ivf_follicle_scans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle_id INTEGER NOT NULL,
    scan_date TEXT NOT NULL,
    right_follicles INTEGER,
    left_follicles INTEGER,
    largest_follicle_mm REAL,
    endometrial_thickness_mm REAL,
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ivf_embryos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle_id INTEGER NOT NULL,
    embryo_number INTEGER NOT NULL,
    retrieval_date TEXT,
    maturity TEXT,  -- MII / MI / GV
    fertilization_status TEXT,  -- fertilized / failed / unknown
    grade TEXT,
    transfer_date TEXT,
    freeze_date TEXT,
    outcome TEXT,
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
'''
