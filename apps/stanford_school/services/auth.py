from ..database import get_db
import hashlib
import time

USERS = {
    "admin": {"username": "admin", "role": "admin", "password": "admin123"},
    "principal": {"username": "principal", "role": "principal", "password": "principal123"},
    "teacher": {"username": "teacher", "role": "teacher", "password": "teacher123"},
    "student": {"username": "student", "role": "student", "password": "student123"},
}

_sessions: dict = {}


def _hash(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


def login(username: str, password: str) -> dict:
    user = USERS.get(username)
    if not user or user["password"] != password:
        return {"success": False, "error": "Invalid credentials"}
    token = hashlib.sha256(f"{username}{time.time()}".encode()).hexdigest()
    _sessions[token] = {"username": username, "role": user["role"]}
    return {"success": True, "token": token, "username": username, "role": user["role"]}


def logout(token: str) -> dict:
    _sessions.pop(token, None)
    return {"success": True}


def get_current_user(token: str) -> dict | None:
    return _sessions.get(token)


def require_roles(token: str, roles: list[str]) -> dict | None:
    user = get_current_user(token)
    if user and user["role"] in roles:
        return user
    return None


def get_dashboard_data(role: str) -> dict:
    with get_db() as conn:
        students_count = conn.execute("SELECT COUNT(*) FROM student").fetchone()[0]
        staff_count = conn.execute("SELECT COUNT(*) FROM staff").fetchone()[0]
        classes_count = conn.execute("SELECT COUNT(*) FROM class").fetchone()[0]
        exams_count = conn.execute("SELECT COUNT(*) FROM exam").fetchone()[0]
        results_count = conn.execute("SELECT COUNT(*) FROM result").fetchone()[0]
        fees_count = conn.execute("SELECT COUNT(*) FROM fee").fetchone()[0]

        today = time.strftime("%Y-%m-%d")
        attendance_today = conn.execute(
            "SELECT COUNT(*) FROM attendance WHERE date=?", (today,)
        ).fetchone()[0]
        present_today = conn.execute(
            "SELECT COUNT(*) FROM attendance WHERE date=? AND status='present'", (today,)
        ).fetchone()[0]

    base = {
        "students_count": students_count,
        "staff_count": staff_count,
        "classes_count": classes_count,
        "exams_count": exams_count,
        "results_count": results_count,
        "fees_count": fees_count,
        "attendance_today": attendance_today,
        "present_today": present_today,
    }

    if role == "admin":
        return {**base, "view": "admin"}
    elif role == "principal":
        return {**base, "view": "principal"}
    elif role == "teacher":
        return {
            "students_count": students_count,
            "attendance_today": attendance_today,
            "present_today": present_today,
            "exams_count": exams_count,
            "view": "teacher",
        }
    elif role == "student":
        return {
            "exams_count": exams_count,
            "results_count": results_count,
            "view": "student",
        }
    return base