"""Tests for J K Hospital app."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.app.main import app


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


def test_hospital_status(client):
    r = client.get("/hospital/api/status")
    assert r.status_code == 200
    data = r.json()
    assert data["ok"]
    assert "patients" in data["counts"]


def test_patient_registry_and_visit(client):
    r = client.post("/hospital/api/patients", json={"name": "Test Patient", "phone": "9999999999", "age": 30, "gender": "Female"})
    assert r.status_code == 200
    patient = r.json()["patient"]
    assert patient["hospital_id"].startswith("JKH-")

    r2 = client.post("/hospital/api/visits", json={"patient_id": patient["id"], "visit_type": "opd", "chief_complaint": "cough"})
    assert r2.status_code == 200
    visit = r2.json()["visit"]
    assert visit["patient_id"] == patient["id"]

    r3 = client.get(f"/hospital/api/visits/{visit['id']}")
    assert r3.status_code == 200
    summary = r3.json()
    assert summary["patient"]["name"] == "Test Patient"


def test_vitals_complaint_diagnosis_prescription(client):
    r = client.post("/hospital/api/patients", json={"name": "Vitals Test", "phone": "8888888888"})
    pid = r.json()["patient"]["id"]
    v = client.post("/hospital/api/visits", json={"patient_id": pid, "visit_type": "opd"}).json()["visit"]
    vid = v["id"]

    assert client.post(f"/hospital/api/visits/{vid}/vitals", json={"temperature": 99.0, "pulse": 80, "bp_systolic": 120, "bp_diastolic": 80, "weight_kg": 70, "height_cm": 170}).status_code == 200
    assert client.post(f"/hospital/api/visits/{vid}/complaints", json={"complaint": "fever", "duration": "2 days"}).status_code == 200
    assert client.post(f"/hospital/api/visits/{vid}/diagnoses", json={"diagnosis": "Viral fever", "type": "provisional"}).status_code == 200
    assert client.post(f"/hospital/api/visits/{vid}/prescriptions", json={"medication": "Paracetamol", "dosage": "500 mg", "frequency": "TDS"}).status_code == 200

    summary = client.get(f"/hospital/api/visits/{vid}").json()
    assert len(summary["vitals"]) == 1
    assert len(summary["complaints"]) == 1
    assert len(summary["diagnoses"]) == 1
    assert len(summary["prescriptions"]) == 1


def test_lab_order_and_result(client):
    r = client.post("/hospital/api/patients", json={"name": "Lab Test", "phone": "7777777777"})
    pid = r.json()["patient"]["id"]
    vid = client.post("/hospital/api/visits", json={"patient_id": pid, "visit_type": "opd"}).json()["visit"]["id"]

    r = client.post(f"/hospital/api/visits/{vid}/lab-orders", json={"test_name": "CBC", "category": "hematology"})
    assert r.status_code == 200
    order_id = r.json()["order"]["id"]

    r2 = client.post(f"/hospital/api/lab-orders/{order_id}/results", json={"parameter": "Hb", "value": "12.5", "unit": "g/dL", "status": "normal"})
    assert r2.status_code == 200


def test_bed_allocation(client):
    rooms = client.get("/hospital/api/rooms?status=available").json()["rooms"]
    assert rooms
    bed_id = rooms[0]["id"]

    r = client.post("/hospital/api/patients", json={"name": "IPD Test", "phone": "6666666666"})
    pid = r.json()["patient"]["id"]
    vid = client.post("/hospital/api/visits", json={"patient_id": pid, "visit_type": "ipd"}).json()["visit"]["id"]

    r = client.post(f"/hospital/api/visits/{vid}/allocate-bed", json={"bed_id": bed_id})
    assert r.status_code == 200

    r2 = client.post(f"/hospital/api/visits/{vid}/discharge-bed")
    assert r2.status_code == 200


def test_ot_schedule(client):
    r = client.post("/hospital/api/patients", json={"name": "OT Test", "phone": "5555555555"})
    pid = r.json()["patient"]["id"]
    vid = client.post("/hospital/api/visits", json={"patient_id": pid, "visit_type": "opd"}).json()["visit"]["id"]

    r = client.post(f"/hospital/api/visits/{vid}/ot-schedule", json={"procedure": "Appendectomy", "scheduled_at": "2026-06-20T10:00:00", "ot_room": "OT-1"})
    assert r.status_code == 200
    ot_id = r.json()["schedule"]["id"]

    r2 = client.patch(f"/hospital/api/ot-schedules/{ot_id}", json={"status": "completed"})
    assert r2.status_code == 200
    assert r2.json()["schedule"]["status"] == "completed"


def test_ivf_cycle(client):
    f = client.post("/hospital/api/patients", json={"name": "IVF Female", "phone": "4444444444", "gender": "Female", "age": 32}).json()["patient"]
    m = client.post("/hospital/api/patients", json={"name": "IVF Male", "phone": "3333333333", "gender": "Male", "age": 34}).json()["patient"]
    couple = client.post("/hospital/api/ivf/couples", json={"female_patient_id": f["id"], "male_patient_id": m["id"], "trying_to_conceive_years": 3}).json()["couple"]

    cycle = client.post(f"/hospital/api/ivf/couples/{couple['id']}/cycles", json={"protocol": "antagonist", "start_date": "2026-06-16"}).json()["cycle"]
    assert cycle["cycle_number"] == 1

    scan = client.post(f"/hospital/api/ivf/cycles/{cycle['id']}/scans", json={"scan_date": "2026-06-18", "right_follicles": 5, "left_follicles": 4, "largest_follicle_mm": 18.5, "endometrial_thickness_mm": 9.2}).json()["scan"]
    assert scan["right_follicles"] == 5


def test_demo_generator(client):
    before = client.get("/hospital/api/status").json()["counts"]["patients"]
    r = client.post("/hospital/api/demo/generate", json={"count": 3})
    assert r.status_code == 200
    after = client.get("/hospital/api/status").json()["counts"]["patients"]
    assert after >= before + 3


def test_staff_estimator(client):
    r = client.post("/hospital/api/estimator/staff", json={"patient_load": 1000, "opd_per_day": 120, "ipd_beds": 40, "ivf_cycles_per_month": 30})
    assert r.status_code == 200
    est = r.json()["estimate"]["estimated_staff"]
    assert est["doctors"] >= 2
    assert est["nurses"] >= 3
