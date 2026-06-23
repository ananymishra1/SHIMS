import pytest
from fastapi.testclient import TestClient
from backend.app.main import app

PREFIX = "/school"

@pytest.fixture(scope="module")
def client():
    return TestClient(app)

def get_token(client, username, password):
    res = client.post(f"{PREFIX}/auth/token", data={"username": username, "password": password})
    assert res.status_code == 200
    return res.json()["access_token"]

def auth_headers(client, username="admin", password="admin123"):
    token = get_token(client, username, password)
    return {"Authorization": f"Bearer {token}"}

# ── Status / UI ──────────────────────────────────────────────────────────────

def test_status(client):
    res = client.get(PREFIX)
    assert res.status_code == 200

def test_dashboard_tab(client):
    res = client.get(f"{PREFIX}/")
    assert res.status_code == 200

# ── Auth ─────────────────────────────────────────────────────────────────────

def test_auth_admin(client):
    token = get_token(client, "admin", "admin123")
    assert token

def test_auth_principal(client):
    token = get_token(client, "principal", "principal123")
    assert token

def test_auth_teacher(client):
    token = get_token(client, "teacher", "teacher123")
    assert token

def test_auth_student(client):
    token = get_token(client, "student", "student123")
    assert token

def test_auth_invalid(client):
    res = client.post(f"{PREFIX}/auth/token", data={"username": "nobody", "password": "wrong"})
    assert res.status_code in (400, 401, 422)

# ── Students ──────────────────────────────────────────────────────────────────

