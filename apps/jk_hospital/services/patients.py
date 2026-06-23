"""Patient and visit business logic."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..database import execute, insert, query_all, query_one


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def generate_uh_id(existing: set[str] | None = None) -> str:
    """Generate a UHID like JKH-000001."""
    prefix = "JKH"
    # count patients in db
    count = query_one("SELECT COUNT(*) c FROM patients")["c"]
    for i in range(count + 1, count + 10000):
        candidate = f"{prefix}-{i:06d}"
        if existing and candidate in existing:
            continue
        if not query_one("SELECT id FROM patients WHERE hospital_id=?", (candidate,)):
            return candidate
    return f"{prefix}-{count + 1:06d}"


def create_patient(data: dict[str, Any], created_by: int | None = None) -> dict[str, Any]:
    hospital_id = data.get("hospital_id") or generate_uh_id()
    sql = '''INSERT INTO patients
        (hospital_id, name, phone, email, gender, dob, age, blood_group, address,
         city, state, pincode, emergency_name, emergency_phone,
         insurance_provider, insurance_id, allergies, medical_history, current_medications)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)'''
    params = (
        hospital_id,
        data.get("name", "").strip(),
        data.get("phone", "").strip(),
        data.get("email", "").strip() or None,
        data.get("gender", "").strip() or None,
        data.get("dob", "").strip() or None,
        data.get("age"),
        data.get("blood_group", "").strip() or None,
        data.get("address", "").strip() or None,
        data.get("city", "").strip() or None,
        data.get("state", "").strip() or None,
        data.get("pincode", "").strip() or None,
        data.get("emergency_name", "").strip() or None,
        data.get("emergency_phone", "").strip() or None,
        data.get("insurance_provider", "").strip() or None,
        data.get("insurance_id", "").strip() or None,
        data.get("allergies", "").strip() or None,
        data.get("medical_history", "").strip() or None,
        data.get("current_medications", "").strip() or None,
    )
    patient_id = insert(sql, params)
    return query_one("SELECT * FROM patients WHERE id=?", (patient_id,))


def update_patient(patient_id: int, data: dict[str, Any]) -> dict[str, Any] | None:
    allowed = {
        "name", "phone", "email", "gender", "dob", "age", "blood_group", "address",
        "city", "state", "pincode", "emergency_name", "emergency_phone",
        "insurance_provider", "insurance_id", "allergies", "medical_history", "current_medications"
    }
    fields = []
    params = []
    for k, v in data.items():
        if k in allowed:
            fields.append(f"{k} = ?")
            params.append(v)
    if not fields:
        return query_one("SELECT * FROM patients WHERE id=?", (patient_id,))
    params.append(now_iso())
    params.append(patient_id)
    execute(f"UPDATE patients SET {', '.join(fields)}, updated_at=? WHERE id=?", tuple(params))
    return query_one("SELECT * FROM patients WHERE id=?", (patient_id,))


def search_patients(q: str, limit: int = 50) -> list[dict[str, Any]]:
    term = f"%{q}%"
    return query_all(
        """SELECT * FROM patients
           WHERE name LIKE ? OR phone LIKE ? OR hospital_id LIKE ? OR city LIKE ?
           ORDER BY updated_at DESC LIMIT ?""",
        (term, term, term, term, limit),
    )


def get_patient(patient_id: int) -> dict[str, Any] | None:
    return query_one("SELECT * FROM patients WHERE id=?", (patient_id,))


def get_patient_by_hospital_id(hospital_id: str) -> dict[str, Any] | None:
    return query_one("SELECT * FROM patients WHERE hospital_id=?", (hospital_id,))


def create_visit(data: dict[str, Any]) -> dict[str, Any]:
    sql = '''INSERT INTO visits (patient_id, visit_type, status, department, assigned_user_id, bed_id, chief_complaint)
             VALUES (?, ?, ?, ?, ?, ?, ?)'''
    visit_id = insert(sql, (
        data["patient_id"],
        data.get("visit_type", "opd"),
        data.get("status", "active"),
        data.get("department", "").strip() or None,
        data.get("assigned_user_id"),
        data.get("bed_id"),
        data.get("chief_complaint", "").strip() or None,
    ))
    return query_one("SELECT * FROM visits WHERE id=?", (visit_id,))


def list_visits(patient_id: int | None = None, status: str | None = None, visit_type: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    where = ["1=1"]
    params: list[Any] = []
    if patient_id:
        where.append("patient_id = ?")
        params.append(patient_id)
    if status:
        where.append("status = ?")
        params.append(status)
    if visit_type:
        where.append("visit_type = ?")
        params.append(visit_type)
    sql = f"SELECT * FROM visits WHERE {' AND '.join(where)} ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    return query_all(sql, tuple(params))


def get_visit(visit_id: int) -> dict[str, Any] | None:
    return query_one("SELECT * FROM visits WHERE id=?", (visit_id,))


def update_visit(visit_id: int, data: dict[str, Any]) -> dict[str, Any] | None:
    allowed = {"status", "department", "assigned_user_id", "bed_id", "chief_complaint", "discharged_at"}
    fields = []
    params = []
    for k, v in data.items():
        if k in allowed:
            fields.append(f"{k} = ?")
            params.append(v)
    if not fields:
        return get_visit(visit_id)
    params.append(now_iso())
    params.append(visit_id)
    execute(f"UPDATE visits SET {', '.join(fields)}, updated_at=? WHERE id=?", tuple(params))
    return get_visit(visit_id)


def add_vitals(visit_id: int, data: dict[str, Any], recorded_by: int | None = None) -> dict[str, Any]:
    # compute bmi
    weight = data.get("weight_kg")
    height = data.get("height_cm")
    bmi = None
    if weight and height:
        try:
            bmi = round(float(weight) / ((float(height) / 100) ** 2), 2)
        except Exception:
            pass
    sql = '''INSERT INTO vitals
        (visit_id, recorded_by, temperature, pulse, bp_systolic, bp_diastolic,
         respiratory_rate, spo2, weight_kg, height_cm, bmi, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)'''
    vid = insert(sql, (
        visit_id, recorded_by,
        data.get("temperature"), data.get("pulse"),
        data.get("bp_systolic"), data.get("bp_diastolic"),
        data.get("respiratory_rate"), data.get("spo2"),
        weight, height, bmi,
        data.get("notes", "").strip() or None,
    ))
    return query_one("SELECT * FROM vitals WHERE id=?", (vid,))


def get_vitals(visit_id: int) -> list[dict[str, Any]]:
    return query_all("SELECT * FROM vitals WHERE visit_id=? ORDER BY recorded_at DESC", (visit_id,))


def add_complaint(visit_id: int, data: dict[str, Any]) -> dict[str, Any]:
    cid = insert(
        "INSERT INTO complaints (visit_id, complaint, duration, severity, notes) VALUES (?, ?, ?, ?, ?)",
        (visit_id, data["complaint"], data.get("duration"), data.get("severity"), data.get("notes")),
    )
    return query_one("SELECT * FROM complaints WHERE id=?", (cid,))


def get_complaints(visit_id: int) -> list[dict[str, Any]]:
    return query_all("SELECT * FROM complaints WHERE visit_id=? ORDER BY created_at DESC", (visit_id,))


def add_diagnosis(visit_id: int, data: dict[str, Any], created_by: int | None = None) -> dict[str, Any]:
    did = insert(
        "INSERT INTO diagnoses (visit_id, diagnosis, icd_code, type, notes, created_by) VALUES (?, ?, ?, ?, ?, ?)",
        (visit_id, data["diagnosis"], data.get("icd_code"), data.get("type", "provisional"), data.get("notes"), created_by),
    )
    return query_one("SELECT * FROM diagnoses WHERE id=?", (did,))


def get_diagnoses(visit_id: int) -> list[dict[str, Any]]:
    return query_all("SELECT * FROM diagnoses WHERE visit_id=? ORDER BY created_at DESC", (visit_id,))


def add_prescription(visit_id: int, data: dict[str, Any], created_by: int | None = None) -> dict[str, Any]:
    pid = insert(
        "INSERT INTO prescriptions (visit_id, medication, dosage, frequency, duration, route, instructions, created_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (visit_id, data["medication"], data.get("dosage"), data.get("frequency"), data.get("duration"), data.get("route"), data.get("instructions"), created_by),
    )
    return query_one("SELECT * FROM prescriptions WHERE id=?", (pid,))


def get_prescriptions(visit_id: int) -> list[dict[str, Any]]:
    return query_all("SELECT * FROM prescriptions WHERE visit_id=? ORDER BY created_at DESC", (visit_id,))


def full_visit_summary(visit_id: int) -> dict[str, Any] | None:
    visit = get_visit(visit_id)
    if not visit:
        return None
    patient = get_patient(visit["patient_id"])
    return {
        "visit": visit,
        "patient": patient,
        "vitals": get_vitals(visit_id),
        "complaints": get_complaints(visit_id),
        "diagnoses": get_diagnoses(visit_id),
        "prescriptions": get_prescriptions(visit_id),
    }
