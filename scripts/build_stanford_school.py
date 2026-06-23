"""Build a focused Stanford International School app using the App Factory."""
from __future__ import annotations

import json
import sys
import time

from shared.agent_tools import _run_app_factory_build_app

SPEC = {
    "app_name": "stanford_school",
    "title": "Stanford International School",
    "prefix": "/school",
    "roles": [
        {"username": "admin", "role": "admin", "password": "admin123"},
        {"username": "principal", "role": "principal", "password": "principal123"},
        {"username": "teacher", "role": "teacher", "password": "teacher123"},
        {"username": "student", "role": "student", "password": "student123"},
    ],
    "entities": [
        {"name": "student", "fields": [
            {"name": "admission_number", "type": "text", "required": True},
            {"name": "full_name", "type": "text", "required": True},
            {"name": "grade", "type": "text", "required": True},
            {"name": "section", "type": "text", "required": True},
            {"name": "parent_phone", "type": "text", "required": False},
        ]},
        {"name": "staff", "fields": [
            {"name": "employee_id", "type": "text", "required": True},
            {"name": "full_name", "type": "text", "required": True},
            {"name": "role", "type": "text", "required": True},
            {"name": "subject", "type": "text", "required": False},
        ]},
        {"name": "class", "fields": [
            {"name": "grade", "type": "text", "required": True},
            {"name": "section", "type": "text", "required": True},
            {"name": "class_teacher_id", "type": "integer", "required": False},
        ]},
        {"name": "attendance", "fields": [
            {"name": "student_id", "type": "integer", "required": True},
            {"name": "date", "type": "text", "required": True},
            {"name": "status", "type": "text", "required": True},
        ]},
        {"name": "exam", "fields": [
            {"name": "name", "type": "text", "required": True},
            {"name": "subject", "type": "text", "required": True},
            {"name": "grade", "type": "text", "required": True},
            {"name": "max_marks", "type": "number", "required": True},
        ]},
        {"name": "result", "fields": [
            {"name": "student_id", "type": "integer", "required": True},
            {"name": "exam_id", "type": "integer", "required": True},
            {"name": "marks", "type": "number", "required": True},
        ]},
        {"name": "fee", "fields": [
            {"name": "student_id", "type": "integer", "required": True},
            {"name": "amount", "type": "number", "required": True},
            {"name": "term", "type": "text", "required": True},
            {"name": "paid", "type": "number", "required": False},
        ]},
    ],
    "routes": [
        {"path": "/api/students", "method": "POST", "purpose": "admit a student", "required_fields": ["admission_number", "full_name", "grade", "section"]},
        {"path": "/api/students", "method": "GET", "purpose": "list students"},
        {"path": "/api/students/{id}", "method": "GET", "purpose": "get student details"},
        {"path": "/api/staff", "method": "POST", "purpose": "add staff", "required_fields": ["employee_id", "full_name", "role"]},
        {"path": "/api/staff", "method": "GET", "purpose": "list staff"},
        {"path": "/api/classes", "method": "POST", "purpose": "create class", "required_fields": ["grade", "section"]},
        {"path": "/api/classes", "method": "GET", "purpose": "list classes"},
        {"path": "/api/attendance", "method": "POST", "purpose": "mark attendance", "required_fields": ["student_id", "date", "status"]},
        {"path": "/api/attendance", "method": "GET", "purpose": "get attendance"},
        {"path": "/api/exams", "method": "POST", "purpose": "create exam", "required_fields": ["name", "subject", "grade", "max_marks"]},
        {"path": "/api/exams", "method": "GET", "purpose": "list exams"},
        {"path": "/api/results", "method": "POST", "purpose": "record result", "required_fields": ["student_id", "exam_id", "marks"]},
        {"path": "/api/results", "method": "GET", "purpose": "list results"},
        {"path": "/api/fees", "method": "POST", "purpose": "add fee record", "required_fields": ["student_id", "amount", "term"]},
        {"path": "/api/fees", "method": "GET", "purpose": "list fees"},
        {"path": "/api/ai/suggest", "method": "POST", "purpose": "AI suggestions for at-risk students"},
    ],
    "ui_tabs": ["Dashboard", "Students", "Staff", "Classes", "Attendance", "Exams", "Results", "Fees"],
    "ai_endpoints": ["suggest_intervention"],
    "tests": [
        "admit a student and retrieve by id",
        "mark attendance and verify report",
        "create exam, record result, list results",
        "add fee record and check balance",
    ],
    "notes": "Role-based dashboards for admin, principal, teacher and student. Keep the generated code compact and avoid extra optional modules.",
}


def main() -> None:
    print(f"[{time.strftime('%H:%M:%S')}] Building Stanford International School app...", flush=True)
    start = time.time()
    result = _run_app_factory_build_app({"spec": SPEC})
    elapsed = time.time() - start
    print(f"[{time.strftime('%H:%M:%S')}] Done in {elapsed:.1f}s", flush=True)
    print("ok:", result.get("ok"), flush=True)
    print("files_written:", result.get("files_written"), flush=True)
    print("files_failed:", result.get("files_failed"), flush=True)
    test = result.get("test_result") or {}
    print("test ok:", test.get("ok"), "returncode:", test.get("returncode"), flush=True)
    if test.get("stdout"):
        print("stdout tail:\n", test["stdout"][-2000:], flush=True)
    if test.get("stderr"):
        print("stderr tail:\n", test["stderr"][-1000:], flush=True)
    if result.get("error"):
        print("error:", result.get("error"), flush=True)
    with open("scripts/stanford_school_build.json", "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()