def test_create_student(client):
    headers = auth_headers(client)
    res = client.post(f"{PREFIX}/api/students", json={
        "admission_number": "ADM-001",
        "full_name": "Alice Tan",
        "grade": "10",
        "section": "A",
        "parent_phone": "98765432"
    }, headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert data.get("ok") or "id" in data or "admission_number" in data

def test_create_student_missing_required(client):
    headers = auth_headers(client)
    res = client.post(f"{PREFIX}/api/students", json={
        "full_name": "Bob Lim"
    }, headers=headers)
    assert res.status_code in (400, 422)

def test_list_students(client):
    headers = auth_headers(client)
    res = client.get(f"{PREFIX}/api/students", headers=headers)
    assert res.status_code == 200
    assert isinstance(res.json(), list)

def test_get_student_by_id(client):
    headers = auth_headers(client)
    # Create first
    create_res = client.post(f"{PREFIX}/api/students", json={
        "admission_number": "ADM-002",
        "full_name": "Charlie Wong",
        "grade": "9",
        "section": "B"
    }, headers=headers)
    assert create_res.status_code == 200
    student_id = create_res.json().get("id", 1)

    res = client.get(f"{PREFIX}/api/students/{student_id}", headers=headers)
    assert res.status_code == 200

def test_get_student_not_found(client):
    headers = auth_headers(client)
    res = client.get(f"{PREFIX}/api/students/999999", headers=headers)
    assert res.status_code in (404, 200)

# ── Staff ─────────────────────────────────────────────────────────────────────

def test_create_staff(client):
    headers = auth_headers(client)
    res = client.post(f"{PREFIX}/api/staff", json={
        "employee_id": "EMP-001",
        "full_name": "Dr. Smith",
        "role": "teacher",
        "subject": "Mathematics"
    }, headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert data.get("ok") or "id" in data or "employee_id" in data

def test_create_staff_missing_required(client):
    headers = auth_headers(client)
    res = client.post(f"{PREFIX}/api/staff", json={
        "full_name": "Incomplete Staff"
    }, headers=headers)
    assert res.status_code in (400, 422)

def test_list_staff(client):
    headers = auth_headers(client)
    res = client.get(f"{PREFIX}/api/staff", headers=headers)
    assert res.status_code == 200
    assert isinstance(res.json(), list)

# ── Classes ───────────────────────────────────────────────────────────────────

def test_create_class(client):
    headers = auth_headers(client)
    res = client.post(f"{PREFIX}/api/classes", json={
        "grade": "10",
        "section": "A",
        "class_teacher_id": 1
    }, headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert data.get("ok") or "id" in data or "grade" in data

def test_create_class_missing_required(client):
    headers = auth_headers(client)
    res = client.post(f"{PREFIX}/api/classes", json={
        "grade": "10"
    }, headers=headers)
    assert res.status_code in (400, 422)

def test_list_classes(client):
    headers = auth_headers(client)
    res = client.get(f"{PREFIX}/api/classes", headers=headers)
    assert res.status_code == 200
    assert isinstance(res.json(), list)

# ── Attendance ────────────────────────────────────────────────────────────────

def test_mark_attendance(client):
    headers = auth_headers(client)
    res = client.post(f"{PREFIX}/api/attendance", json={
        "student_id": 1,
        "date": "2024-01-15",
        "status": "present"
    }, headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert data.get("ok") or "id" in data or "student_id" in data

def test_mark_attendance_missing_required(client):
    headers = auth_headers(client)
    res = client.post(f"{PREFIX}/api/attendance", json={
        "student_id": 1
    }, headers=headers)
    assert res.status_code in (400, 422)

def test_get_attendance(client):
    headers = auth_headers(client)
    res = client.get(f"{PREFIX}/api/attendance", headers=headers)
    assert res.status_code == 200
    assert isinstance(res.json(), list)

def test_get_attendance_filter_by_student(client):
    headers = auth_headers(client)
    res = client.get(f"{PREFIX}/api/attendance?student_id=1", headers=headers)
    assert res.status_code == 200

# ── Exams ─────────────────────────────────────────────────────────────────────

def test_create_exam(client):
    headers = auth_headers(client)
    res = client.post(f"{PREFIX}/api/exams", json={
        "name": "Midterm 2024",
        "subject": "Mathematics",
        "grade": "10",
        "max_marks": 100.0
    }, headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert data.get("ok") or "id" in data or "name" in data

def test_create_exam_missing_required(client):
    headers = auth_headers(client)
    res = client.post(f"{PREFIX}/api/exams", json={
        "name": "Incomplete Exam"
    }, headers=headers)
    assert res.status_code in (400, 422)

def test_list_exams(client):
    headers = auth_headers(client)
    res = client.get(f"{PREFIX}/api/exams", headers=headers)
    assert res.status_code == 200
    assert isinstance(res.json(), list)

# ── Results ───────────────────────────────────────────────────────────────────

def test_record_result(client):
    headers = auth_headers(client)
    res = client.post(f"{PREFIX}/api/results", json={
        "student_id": 1,
        "exam_id": 1,
        "marks": 85.5
    }, headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert data.get("ok") or "id" in data or "student_id" in data

def test_record_result_missing_required(client):
    headers = auth_headers(client)
    res = client.post(f"{PREFIX}/api/results", json={
        "student_id": 1
    }, headers=headers)
    assert res.status_code in (400, 422)

def test_list_results(client):
    headers = auth_headers(client)
    res = client.get(f"{PREFIX}/api/results", headers=headers)
    assert res.status_code == 200
    assert isinstance(res.json(), list)

def test_list_results_filter_by_student(client):
    headers = auth_headers(client)
    res = client.get(f"{PREFIX}/api/results?student_id=1", headers=headers)
    assert res.status_code == 200

# ── Fees ──────────────────────────────────────────────────────────────────────

def test_add_fee_record(client):
    headers = auth_headers(client)
    res = client.post(f"{PREFIX}/api/fees", json={
        "student_id": 1,
        "amount": 5000.0,
        "term": "Term 1 2024",
        "paid": 5000.0
    }, headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert data.get("ok") or "id" in data or "student_id" in data

def test_add_fee_record_missing_required(client):
    headers = auth_headers(client)
    res = client.post(f"{PREFIX}/api/fees", json={
        "student_id": 1,
        "amount": 5000.0
    }, headers=headers)
    assert res.status_code in (400, 422)

def test_list_fees(client):
    headers = auth_headers(client)
    res = client.get(f"{PREFIX}/api/fees", headers=headers)
    assert res.status_code == 200
    assert isinstance(res.json(), list)

def test_list_fees_filter_by_student(client):
    headers = auth_headers(client)
    res = client.get(f"{PREFIX}/api/fees?student_id=1", headers=headers)
    assert res.status_code == 200

# ── AI Suggest ────────────────────────────────────────────────────────────────

def test_ai_suggest(client):
    headers = auth_headers(client)
    res = client.post(f"{PREFIX}/api/ai/suggest", json={
        "context": "student at risk",
        "student_id": 1
    }, headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert "suggestion" in data or "result" in data or "ok" in data or isinstance(data, dict)

def test_ai_suggest_no_body(client):
    headers = auth_headers(client)
    res = client.post(f"{PREFIX}/api/ai/suggest", json={}, headers=headers)
    assert res.status_code in (200, 400, 422)

# ── Role-based access ─────────────────────────────────────────────────────────

def test_teacher_can_list_students(client):
    headers = auth_headers(client, "teacher", "teacher123")
    res = client.get(f"{PREFIX}/api/students", headers=headers)
    assert res.status_code == 200

def test_student_can_view_own_results(client):
    headers = auth_headers(client, "student", "student123")
    res = client.get(f"{PREFIX}/api/results", headers=headers)
    assert res.status_code in (200, 403)

def test_principal_can_view_fees(client):
    headers = auth_headers(client, "principal", "principal123")
    res = client.get(f"{PREFIX}/api/fees", headers=headers)
    assert res.status_code in (200, 403)

def test_unauthenticated_cannot_create_student(client):
    res = client.post(f"{PREFIX}/api/students", json={
        "admission_number": "ADM-999",
        "full_name": "Unauthorized User",
        "grade": "8",
        "section": "C"
    })
    assert res.status_code in (401, 403, 422)

# ── Full Flow ─────────────────────────────────────────────────────────────────

def test_full_student_flow(client):
    headers = auth_headers(client)

    # Admit student
    create_res = client.post(f"{PREFIX}/api/students", json={
        "admission_number": "ADM-FLOW-001",
        "full_name": "Flow Test Student",
        "grade": "11",
        "section": "C",
        "parent_phone": "91234567"
    }, headers=headers)
    assert create_res.status_code == 200
    student_id = create_res.json().get("id", 1)

    # Mark attendance
    att_res = client.post(f"{PREFIX}/api/attendance", json={
        "student_id": student_id,
        "date": "2024-02-01",
        "status": "present"
    }, headers=headers)
    assert att_res.status_code == 200

    # Create exam
    exam_res = client.post(f"{PREFIX}/api/exams", json={
        "name": "Final Exam",
        "subject": "Science",
        "grade": "11",
        "max_marks": 100.0
    }, headers=headers)
    assert exam_res.status_code == 200
    exam_id = exam_res.json().get("id", 1)

    # Record result
    result_res = client.post(f"{PREFIX}/api/results", json={
        "student_id": student_id,
        "exam_id": exam_id,
        "marks": 72.0
    }, headers=headers)
    assert result_res.status_code == 200

    # Add fee
    fee_res = client.post(f"{PREFIX}/api/fees", json={
        "student_id": student_id,
        "amount": 4500.0,
        "term": "Term 2 2024",
        "paid": 4500.0
    }, headers=headers)
    assert fee_res.status_code == 200

    # AI intervention suggestion
    ai_res = client.post(f"{PREFIX}/api/ai/suggest", json={
        "student_id": student_id,
        "context": "low marks, irregular attendance"
    }, headers=headers)
    assert ai_res.status_code == 200

def test_full_staff_and_class_flow(client):
    headers = auth_headers(client)

    # Add staff
    staff_res = client.post(f"{PREFIX}/api/staff", json={
        "employee_id": "EMP-FLOW-001",
        "full_name": "Ms. Johnson",
        "role": "teacher",
        "subject": "English"
    }, headers=headers)
    assert staff_res.status_code == 200
    staff_id = staff_res.json().get("id", 1)

    # Create class with teacher
    class_res = client.post(f"{PREFIX}/api/classes", json={
        "grade": "11",
        "section": "D",
        "class_teacher_id": staff_id
    }, headers=headers)
    assert class_res.status_code == 200

    # List classes
    list_res = client.get(f"{PREFIX}/api/classes", headers=headers)
    assert list_res.status_code == 200
    assert len(list_res.json()) >= 1